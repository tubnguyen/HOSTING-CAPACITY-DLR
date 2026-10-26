"""Battery storage dispatch, state of charge and reserve accounting.

Sign convention, stated once because getting it wrong is silent:

    plant frame   pandapower storage.p_mw: charging positive, discharging
                  negative. State of charge integrates on this frame.
    grid frame    discharging positive, charging negative, matching the export
                  sign used everywhere else.

Both are written to every row so a sign error is auditable rather than hidden.

Where it connects (Config.storage_at, TAP_B by default) decides what it can
reach. Its power flows through every corridor segment between its point and
the PCC, and through none upstream of it. Charging relieves an export overload
on those segments and discharging adds to it; on segments upstream of the
battery it can do neither, and only curtailment helps.

Dispatch runs in three phases. Phase one sets an intent before the network is
solved; phase two reconciles that intent against the export headroom the
solved network actually leaves. Phase three, before any plant is curtailed,
lets the battery relieve what binds within its reach: generation has priority,
so a discharge that loads a congested segment or the export cap is held back
first, and an idle or charging battery then charges with power that would
otherwise be curtailed (Config.storage_relieves_congestion).
"""
from __future__ import annotations

from . import constants as C
from .constraints import measure_pcc_export, scan
from .controls import solve
from .network import SEGMENT_ENDS

RELIEF_RESOLUTION_MW = 0.5      # bracket width the relief search stops at
RELIEF_MAX_TRIALS = 14


def to_grid_frame(p_plant_mw: float) -> float:
    return -p_plant_mw


def dischargeable_mw(cfg, soc_mwh: float) -> float:
    """Power deliverable for one step without breaching the physical floor."""
    usable = max(0.0, soc_mwh - cfg.soc_min_mwh)
    return min(cfg.storage_p_mw, usable * C.BESS_ETA_DIS / C.DT_H)


def chargeable_mw(cfg, soc_mwh: float) -> float:
    """Power absorbable for one step without breaching the physical ceiling."""
    headroom = max(0.0, cfg.soc_max_mwh - soc_mwh)
    return min(cfg.storage_p_mw, headroom / C.BESS_ETA_CH / C.DT_H)


def _static_q(cfg, p_plant_mw: float) -> float:
    """Reactive setpoint for the non-droop modes."""
    if cfg.storage_q_mode == "fixed":
        return -0.5 * cfg.q_limit_storage
    if cfg.storage_q_mode == "cosphi":
        return cfg.cosphi_sign_factor * p_plant_mw * C.TAN_PHI
    return 0.0


def phase1_intent(net, cfg, idx, activation_mw: float, soc_mwh: float) -> dict:
    """Provisional setpoint, set before the network is solved.

    Discharge fires when the balancing signal clears the trigger and the state
    of charge is above the reserve floor, which keeps the contracted upward
    reserve deliverable instead of letting energy delivery drain it.
    """
    if not cfg.storage_enabled:
        net.storage.at[idx.storage, "p_mw"] = 0.0
        net.storage.at[idx.storage, "q_mvar"] = 0.0
        return {"mode": "disabled", "intent_mw": 0.0}

    if activation_mw > C.BESS_ACTIVATION_TRIGGER_MW and soc_mwh > cfg.soc_reserve_mwh:
        intent = min(cfg.storage_contract_mw, dischargeable_mw(cfg, soc_mwh))
        net.storage.at[idx.storage, "p_mw"] = -intent
        mode = "discharge"
    else:
        intent = 0.0
        net.storage.at[idx.storage, "p_mw"] = 0.0    # charging is resolved in phase two
        mode = "idle"

    net.storage.at[idx.storage, "q_mvar"] = (
        0.0 if cfg.storage_q_mode == "droop"
        else _static_q(cfg, float(net.storage.at[idx.storage, "p_mw"])))
    return {"mode": mode, "intent_mw": intent}


def phase2_reconcile(net, cfg, idx, intent: dict, soc_mwh: float) -> dict:
    """Clamp the discharge to the export headroom, or resolve the charge split.

    The export a solved network would show with the battery idle is obtained
    algebraically rather than by a second power flow: the battery sits one
    short tie-line behind the PCC, so its marginal contribution to export is
    within a fraction of a percent of one to one.
    """
    if not cfg.storage_enabled:
        return {"mode": "disabled", "p_plant_mw": 0.0, "headroom_clamped": 0,
                "shortfall_mw": 0.0, "charge_surplus_mw": 0.0, "charge_grid_mw": 0.0}

    p_plant = float(net.storage.at[idx.storage, "p_mw"])
    export_now = measure_pcc_export(net, idx.buses)["p_mw"]
    export_idle = export_now - to_grid_frame(p_plant)
    headroom = cfg.export_cap_mw - export_idle

    mode = intent["mode"]
    clamped = 0
    shortfall = charge_surplus = charge_grid = 0.0

    if mode == "discharge":
        wanted = intent["intent_mw"]
        if wanted > headroom and not cfg.storage_may_curtail_der:
            # Generation has priority: the battery delivers less than contracted
            # and the shortfall is recorded rather than absorbed.
            delivered = max(0.0, headroom)
            clamped = 1
            shortfall = wanted - delivered
        else:
            delivered = wanted
        net.storage.at[idx.storage, "p_mw"] = -delivered
    else:
        surplus = max(0.0, export_idle - cfg.export_cap_mw)
        capacity = chargeable_mw(cfg, soc_mwh)
        charge_surplus = min(surplus, capacity)
        if cfg.storage_charge_source == "grid_only":
            charge_surplus, charge_grid = 0.0, capacity
        elif (cfg.storage_charge_source == "surplus_then_grid"
              and soc_mwh < cfg.soc_recharge_target_mwh):
            charge_grid = max(0.0, capacity - charge_surplus)
        total_charge = charge_surplus + charge_grid
        if total_charge > 0.005:
            mode = ("charge_mixed" if charge_surplus > 0.005 and charge_grid > 0.005
                    else "charge_surplus" if charge_surplus > 0.005 else "charge_grid")
        net.storage.at[idx.storage, "p_mw"] = total_charge

    if cfg.storage_q_mode != "droop":
        net.storage.at[idx.storage, "q_mvar"] = _static_q(
            cfg, float(net.storage.at[idx.storage, "p_mw"]))

    return {"mode": mode, "p_plant_mw": float(net.storage.at[idx.storage, "p_mw"]),
            "headroom_clamped": clamped, "shortfall_mw": shortfall,
            "charge_surplus_mw": charge_surplus, "charge_grid_mw": charge_grid}


