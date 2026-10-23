"""Minimal pro-rata curtailment.

Curtailment is parametrised by one scalar: the total megawatts removed,
allocated pro rata over the entry dispatch. Every actionable constraint is
relieved monotonically by injecting less, so the search brackets the
feasibility boundary and then interpolates on the signed margin, and applies
the smallest cut that clears the violation.

Marching down in fixed steps and keeping whatever was taken would answer "the
first grid point past the boundary" rather than the boundary itself, which
overstates the curtailment a constraint actually requires.

Two properties the search depends on:

* each trial evaluates the same state that will be applied, droop re-settle
  included, so the search and the reported result are the same function;
* each trial restarts from the entry reactive power, so a trial is a pure
  function of the cut and the bracket stays meaningful.

Every plant that is cut in a step gives up the same share of what it is
producing at that moment: one fraction, applied to each plant's
instantaneous output, so a plant producing twice as much is cut twice as
many megawatts. That share is recorded per step as `curtail_fraction`.

Which plants share it depends on what binds. An over-voltage, the export cap
or an overloaded transformer is shared by every connected plant, and so is
any step in which one of them binds. A corridor overload alone is shared by
the plants whose power flows through the overloaded segment, those joining
at or upstream of its head-side end: a plant connected further along, at the
PCC for one, puts no current through that segment and is not cut for it.
With the four built-in plants the segment that binds carries all of them.

The cut is searched on the solved network, so a large load is accounted for
before anything is curtailed: what the load draws locally never reaches the
congested segment, and only the excess beyond what the network can carry
and the load can use is cut. A segment overloaded by power drawn toward the
head, to supply a large load up the corridor, is not curtailed for at all:
generation is what relieves it, not what causes it.
"""
from __future__ import annotations

from . import constants as C
from .constraints import (
    cause_class,
    classify_residual,
    drawn_overload,
    feasibility_margin,
    format_levels,
    scan,
    worst_corridor,
)
from .controls import droop_reference, measured_voltage, solve
from .network import SEGMENT_ENDS

CURTAILABLE = ("PV_1", "WF_2", "WF_1", "WF_3")      # the built-in plants
MAX_TRIALS = 40
RESOLUTION_MW = 0.5             # bracket width the search stops at
SETTLE_ITERS = 8


def curtailable(cfg) -> tuple:
    """Every plant curtailment may act on: the built-in four, then any added."""
    return CURTAILABLE + tuple(p.name for p in cfg.added_der)


def upstream_of_overload(cfg, detail: dict) -> tuple | None:
    """The plants that put current through the overloaded corridor segments.

    Those joining at or upstream of the head-side end of the furthest
    overloaded segment. None when no export path segment is overloaded, or
    when a line beyond the PCC is, which has no upstream to speak of.
    """
    over = detail.get("corridor_over", [])
    if not over or any(name not in SEGMENT_ENDS for name in over):
        return None
    reach = max(C.CORRIDOR_POSITION_KM[SEGMENT_ENDS[name][0]] for name in over)
    joins = cfg.unit_bus
    return tuple(u for u in curtailable(cfg) if C.CORRIDOR_POSITION_KM[joins[u]] <= reach)


def relieving_units(cfg, detail: dict) -> tuple:
    """The plants whose curtailment relieves the actionable violations in `detail`.

    Everyone for an over-voltage, the export cap or a transformer; for
    corridor overloads alone, the plants upstream of them.
    """
    levels, groups = detail["levels"], detail["groups"]
    shared = (levels[1] is not None or levels[4] is not None
              or groups["dso_trafo"][1] > 100.0 or groups["plant"][1] > 100.0)
    upstream = None if shared else upstream_of_overload(cfg, detail)
    return curtailable(cfg) if upstream is None else upstream


def default_record(cfg) -> dict:
    return {
        "curtailment_enabled": cfg.curtailment,
        **{f"curtailed_{unit}_mw": 0.0 for unit in cfg.units},
        "curtailed_total_mw": 0.0, "curtail_fraction": 0.0,
        "binding_before": "none", "binding_after": "none",
        "binding_all_before": "none", "binding_all_after": "none",
        "curtail_cause": "none", "curtail_exit": "not_needed",
        "curtail_trials": 0, "curtail_residual_mw": 0.0,
        "curtail_failures": 0, "feasible": True,
        "margin_after": 0.0, "residual_class": "none", "ok": True,
        "worst_corridor_pct": 0.0, "worst_corridor_elem": "none",
        "worst_dso_trafo_pct": 0.0, "worst_dso_trafo_elem": "none",
        "worst_plant_pct": 0.0, "worst_plant_elem": "none",
    }