def _within_reach(cfg, detail: dict) -> bool:
    """Whether what binds includes something the battery's power flows through.

    The export cap always does. A corridor segment does when it carries power
    toward the PCC and lies between the battery's point and the PCC.
    """
    if detail["levels"][4] is not None:
        return True
    here = C.CORRIDOR_POSITION_KM[cfg.storage_bus]
    return any(name in SEGMENT_ENDS and C.CORRIDOR_POSITION_KM[SEGMENT_ENDS[name][0]] >= here
               for name in detail.get("corridor_over", []))


def relieve_congestion(net, cfg, idx, limits_a, soc_mwh: float, phase: dict) -> dict:
    """Phase three: the smallest move of the battery that clears what it can reach.

    Raises the plant-frame power - less discharge, or more charge - just far
    enough to clear the export cap and every overloaded segment between the
    battery and the PCC, or as far as it can go. A discharge is only held
    back, never turned into a charge, and only when generation has priority
    (storage_may_curtail_der off). Charging is limited by the state of charge
    and taken only when the battery may charge from surplus. What it leaves
    binding is curtailment's. `phase` is updated to the power applied.
    """
    out = {"withheld_mw": 0.0, "captured_mw": 0.0, "trials": 0}
    level, _, _, detail = scan(net, idx, limits_a, cfg)
    if level == 0 or not _within_reach(cfg, detail):
        return out
    p0 = float(net.storage.at[idx.storage, "p_mw"])
    if phase["mode"] == "discharge":
        room = -p0 if not cfg.storage_may_curtail_der else 0.0
    elif cfg.storage_charge_source != "grid_only":
        room = max(0.0, chargeable_mw(cfg, soc_mwh) - max(p0, 0.0))
    else:
        room = 0.0
    if room <= 0.01:
        return out

    def apply(step_mw: float) -> bool:
        net.storage.at[idx.storage, "p_mw"] = p0 + step_mw
        if cfg.storage_q_mode != "droop":
            net.storage.at[idx.storage, "q_mvar"] = _static_q(cfg, p0 + step_mw)
        out["trials"] += 1
        return solve(net, "results")

    def cleared() -> bool:
        level, _, _, detail = scan(net, idx, limits_a, cfg)
        return level == 0 or not _within_reach(cfg, detail)

    lo, hi = 0.0, room
    if apply(room) and cleared():
        while hi - lo > RELIEF_RESOLUTION_MW and out["trials"] < RELIEF_MAX_TRIALS:
            mid = 0.5 * (lo + hi)
            if apply(mid) and cleared():
                hi = mid
            else:
                lo = mid
    if not apply(hi):
        apply(0.0)
        return out

    moved = hi
    if phase["mode"] == "discharge":
        out["withheld_mw"] = moved
        phase["shortfall_mw"] = phase.get("shortfall_mw", 0.0) + moved
        phase["headroom_clamped"] = 1
    else:
        out["captured_mw"] = moved
        phase["charge_surplus_mw"] = phase.get("charge_surplus_mw", 0.0) + moved
        phase["mode"] = "charge_mixed" if phase.get("charge_grid_mw", 0.0) > 0.005 \
            else "charge_surplus"
    phase["p_plant_mw"] = p0 + moved
    return out


def update_soc(cfg, soc_mwh: float, p_plant_mw: float, apply: bool):
    """Integrate the state of charge on realised power only.

    On a step that did not converge, nothing is integrated: no energy
    bookkeeping is possible for power that was never delivered. When a limit
    clamps the result, the realised power is backed out of the actual state of
    charge change so the two stay consistent.
    """
    if not apply:
        return soc_mwh, 0, p_plant_mw
    delta = (C.BESS_ETA_CH * p_plant_mw * C.DT_H if p_plant_mw > 0
             else p_plant_mw / C.BESS_ETA_DIS * C.DT_H)
    new = soc_mwh + delta
    if new > cfg.soc_max_mwh:
        realised = (cfg.soc_max_mwh - soc_mwh) / (C.BESS_ETA_CH * C.DT_H)
        return cfg.soc_max_mwh, 1, realised
    if new < cfg.soc_min_mwh:
        realised = (cfg.soc_min_mwh - soc_mwh) * C.BESS_ETA_DIS / C.DT_H
        return cfg.soc_min_mwh, 1, realised
    return new, 0, p_plant_mw


def reserve_availability(cfg, soc_mwh: float, p_plant_mw: float):
    """Upward reserve still deliverable after this step, and whether it is short.

    A shortfall surfaces the conflict between holding a reserve and using the
    same asset to deliver energy, as a counted per-step result.
    """
    if not cfg.storage_enabled:
        return 0.0, 0
    discharging = max(0.0, -p_plant_mw)
    available = min(cfg.storage_reserve_mw,
                    dischargeable_mw(cfg, soc_mwh),
                    cfg.storage_p_mw - discharging)
    return available, int(available < cfg.storage_reserve_mw - 1e-9)