def _record_scan(record: dict, detail: dict, when: str) -> None:
    record[f"binding_all_{when}"] = format_levels(detail)
    if when == "after":
        for group, prefix in (("corridor", "worst_corridor"),
                              ("dso_trafo", "worst_dso_trafo"),
                              ("plant", "worst_plant")):
            element, pct = (worst_corridor(detail) if group == "corridor"
                            else detail["groups"][group])
            record[f"{prefix}_elem"], record[f"{prefix}_pct"] = element, pct


def settle_droop(net, cfg, idx, units) -> bool:
    """Re-settle plant reactive power after active power has been cut.

    Curtailment moves active power by tens of megawatts, so the droop
    reference genuinely moves with it and a single damped step does not reach
    it. Most steps sit inside the droop deadband, so this usually exits after
    one pass without a solve.
    """
    damping = dict.fromkeys(units, C.DROOP_Q_DAMP)
    last = dict.fromkeys(units, 0)
    rating, q_limit = cfg.unit_rating_mw, cfg.unit_q_limit_mvar
    for _ in range(SETTLE_ITERS):
        moved = False
        for unit in units:
            s_idx = idx.sgens[unit]
            p_now = float(net.sgen.at[s_idx, "p_mw"])
            q_now = float(net.sgen.at[s_idx, "q_mvar"])
            if cfg.unit_mode(unit) != "droop":
                q_new = cfg.cosphi_sign_factor * p_now * C.TAN_PHI
                if abs(q_new - q_now) > C.DROOP_Q_STEP_TOL_MVAR:
                    moved = True
                net.sgen.at[s_idx, "q_mvar"] = q_new
                continue
            v = measured_voltage(net, idx, cfg, unit)
            q_ref = droop_reference(v, p_now, q_limit[unit], rating[unit],
                                    cfg.droop_p_min_frac)
            delta = q_ref - q_now
            direction = (delta > 0) - (delta < 0)
            if direction and last[unit] and direction != last[unit]:
                damping[unit] = max(C.DROOP_Q_DAMP_MIN, damping[unit] * 0.5)
            if direction:
                last[unit] = direction
            q_new = q_now + damping[unit] * delta
            if abs(q_new - q_now) > C.DROOP_Q_STEP_TOL_MVAR:
                moved = True
            net.sgen.at[s_idx, "q_mvar"] = q_new
        if not moved:
            return True
        if not solve(net, "results"):
            return False
    return True


def uncurtailed_record(net, cfg, idx, limits_a) -> dict:
    """What binds when curtailment is switched off, recorded rather than blank."""
    record = default_record(cfg)
    level, element, magnitude, detail = scan(net, idx, limits_a, cfg)
    tag = f"L{level}:{element}" if level else "none"
    record.update({
        "binding_before": tag, "binding_after": tag,
        "margin_after": magnitude,
        "curtail_cause": cause_class(level, element, detail),
        "curtail_exit": "disabled",
        "feasible": level == 0,
    })
    _record_scan(record, detail, "before")
    _record_scan(record, detail, "after")
    return record


def _search(net, cfg, idx, limits_a, units, plants, base_q, detail, record) -> dict:
    """The smallest pro-rata cut over `units` that clears every actionable level.

    Each trial cuts every unit by the same fraction of the output it entered
    with and puts every plant back on its entry reactive power, so a trial is
    a pure function of the cut. Leaves the network at the cut found.
    """
    base_p = {u: float(net.sgen.at[idx.sgens[u], "p_mw"]) for u in units}
    total_p = sum(base_p.values())
    out = {"cut": dict.fromkeys(units, 0.0), "fraction": 0.0, "trials": 0,
           "exit": "nothing_to_curtail", "residual": 0.0, "result": None,
           "failed": False, "fleet_off": True}
    if total_p <= 0.01:
        return out                  # a constraint binds with nothing left to cut

    trials = 0

    def attempt(cut_mw: float):
        """Apply a pro-rata cut, re-settle the droop, and re-scan."""
        nonlocal trials
        fraction = min(1.0, cut_mw / total_p) if total_p > 0 else 0.0
        for unit in units:
            net.sgen.at[idx.sgens[unit], "p_mw"] = base_p[unit] * (1.0 - fraction)
        for unit in plants:
            net.sgen.at[idx.sgens[unit], "q_mvar"] = base_q[unit]
        trials += 1
        if not solve(net, "results"):
            record["curtail_failures"] += 1
            return None
        if fraction > 0.0 and not settle_droop(net, cfg, idx, plants):
            record["curtail_failures"] += 1
            return None
        return scan(net, idx, limits_a, cfg)

    # First guess from the linear structure of the violation: corridor current
    # and export both scale with injection, so the required cut is predictable
    # to within a factor rather than something to find by doubling from zero.
    margins = detail["margins"]
    guesses = []
    if margins[3] > 0:
        guesses.append(total_p * margins[3] / (100.0 + margins[3]))
    if margins[4] > 0:
        guesses.append(margins[4])
    if margins[1] > 0:
        guesses.append(total_p * 0.10)
    cut = min(total_p, max(RESOLUTION_MW, max(guesses) if guesses else 5.0))

    lo, g_lo = 0.0, feasibility_margin(detail)     # the entry state, violating
    hi, g_hi = None, None
    exit_reason = "trial_budget"

    while trials < MAX_TRIALS:
        result = attempt(cut)
        if result is None:
            # Treat a diverged trial as still violating: that pushes the search
            # toward more curtailment, which is the safe direction.
            lo, g_lo = cut, max(g_lo, 1.0)
        else:
            trial_level, _, _, trial_detail = result
            g = feasibility_margin(trial_detail)
            if trial_level == 0:
                hi, g_hi = cut, g
            else:
                lo, g_lo = cut, g

        if hi is None:
            if cut >= total_p:
                exit_reason = "nothing_to_curtail"
                break
            cut = min(total_p, cut * 2.0)
            continue

        if hi - lo <= RESOLUTION_MW:
            exit_reason = "cleared"
            break

        # Regula falsi with an Illinois-style guard: an interpolant landing too
        # close to either end is replaced by the midpoint, so the bracket always
        # shrinks and the search cannot stall.
        denominator = g_lo - g_hi
        span = hi - lo
        nxt = lo + span * (g_lo / denominator) if denominator > 1e-12 else lo + 0.5 * span
        if not (lo + 0.05 * span < nxt < hi - 0.05 * span):
            nxt = lo + 0.5 * span
        cut = nxt

    applied = hi if hi is not None else total_p
    result = attempt(applied)
    out["trials"] = trials
    if result is None:
        out["failed"] = True
        return out
    fraction = min(1.0, applied / total_p) if total_p > 0 else 0.0
    out.update({
        "cut": {u: base_p[u] * fraction for u in units},
        "fraction": fraction,
        "exit": exit_reason,
        "residual": (hi - lo) if hi is not None else 0.0,
        "result": result,
        "fleet_off": all(float(net.sgen.at[idx.sgens[u], "p_mw"]) < 0.01 for u in units),
    })
    return out


def curtail(net, cfg, idx, limits_a) -> dict:
    """Apply the smallest pro-rata cut that clears every actionable violation.

    One search over the plants that relieve what binds, all cut by the same
    share of their instantaneous output.
    """
    record = default_record(cfg)
    level, element, magnitude, detail = scan(net, idx, limits_a, cfg)
    record["binding_before"] = f"L{level}:{element}" if level else "none"
    record["curtail_cause"] = cause_class(level, element, detail)
    _record_scan(record, detail, "before")

    if level == 0:
        _record_scan(record, detail, "after")
        record["binding_after"] = record["binding_before"]
        if drawn_overload(detail):
            record.update({"curtail_exit": "load_driven", "feasible": False,
                           "residual_class": "drawn_by_load"})
        return record

    plants = curtailable(cfg)
    base_q = {u: float(net.sgen.at[idx.sgens[u], "q_mvar"]) for u in plants}
    units = relieving_units(cfg, detail)
    out = _search(net, cfg, idx, limits_a, units, plants, base_q, detail, record)
    if out["failed"]:
        record.update({"ok": False, "curtail_exit": "solve_failure", "feasible": False,
                       "curtail_trials": out["trials"]})
        return record
    if out["result"] is not None:
        level, element, magnitude, detail = out["result"]

    for unit, mw in out["cut"].items():
        record[f"curtailed_{unit}_mw"] = mw
    record.update({
        "curtail_trials": out["trials"],
        "curtail_exit": out["exit"],
        "curtail_residual_mw": out["residual"],
        "curtail_fraction": out["fraction"],
        "feasible": level == 0,
        "binding_after": f"L{level}:{element}" if level else "none",
        "margin_after": magnitude if level else 0.0,
        "curtailed_total_mw": sum(out["cut"].values()),
    })
    _record_scan(record, detail, "after")
    record["residual_class"] = classify_residual(level, record["feasible"], out["fleet_off"])
    if level == 0 and drawn_overload(detail):
        record["residual_class"] = "drawn_by_load"
    return record
