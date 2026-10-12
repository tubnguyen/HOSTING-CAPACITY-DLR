"""Run outputs: one summary CSV of titled tables, per-step results and metrics.

The summary leads with how the network behaved: the current the plants push
into the corridor against what each limit allows, the power flow along the
corridor, generation by plant, the exchange at the grid interface, voltages
and transformer loading. The technical cost - curtailed energy, losses and
reactive exchange outside the window - follows, in energy units and in euros
at indicative prices.

With a static reference (reference.py, `--compare`) the headline and cost
tables also give the static run, the change in the quantity's own unit and
the change in per cent.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import constants as C
from .config import to_record
from .network import CORRIDOR_ZONES, DSO_TRAFO_NAMES, EXPORT_PATH_LINES, PLANT_TRAFOS
from .reference import static_key
from .simulate import DER_UNITS, realised_generation

RATING_METHODS = {0: "Static rating", 1: "Ambient-adjusted DLR", 2: "Full-weather DLR"}

# The single and twin builds' static ratings are the references every summary
# measures the corridor current against: what the line carries today, and what
# reinforcing it would.
SINGLE = C.CONDUCTOR_OPTIONS["single"]
TWIN = C.CONDUCTOR_OPTIONS["twin"]
SINGLE_STATIC_A = SINGLE["max_i_ka"] * 1000.0
TWIN_STATIC_A = TWIN["max_i_ka"] * 1000.0

# Export path segments, head to grid interface, with their length.
SEGMENTS = {
    "CORR_A_PV": ("SUB_A to TAP_PV", C.LEN_CORR_A_PV),
    "CORR_PV_W": ("TAP_PV to TAP_W", C.LEN_CORR_PV_W),
    "CORR_W_C": ("TAP_W to SUB_C", C.LEN_CORR_W_C),
    "CORR_C_B": ("SUB_C to TAP_B", C.LEN_CORR_C_B),
    "CORR_B_PCC": ("TAP_B to PCC", C.LEN_CORR_B_PCC),
}
ZONE_OF = {line: zone for zone, lines in CORRIDOR_ZONES.items() for line in lines}
CORRIDOR_BUSES = ["SUB_A", "TAP_PV", "TAP_W", "SUB_C", "TAP_B", "PCC"]
VOLTAGE_BUSES = CORRIDOR_BUSES + ["SUB_E", "SUB_B", "SUB_A_MV", "SUB_B_MV"]
TRANSFORMER_ROLES = {name: "distribution 110/20 kV" for name in DSO_TRAFO_NAMES}
TRANSFORMER_ROLES.update({name: ("solar step-up" if name == "T_PV"
                                 else "battery" if name == "T_BESS" else "wind step-up")
                          for name in PLANT_TRAFOS})

DURATION_POINTS_PCT = (0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)
SHORT_WINDOW_DAYS = 28            # below this the means are over the whole window

# Decimals per unit in the summary tables.
_DECIMALS = {"A": 0, "MW": 1, "MVAr": 1, "MWh": 1, "MVArh": 1, "h": 2, "%": 1,
             "% of time": 1, "°C": 1, "k€": 1, "per day": 1, "steps": 0, "cycles": 1,
             "pu": 3, "km": 0}


def _energy_mwh(series: pd.Series) -> float:
    return float(series.sum() * C.DT_H)


def corridor_series(frame: pd.DataFrame, template: str, how: str) -> pd.Series:
    """Combine the per-zone columns named by `template` into one corridor series.

    The export path is one series thermal path: its rating is the lower zone's
    and its current the higher zone's.
    """
    zones = pd.concat([frame[template.format(zone=z)] for z in CORRIDOR_ZONES], axis=1)
    return zones.max(axis=1) if how == "max" else zones.min(axis=1)


def pushed_current(frame: pd.DataFrame) -> pd.Series | None:
    """Corridor current with every plant at its full available output [A].

    Read before curtailment, so it is the current the generation would push
    through the corridor if nothing held it back. None for a result saved
    before this was recorded.
    """
    columns = [f"i_{z}_uncurtailed_a" for z in CORRIDOR_ZONES]
    if not all(c in frame.columns for c in columns):
        return None
    return corridor_series(frame, "i_{zone}_uncurtailed_a", "max")


def governing_binding(frame: pd.DataFrame) -> np.ndarray:
    """What set the corridor rating at each step: weather, cap, equipment or static."""
    ratings = pd.concat([frame[f"rating_{z}_a"] for z in CORRIDOR_ZONES], axis=1).to_numpy()
    labels = pd.concat([frame[f"rating_{z}_binding"] for z in CORRIDOR_ZONES], axis=1).to_numpy()
    return labels[np.arange(len(frame)), np.argmin(ratings, axis=1)]


def _share(mask) -> float:
    mask = np.asarray(mask, dtype=bool)
    return 100.0 * float(mask.mean()) if mask.size else float("nan")


def metrics(cfg, result: pd.DataFrame) -> dict:
    """Headline numbers for one run, used by the summary and the matrix table."""
    ok = result[result["converged"]]
    n = len(ok)
    if n == 0:
        return {"scenario": cfg.stem, "converged_steps": 0}

    available = _energy_mwh(ok["available_total_mw"])
    curtailed = _energy_mwh(ok["curtailed_total_mw"])
    delivered = _energy_mwh(realised_generation(ok))
    losses_line = _energy_mwh(ok["loss_line_mw"])
    losses_trafo = _energy_mwh(ok["loss_trafo_mw"])
    losses = losses_line + losses_trafo
    reactive_outside = _energy_mwh(ok["q_exceedance_mvar"])
    loading = corridor_series(ok, "loading_{zone}_pct", "max")
    rating = corridor_series(ok, "rating_{zone}_a", "min")
    current = corridor_series(ok, "i_{zone}_a", "max")
    pushed = pushed_current(ok)
    binding = governing_binding(ok)
    rating_mean = float(rating.mean())
    weather_cols = [f"rating_{z}_weather_a" for z in CORRIDOR_ZONES
                    if f"rating_{z}_weather_a" in ok.columns]
    weather_mean = (float(pd.concat([ok[c] for c in weather_cols], axis=1).min(axis=1).mean())
                    if weather_cols else float("nan"))
    t_cond = pd.concat([ok[f"t_cond_{z}_c"] for z in CORRIDOR_ZONES], axis=1)
    nan = float("nan")

    out = {
        "scenario": cfg.stem,
        "conductor": cfg.conductor,
        "dlr_mode": cfg.dlr_mode,
        "azimuth_z1_deg": cfg.azimuth_z1_deg,
        "azimuth_z2_deg": cfg.azimuth_z2_deg,
        "n_der": cfg.n_der,
        "fleet_mw": cfg.der_fleet_mw,
        "storage": int(cfg.storage_enabled),
        "steps": len(result),
        "converged_steps": n,
        "converged_pct": 100.0 * n / len(result),
        "reg_converged_pct": 100.0 * float(ok["reg_converged"].mean()),
        "available_mwh": available,
        "delivered_mwh": delivered,
        "curtailed_mwh": curtailed,
        "curtailed_pct": 100.0 * curtailed / available if available > 0 else 0.0,
        "net_export_mwh": _energy_mwh(ok["pcc_p_mw"]),
        "capacity_factor": delivered / (cfg.der_fleet_mw * n * C.DT_H) if cfg.der_fleet_mw else 0.0,
        "rating_mean_a": rating_mean,
        "rating_uplift_pct": 100.0 * (rating_mean / cfg.static_rating_a - 1.0),
        # What the conductor heat balance alone would have allowed, so the
        # uplift the weather offered and the uplift the scheme may use are
        # both on the record rather than only their difference.
        "rating_weather_mean_a": weather_mean,
        "rating_uplift_uncapped_pct": (100.0 * (weather_mean / cfg.static_rating_a - 1.0)
                                       if weather_mean == weather_mean else 0.0),
        "rating_cap_a": cfg.rating_cap_a if cfg.rating_cap_a is not None else nan,
        "rating_equipment_a": (cfg.equipment_rating_a if cfg.equipment_rating_a is not None
                               else nan),
        "hours_headroom_limited": float(ok["rating_headroom_limited"].sum() * C.DT_H),
        "headroom_limited_pct": 100.0 * float(ok["rating_headroom_limited"].mean()),
        # Share of steps each limit set the corridor rating; static runs are
        # set by none of the three.
        "rating_by_weather_pct": _share(binding == "weather"),
        "rating_by_cap_pct": _share(binding == "cap"),
        "rating_by_equipment_pct": _share(binding == "equipment"),
        "corridor_current_mean_a": float(current.mean()),
        "corridor_current_max_a": float(current.max()),
        # What the plants push into the corridor before any curtailment,
        # against a single conductor's static rating, against the operative
        # rating of this run and against the twin-bundle reinforcement.
        "pushed_current_mean_a": float(pushed.mean()) if pushed is not None else nan,
        "pushed_current_max_a": float(pushed.max()) if pushed is not None else nan,
        "hours_pushed_above_single_static": (float((pushed > SINGLE_STATIC_A).sum() * C.DT_H)
                                             if pushed is not None else nan),
        "pushed_above_single_static_pct": (_share(pushed > SINGLE_STATIC_A)
                                           if pushed is not None else nan),
        "pushed_above_rating_pct": _share(pushed > rating) if pushed is not None else nan,
        "pushed_above_twin_static_pct": (_share(pushed > TWIN_STATIC_A)
                                         if pushed is not None else nan),
        "corridor_loading_mean_pct": float(loading.mean()),
        "corridor_loading_p95_pct": float(loading.quantile(0.95)),
        "hours_corridor_over_limit": float((loading > 100.0).sum() * C.DT_H),
        # Counted per interval, not per zone. Summing the zones would charge a
        # quarter-hour twice for a step in which both zones ran hot, which is
        # one interval of exposure, not two.
        "hours_over_temperature": float(
            (t_cond > cfg.t_cond_max_c + C.T_COND_TOLERANCE_C).any(axis=1).sum() * C.DT_H),
        "t_cond_max_c": float(t_cond.max().max()),
        "pcc_peak_mw": float(ok["pcc_p_mw"].max()),
        "hours_over_export_cap": float((ok["pcc_p_mw"] > cfg.export_cap_mw).sum() * C.DT_H),
        "hours_voltage_high": float(ok["viol_L1"].sum() * C.DT_H),
        "hours_voltage_low": float(ok["viol_L2"].sum() * C.DT_H),
        "losses_mwh": losses,
        "losses_line_mwh": losses_line,
        "losses_trafo_mwh": losses_trafo,
        "reactive_exchange_mvarh": _energy_mwh(ok["pcc_q_mvar"].abs()),
        "reactive_outside_window_mvarh": reactive_outside,
        "hours_q_outside_window": float((1 - ok["q_within_window"]).sum() * C.DT_H),
        "cost_curtailed_keur": curtailed * cfg.energy_price_eur_mwh / 1000.0,
        "cost_losses_keur": losses * cfg.energy_price_eur_mwh / 1000.0,
        "cost_reactive_keur": reactive_outside * cfg.reactive_price_eur_mvarh / 1000.0,
        "oltc_operations": int(ok["oltc_operations"].sum()),
        "reactor_operations": int(ok["reactor_operations"].sum()),
        "oltc_loop_moves": int(ok["oltc_loop_moves"].sum()),
        "reactor_loop_moves": int(ok["reactor_loop_moves"].sum()),
        "q_tracking_error_max_mvar": float(ok["q_tracking_error_mvar"].max()),
        "runtime_s": float(result.attrs.get("runtime_s", nan)),
    }
    out["technical_cost_keur"] = (out["cost_curtailed_keur"] + out["cost_losses_keur"]
                                  + out["cost_reactive_keur"])

    for cause in ("corridor", "dso_trafo", "plant", "overvoltage", "export_cap"):
        mask = ok["curtail_cause"] == cause
        out[f"curtailed_{cause}_mwh"] = _energy_mwh(ok.loc[mask, "curtailed_total_mw"])

    if cfg.storage_enabled:
        out.update({
            "storage_discharged_mwh": _energy_mwh(ok["storage_p_grid_mw"].clip(lower=0)),
            "storage_charged_mwh": _energy_mwh((-ok["storage_p_grid_mw"]).clip(lower=0)),
            "storage_cycles": _energy_mwh(ok["storage_p_grid_mw"].clip(lower=0)) / cfg.storage_e_mwh,
            "storage_shortfall_mwh": _energy_mwh(ok["storage_shortfall_mw"].fillna(0)),
            "hours_reserve_short": float(ok["storage_reserve_short"].sum() * C.DT_H),
            "storage_soc_mean_pct": float(ok["storage_soc_pct"].mean()),
        })
    return out


def violations(result: pd.DataFrame) -> pd.DataFrame:
    """Every step where any level was violated after remediation."""
    ok = result[result["converged"]]
    mask = ok[["viol_L1", "viol_L2", "viol_L3", "viol_L4"]].any(axis=1)
    cols = ["binding_all_after", "curtail_cause", "curtailed_total_mw", "margin_after",
            "residual_class", "viol_L1", "viol_L2", "viol_L3", "viol_L4",
            "worst_corridor_pct", "worst_dso_trafo_pct", "worst_plant_pct",
            "pcc_p_mw", "available_total_mw"]
    return ok.loc[mask, [c for c in cols if c in ok.columns]]


# ── Means by period and duration points (shared with the figures) ───────────
def _utc_naive(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_convert("UTC").tz_localize(None) if index.tz is not None else index


def is_short_window(result: pd.DataFrame) -> bool:
    """Whether the window is too short to split by calendar month."""
    return len(result) * C.DT_H / 24.0 < SHORT_WINDOW_DAYS


def period_means(result: pd.DataFrame) -> pd.DataFrame:
    """Currents and energy by calendar month, or over the whole of a short window.

    One row per month in the window; a window under four weeks is one row
    named after its dates. Currents are means in amperes, energies totals.
    """
    ok = result[result["converged"]]
    naive = _utc_naive(ok.index)
    if is_short_window(result):
        keys = np.zeros(len(ok), dtype=int)
        names = [f"{naive[0]:%d %b} to {naive[-1]:%d %b %Y}"] if len(ok) else []
    else:
        months = naive.to_period("M")
        order = list(dict.fromkeys(months))
        position = {month: i for i, month in enumerate(order)}
        keys = np.array([position[month] for month in months])
        names = [month.strftime("%b %Y") for month in order]

    pushed = pushed_current(ok)
    rating = corridor_series(ok, "rating_{zone}_a", "min")
    weather = (corridor_series(ok, "rating_{zone}_weather_a", "min")
               if all(f"rating_{z}_weather_a" in ok.columns for z in CORRIDOR_ZONES)
               else pd.Series(np.nan, index=ok.index))
    missing = np.full(len(ok), np.nan)
    frame = pd.DataFrame({
        "pushed": pushed.to_numpy() if pushed is not None else missing,
        "carried": corridor_series(ok, "i_{zone}_a", "max").to_numpy(),
        "rating": rating.to_numpy(),
        "weather": weather.to_numpy(),
        "above_static": ((pushed > SINGLE_STATIC_A).to_numpy(dtype=float)
                         if pushed is not None else missing),
        "above_rating": ((pushed > rating).to_numpy(dtype=float)
                         if pushed is not None else missing),
        "available": ok["available_total_mw"].to_numpy(),
        "delivered": realised_generation(ok).to_numpy(),
        "curtailed": ok["curtailed_total_mw"].to_numpy(),
        "export": ok["pcc_p_mw"].to_numpy(),
        "losses": (ok["loss_line_mw"] + ok["loss_trafo_mw"]).to_numpy(),
        "key": keys,
    })
    grouped = frame.groupby("key")
    table = pd.DataFrame({
        "hours": grouped.size() * C.DT_H,
        "pushed_mean": grouped["pushed"].mean(),
        "pushed_max": grouped["pushed"].max(),
        "carried_mean": grouped["carried"].mean(),
        "rating_mean": grouped["rating"].mean(),
        "weather_mean": grouped["weather"].mean(),
        "above_static_pct": 100.0 * grouped["above_static"].mean(),
        "above_rating_pct": 100.0 * grouped["above_rating"].mean(),
        "available_mwh": grouped["available"].sum() * C.DT_H,
        "delivered_mwh": grouped["delivered"].sum() * C.DT_H,
        "curtailed_mwh": grouped["curtailed"].sum() * C.DT_H,
        "net_export_mwh": grouped["export"].sum() * C.DT_H,
        "losses_mwh": grouped["losses"].sum() * C.DT_H,
    })
    table.index = [names[k] for k in table.index]
    return table


def duration_points(result: pd.DataFrame) -> pd.DataFrame:
    """Each current and rating exceeded for a given share of time [A]."""
    ok = result[result["converged"]]
    q = [1.0 - p / 100.0 for p in DURATION_POINTS_PCT]
    pushed = pushed_current(ok)
    weather = corridor_series(ok, "rating_{zone}_weather_a", "min")
    return pd.DataFrame({
        "pct": DURATION_POINTS_PCT,
        "pushed_a": pushed.quantile(q).to_numpy() if pushed is not None else np.nan,
        "carried_a": corridor_series(ok, "i_{zone}_a", "max").quantile(q).to_numpy(),
        "rating_a": corridor_series(ok, "rating_{zone}_a", "min").quantile(q).to_numpy(),
        "weather_a": weather.quantile(q).to_numpy() if weather.notna().any() else np.nan,
    })


# ── Formatting ───────────────────────────────────────────────────────────────
def _num(value, unit: str):
    """A value rounded for reading, or None when there is nothing to show."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    decimals = _DECIMALS.get(unit, 2)
    if decimals == 0:
        return int(round(value))
    return round(value, decimals) + 0.0       # + 0.0 turns -0.0 into 0.0


def _change(static, dlr, relative: bool, unit: str):
    """Absolute and relative change from the static run to the DLR run.

    No relative change is given against a static value that rounds to zero:
    a percentage of almost nothing is a large number that means nothing.
    """
    try:
        static, dlr = float(static), float(dlr)
    except (TypeError, ValueError):
        return None, None
    if not (math.isfinite(static) and math.isfinite(dlr)):
        return None, None
    delta = dlr - static
    pct = 100.0 * delta / abs(static) if relative and _num(static, unit) else None
    return delta, pct


def _table(rows, columns, note: str = "") -> pd.DataFrame:
    table = pd.DataFrame(rows, columns=columns, dtype=object)
    table.attrs["note"] = note
    return table


def _days(result: pd.DataFrame) -> str:
    days = len(result) * C.DT_H / 24.0
    return f"{days:g} day" + ("" if days == 1 else "s")


def _run_column(cfg) -> str:
    return "Static" if cfg.dlr_mode == 0 else "DLR"


def _labels(cfg) -> dict:
    return {"single": SINGLE["label"], "single_a": f"{SINGLE_STATIC_A:.0f}",
            "twin": TWIN["label"], "twin_a": f"{TWIN_STATIC_A:.0f}",
            "window": f"{cfg.q_window_mvar:g}", "energy": f"{cfg.energy_price_eur_mwh:g}",
            "reactive": f"{cfg.reactive_price_eur_mvarh:g}"}


# ── Summary tables ───────────────────────────────────────────────────────────
# (metric, label, unit, whether a relative change means anything). A share is
# already a percentage: its change is given in percentage points only.
HEADLINE_ROWS = (
    ("available_mwh", "Generation available", "MWh", True),
    ("delivered_mwh", "Generation delivered", "MWh", True),
    ("curtailed_mwh", "Curtailed energy", "MWh", True),
    ("curtailed_pct", "Curtailed share of available", "%", False),
    ("net_export_mwh", "Net export to grid at PCC", "MWh", True),
    ("losses_mwh", "Network losses", "MWh", True),
    ("pushed_current_max_a", "Peak current pushed by all plants", "A", True),
    ("rating_mean_a", "Mean operative rating", "A", True),
    ("pushed_above_single_static_pct", "Pushed current above {single_a} A ({single} static)",
     "% of time", False),
    ("pushed_above_rating_pct", "Pushed current above the operative rating", "% of time", False),
    ("pushed_above_twin_static_pct", "Pushed current above {twin_a} A ({twin} static)",
     "% of time", False),
    ("technical_cost_keur", "Technical cost", "k€", True),
)
COST_ROWS = (
    ("curtailed_mwh", "Curtailed energy", "MWh", True),
    ("losses_mwh", "Network losses", "MWh", True),
    ("reactive_exchange_mvarh", "Reactive exchange at PCC", "MVArh", True),
    ("reactive_outside_window_mvarh", "Reactive outside ±{window} MVAr", "MVArh", True),
    ("hours_q_outside_window", "Time outside reactive window", "h", True),
    ("cost_curtailed_keur", "Curtailment cost at {energy} €/MWh", "k€", True),
    ("cost_losses_keur", "Loss cost at {energy} €/MWh", "k€", True),
    ("cost_reactive_keur", "Reactive cost at {reactive} €/MVArh", "k€", True),
    ("technical_cost_keur", "Total technical cost", "k€", True),
)


def _comparison(cfg, rows, m: dict, r: dict | None, note: str) -> pd.DataFrame:
    names = _labels(cfg)
    data = []
    for key, label, unit, relative in rows:
        label = label.format(**names)
        if r is None:
            data.append([label, unit, _num(m.get(key), unit)])
            continue
        delta, pct = _change(r.get(key), m.get(key), relative, unit)
        data.append([label, unit, _num(r.get(key), unit), _num(m.get(key), unit),
                     _num(delta, unit), _num(pct, "%")])
    columns = (["Metric", "Unit", _run_column(cfg)] if r is None
               else ["Metric", "Unit", "Static", "DLR", "Change", "Change %"])
    return _table(data, columns, note)


def _ceilings(cfg) -> str:
    cap = ("no cap" if cfg.rating_cap_a is None
           else f"cap {cfg.rating_cap_a:.0f} A ({cfg.dlr_cap_ratio:g} × static)")
    equipment = ("equipment limit off" if cfg.equipment_rating_a is None
                 else f"series equipment {cfg.equipment_rating_a:.0f} A")
    return f"{cap}, {equipment}"


def _run_table(cfg, result: pd.DataFrame, reference) -> pd.DataFrame:
    storage = (f"{cfg.storage_p_mw:.0f} MW / {cfg.storage_e_mwh:.0f} MWh"
               if cfg.storage_enabled else "none")
    rows = [
        ["Scenario", cfg.stem, RATING_METHODS[cfg.dlr_mode]],
        ["Window", f"{result.index[0]:%Y-%m-%d} to {result.index[-1]:%Y-%m-%d}",
         f"{_days(result)}, {len(result)} steps of {C.DT_H * 60:.0f} min"],
        ["Corridor", cfg.conductor_label,
         f"{C.CORRIDOR_LENGTH_KM:.0f} km, static rating {cfg.static_rating_a:.0f} A"],
        ["Reinforcement reference", TWIN["label"], f"static rating {TWIN_STATIC_A:.0f} A"],
        ["Generation", f"{cfg.n_der} of {len(cfg.der_enabled)} plants, "
                       f"{cfg.der_fleet_mw:.0f} MW", f"{cfg.control_mode} control"],
        ["Storage", storage, ""],
    ]
    if cfg.dlr_mode == 0:
        rows.append(["Operative rating", f"static {cfg.static_rating_a:.0f} A", "fixed"])
    else:
        rows.append(["Operative rating", "lowest of heat balance, cap, equipment",
                     _ceilings(cfg)])
    if cfg.dlr_mode == 2:
        rows.append(["Rating weather", f"bearing Z1 {cfg.azimuth_z1_deg:.0f}°, "
                                       f"Z2 {cfg.azimuth_z2_deg:.0f}°",
                     f"{100 * cfg.dlr_wind_factor:.0f} % of the conductor-height wind "
                     f"counted as cooling"])
    elif cfg.dlr_mode == 1:
        rows.append(["Rating weather", "measured air temperature and sun",
                     f"wind fixed at {cfg.low_wind_ms:g} m/s across the line"])
    if reference is not None:
        rows.append(["Compared with", reference.cfg.stem,
                     f"static rating, otherwise identical ({reference.origin})"])
    rows.append(["Prices", f"{cfg.energy_price_eur_mwh:g} €/MWh, "
                           f"{cfg.reactive_price_eur_mvarh:g} €/MVArh",
                 "indicative, for the technical cost"])
    return _table(rows, ["Item", "Value", "Detail"], "what was simulated")


def _stats_row(name: str, series: pd.Series) -> list:
    """Mean, minimum, 95th percentile and maximum, and time above both references."""
    s = series.dropna()
    if s.empty:
        return [name] + [None] * 6
    return [name, _num(s.mean(), "A"), _num(s.min(), "A"), _num(s.quantile(0.95), "A"),
            _num(s.max(), "A"), _num(_share(s > SINGLE_STATIC_A), "%"),
            _num(_share(s > TWIN_STATIC_A), "%")]


def _current_table(cfg, ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    pushed = pushed_current(ok)
    if pushed is not None:
        rows.append(_stats_row("Pushed by all plants (before curtailment)", pushed))
    rows.append(_stats_row("Carried (after curtailment)",
                           corridor_series(ok, "i_{zone}_a", "max")))
    if cfg.dlr_mode > 0:
        rows.append(_stats_row("Operative DLR rating",
                               corridor_series(ok, "rating_{zone}_a", "min")))
        rows.append(_stats_row("DLR ampacity, heat balance (weather)",
                               corridor_series(ok, "rating_{zone}_weather_a", "min")))
    for name, amps in ((f"Static rating, {SINGLE['label']}", SINGLE_STATIC_A),
                       (f"Reinforcement, {TWIN['label']} static", TWIN_STATIC_A)):
        rows.append([name] + [_num(amps, "A")] * 4 + [None, None])
    columns = ["Current", "Mean (A)", "Min (A)", "P95 (A)", "Max (A)",
               f"Time above {SINGLE_STATIC_A:.0f} A (%)", f"Time above {TWIN_STATIC_A:.0f} A (%)"]
    return _table(rows, columns, "highest current on any corridor segment; pushed = every "
                                 "plant at full available output, carried = after curtailment")


def _segment_table(ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for line in EXPORT_PATH_LINES:
        name, km = SEGMENTS[line]
        amps = ok[f"i_{line}_a"]
        power = ok[f"p_{line}_mw"]
        loading = 100.0 * amps / ok[f"rating_{ZONE_OF[line]}_a"]
        rows.append([name, ZONE_OF[line], _num(km, "km"), _num(power.mean(), "MW"),
                     _num(power.max(), "MW"), _num(amps.mean(), "A"), _num(amps.max(), "A"),
                     _num(loading.mean(), "%"), _num(loading.max(), "%"),
                     _num(_share(amps > SINGLE_STATIC_A), "%")])
    columns = ["Segment", "Zone", "Length (km)", "Mean P (MW)", "Max P (MW)", "Mean I (A)",
               "Max I (A)", "Mean loading (%)", "Max loading (%)",
               f"Time above {SINGLE_STATIC_A:.0f} A (%)"]
    return _table(rows, columns, "export path from the head to the grid interface, after "
                                 "curtailment; loading against the operative rating")


def _generation_table(cfg, ok: pd.DataFrame) -> pd.DataFrame:
    hours = len(ok) * C.DT_H

    def row(name, rated, connected, available, delivered, curtailed, peak):
        return [name, _num(rated, "MW"), connected, _num(available, "MWh"),
                _num(delivered, "MWh"), _num(curtailed, "MWh"),
                _num(100.0 * curtailed / available if available > 0 else 0.0, "%"),
                _num(100.0 * delivered / (rated * hours) if rated and hours else 0.0, "%"),
                _num(peak, "MW")]

    rows, totals = [], np.zeros(4)
    for unit in DER_UNITS:
        connected = bool(cfg.der_enabled.get(unit, False))
        rated = C.DER_RATING_MW[unit] if connected else 0.0
        available = _energy_mwh(ok[f"available_{unit}_mw"])
        delivered = _energy_mwh(ok[f"p_{unit}_mw"])
        curtailed = _energy_mwh(ok[f"curtailed_{unit}_mw"])
        totals += [rated, available, delivered, curtailed]
        rows.append(row(unit, C.DER_RATING_MW[unit], "yes" if connected else "no",
                        available, delivered, curtailed, ok[f"p_{unit}_mw"].max()))
    rated, available, delivered, curtailed = totals
    rows.append(row("Total connected", rated, f"{cfg.n_der} of {len(DER_UNITS)}",
                    available, delivered, curtailed, realised_generation(ok).max()))
    columns = ["Plant", "Rated (MW)", "Connected", "Available (MWh)", "Delivered (MWh)",
               "Curtailed (MWh)", "Curtailed (%)", "Capacity factor (%)", "Peak output (MW)"]
    return _table(rows, columns, "available = what the weather offered, delivered = what "
                                 "was injected after curtailment")


def _interface_table(cfg, ok: pd.DataFrame, m: dict) -> pd.DataFrame:
    p, q = ok["pcc_p_mw"], ok["pcc_q_mvar"]
    rows = [
        ["Net export", "MWh", m["net_export_mwh"]],
        ["Energy exported", "MWh", _energy_mwh(p.clip(lower=0))],
        ["Energy imported", "MWh", _energy_mwh((-p).clip(lower=0))],
        ["Mean active power", "MW", p.mean()],
        ["Peak export", "MW", p.max()],
        [f"Time above the {cfg.export_cap_mw:g} MW export cap", "h", m["hours_over_export_cap"]],
        ["Mean reactive power", "MVAr", q.mean()],
        ["Lowest reactive power", "MVAr", q.min()],
        ["Highest reactive power", "MVAr", q.max()],
        ["Reactive energy imported", "MVArh", _energy_mwh((-q).clip(lower=0))],
        ["Reactive energy exported", "MVArh", _energy_mwh(q.clip(lower=0))],
        [f"Time outside the ±{cfg.q_window_mvar:g} MVAr window", "h",
         m["hours_q_outside_window"]],
        ["Reactive energy outside the window", "MVArh", m["reactive_outside_window_mvarh"]],
    ]
    return _table([[name, unit, _num(v, unit)] for name, unit, v in rows],
                  ["Metric", "Unit", "Value"],
                  "power crossing the point of common coupling; positive = export to the grid")


def _voltage_table(ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for bus in VOLTAGE_BUSES:
        column = f"v_{bus}_pu"
        if column not in ok:
            continue
        v = ok[column]
        rows.append([bus, _num(v.min(), "pu"), _num(v.mean(), "pu"), _num(v.max(), "pu"),
                     _num((v > C.V_MAX_PU).sum() * C.DT_H, "h"),
                     _num((v < C.V_MIN_PU).sum() * C.DT_H, "h")])
    columns = ["Bus", "Min (pu)", "Mean (pu)", "Max (pu)", f"Time above {C.V_MAX_PU:g} pu (h)",
               f"Time below {C.V_MIN_PU:g} pu (h)"]
    return _table(rows, columns, f"corridor from head to grid interface, then the far "
                                 f"substations; band {C.V_MIN_PU:g} to {C.V_MAX_PU:g} pu")


def _transformer_table(ok: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, role in TRANSFORMER_ROLES.items():
        column = f"loading_{name}_pct"
        if column not in ok:
            continue
        load = ok[column]
        rows.append([name, role, _num(load.mean(), "%"), _num(load.max(), "%"),
                     _num((load > 100.0).sum() * C.DT_H, "h")])
    return _table(rows, ["Transformer", "Role", "Mean loading (%)", "Max loading (%)",
                         "Time above 100 % (h)"], "loading against the transformer rating")


def _cost_table(cfg, m: dict, r: dict | None) -> pd.DataFrame:
    if r is not None:
        return _comparison(cfg, COST_ROWS, m, r, "static against DLR; indicative prices")
    energy, reactive = cfg.energy_price_eur_mwh, cfg.reactive_price_eur_mvarh
    items = [
        ("Curtailed energy", m["curtailed_mwh"], "MWh", energy, "€/MWh"),
        ("Losses in lines", m["losses_line_mwh"], "MWh", energy, "€/MWh"),
        ("Losses in transformers", m["losses_trafo_mwh"], "MWh", energy, "€/MWh"),
        (f"Reactive energy outside ±{cfg.q_window_mvar:g} MVAr",
         m["reactive_outside_window_mvarh"], "MVArh", reactive, "€/MVArh"),
    ]
    rows = [[name, _num(quantity, unit), unit, f"{price:g} {price_unit}",
             _num(quantity * price / 1000.0, "k€")]
            for name, quantity, unit, price, price_unit in items]
    rows.append(["Total", None, "", "", _num(m["technical_cost_keur"], "k€")])
    return _table(rows, ["Item", "Quantity", "Unit", "Price", "Cost (k€)"],
                  "indicative prices; set them with --energy-price and --reactive-price")


def _binding_table(cfg, m: dict) -> pd.DataFrame:
    cap = "none" if cfg.rating_cap_a is None else _num(cfg.rating_cap_a, "A")
    equipment = "none" if cfg.equipment_rating_a is None else _num(cfg.equipment_rating_a, "A")
    cap_name = ("Cap" if cfg.dlr_cap_ratio is None
                else f"Cap ({cfg.dlr_cap_ratio:g} × static)")
    rows = [["Heat balance (weather)", "varies", _num(m["rating_by_weather_pct"], "%")],
            [cap_name, cap, _num(m["rating_by_cap_pct"], "%")],
            ["Series equipment", equipment, _num(m["rating_by_equipment_pct"], "%")]]
    return _table(rows, ["Limit", "A", "Share of time (%)"],
                  "which limit set the operative rating, step by step")


def _period_table(result: pd.DataFrame) -> pd.DataFrame:
    means = period_means(result)
    rows = [[name, _num(row.hours, "h"), _num(row.pushed_mean, "A"), _num(row.pushed_max, "A"),
             _num(row.carried_mean, "A"), _num(row.rating_mean, "A"), _num(row.weather_mean, "A"),
             _num(row.above_static_pct, "%"), _num(row.above_rating_pct, "%"),
             _num(row.available_mwh, "MWh"), _num(row.delivered_mwh, "MWh"),
             _num(row.curtailed_mwh, "MWh"), _num(row.net_export_mwh, "MWh"),
             _num(row.losses_mwh, "MWh")]
            for name, row in means.iterrows()]
    columns = ["Period", "Hours", "Pushed mean (A)", "Pushed max (A)", "Carried mean (A)",
               "Operative mean (A)", "Heat balance mean (A)",
               f"Pushed above {SINGLE_STATIC_A:.0f} A (%)", "Pushed above rating (%)",
               "Available (MWh)", "Delivered (MWh)", "Curtailed (MWh)", "Net export (MWh)",
               "Losses (MWh)"]
    return _table(rows, columns, "currents are means, energies totals; one row per calendar "
                                 "month, or one for a window under four weeks")


def _duration_table(result: pd.DataFrame) -> pd.DataFrame:
    points = duration_points(result)
    rows = [[int(row.pct), _num(row.pushed_a, "A"), _num(row.carried_a, "A"),
             _num(row.rating_a, "A"), _num(row.weather_a, "A")]
            for row in points.itertuples(index=False)]
    return _table(rows, ["Time exceeded (%)", "Pushed (A)", "Carried (A)",
                         "Operative rating (A)", "Heat balance (A)"],
                  "the current each line reaches or exceeds for the given share of time")


# Run counters worth a line when they are not zero.
_FLAGS = (
    ("not_converged", "Steps that did not converge"),
    ("curtail_failures", "Curtailment trials that failed to solve"),
    ("storage_resolve_failed", "Storage reconciliations failed"),
    ("soc_clamped", "Steps clamped by a state-of-charge limit"),
    ("reserve_short", "Steps short of contracted reserve"),
    ("t_cond_saturated", "Conductor temperature solves saturated"),
)


def _checks_table(cfg, result: pd.DataFrame, m: dict) -> pd.DataFrame:
    days = max(len(result) * C.DT_H / 24.0, 1e-9)
    rows = [
        ["Converged steps", "%", m["converged_pct"]],
        ["Control loop converged", "%", m["reg_converged_pct"]],
        ["Current above operative rating", "h", m["hours_corridor_over_limit"]],
        [f"Conductor above {cfg.t_cond_max_c:g} °C", "h", m["hours_over_temperature"]],
    ]
    if cfg.dlr_mode > 0:
        rows.append(["Highest conductor temperature", "°C", m["t_cond_max_c"]])
    rows += [
        [f"Any 110 kV bus above {C.V_MAX_PU:g} pu", "h", m["hours_voltage_high"]],
        [f"Any 110 kV bus below {C.V_MIN_PU:g} pu", "h", m["hours_voltage_low"]],
        [f"Export above the {cfg.export_cap_mw:g} MW cap", "h", m["hours_over_export_cap"]],
        ["Largest reactive tracking error", "MVAr", m["q_tracking_error_max_mvar"]],
        ["Tap operations", "per day", m["oltc_operations"] / days],
        ["Reactor operations", "per day", m["reactor_operations"] / days],
    ]
    counters = result.attrs.get("counters", {})
    rows += [[text, "steps", counters[key]] for key, text in _FLAGS if counters.get(key)]
    return _table([[name, unit, _num(v, unit)] for name, unit, v in rows],
                  ["Check", "Unit", "Value"], "limits after control; the hours should be zero")


def _storage_table(m: dict) -> pd.DataFrame:
    rows = [["Discharged", "MWh", m["storage_discharged_mwh"]],
            ["Charged", "MWh", m["storage_charged_mwh"]],
            ["Equivalent cycles", "cycles", m["storage_cycles"]],
            ["Delivery shortfall", "MWh", m["storage_shortfall_mwh"]],
            ["Hours short of reserve", "h", m["hours_reserve_short"]],
            ["Mean state of charge", "%", m["storage_soc_mean_pct"]]]
    return _table([[name, unit, _num(v, unit)] for name, unit, v in rows],
                  ["Metric", "Unit", "Value"], "battery at TAP_B; discharge counts as export")


def _reference_metrics(reference) -> dict | None:
    if reference is None:
        return None
    r = metrics(reference.cfg, reference.result)
    return r if r.get("converged_steps") else None


def summary_tables(cfg, result: pd.DataFrame, reference=None) -> dict:
    """The run summary as titled tables, in reading order.

    Network behaviour first: currents, power flow by segment, generation,
    grid interface, voltage and transformers; then cost, the rating, means by
    period, duration and checks. With a static reference the headline and
    cost tables set static and DLR side by side.
    """
    m = metrics(cfg, result)
    r = _reference_metrics(reference)
    tables = {"RUN": _run_table(cfg, result, reference if r else None)}
    if not m.get("converged_steps"):
        tables["RESULT"] = _table([["No converged timesteps"]], ["Note"])
        return tables
    ok = result[result["converged"]]
    headline_note = ("static against DLR, change in the unit and in per cent" if r
                     else "the run at a glance")
    tables["HEADLINE: DLR VS STATIC" if r else "HEADLINE"] = _comparison(
        cfg, HEADLINE_ROWS, m, r, headline_note)
    tables["CORRIDOR CURRENT"] = _current_table(cfg, ok)
    tables["CORRIDOR POWER FLOW"] = _segment_table(ok)
    tables["GENERATION BY PLANT"] = _generation_table(cfg, ok)
    tables["GRID INTERFACE (PCC)"] = _interface_table(cfg, ok, m)
    tables["VOLTAGE"] = _voltage_table(ok)
    tables["TRANSFORMER LOADING"] = _transformer_table(ok)
    tables["TECHNICAL COST: DLR VS STATIC" if r else "TECHNICAL COST"] = _cost_table(cfg, m, r)
    if cfg.dlr_mode > 0:
        tables["RATING SET BY"] = _binding_table(cfg, m)
    tables["PERIOD MEANS" if is_short_window(result) else "MONTHLY MEANS"] = _period_table(result)
    tables["CURRENT DURATION"] = _duration_table(result)
    tables["CHECKS"] = _checks_table(cfg, result, m)
    if cfg.storage_enabled:
        tables["STORAGE"] = _storage_table(m)
    return tables


def _cell(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def write_tables(tables: dict, path: Path) -> None:
    """Write titled tables one after another into a single CSV file.

    Each table is a title row (the title, then a one-line note), a header row
    and its rows, followed by a blank line. Written with a byte-order mark so
    a spreadsheet shows € and °C.
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for i, (title, table) in enumerate(tables.items()):
            if i:
                writer.writerow([])
            note = table.attrs.get("note", "")
            writer.writerow([title, note] if note else [title])
            writer.writerow(list(table.columns))
            for row in table.itertuples(index=False):
                writer.writerow([_cell(v) for v in row])


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def format_table(title: str, table: pd.DataFrame) -> str:
    """One summary table as aligned text, numbers right-aligned."""
    header = [str(c) for c in table.columns]
    body = [[_cell(v) for v in row] for row in table.itertuples(index=False)]
    widths = [max([len(header[i])] + [len(r[i]) for r in body]) for i in range(len(header))]
    numeric = [i > 0 and all(_is_number(r[i]) or r[i] == "" for r in body)
               for i in range(len(header))]

    def line(cells):
        return "  " + "  ".join(c.rjust(w) if num else c.ljust(w)
                                for c, w, num in zip(cells, widths, numeric, strict=True))

    return "\n".join([f"  {title}"] + [line(r).rstrip() for r in [header] + body])


def summary_text(cfg, result: pd.DataFrame, reference=None) -> str:
    """The setup, headline, cost and checks as console text.

    Printed at the end of a run; the summary CSV holds every table.
    """
    tables = summary_tables(cfg, result, reference)
    shown = [t for t in tables if t.split(":")[0] in ("RUN", "HEADLINE", "TECHNICAL COST",
                                                      "CHECKS", "RESULT")]
    rule = "=" * 78
    parts = [rule, f"  {cfg.stem}", rule]
    for title in shown:
        parts += [format_table(title, tables[title]), ""]
    parts += [f"  every table: {cfg.stem}_summary.csv", rule]
    return "\n".join(parts)


# ── Files ────────────────────────────────────────────────────────────────────
# Written by earlier versions and no longer: removed when a run of the same
# name is written again, so one folder never mixes two versions' outputs.
_RETIRED = ("_summary.txt", "_seasonal.csv")


def write_summary(cfg, result: pd.DataFrame, out_dir: Path, reference=None) -> dict:
    """Write the metrics and the summary CSV, and return their paths.

    The metrics file also records the run's full configuration and the
    fingerprint its static reference is found by.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = cfg.stem
    paths = {
        "metrics": out_dir / f"{stem}_metrics.json",
        "summary_csv": out_dir / f"{stem}_summary.csv",
    }
    meta = metrics(cfg, result)
    meta["static_key"] = static_key(cfg)
    meta["static_reference"] = reference.cfg.stem if reference is not None else None
    meta["config"] = to_record(cfg)
    paths["metrics"].write_text(json.dumps(meta, indent=2), encoding="utf-8")
    write_tables(summary_tables(cfg, result, reference), paths["summary_csv"])
    for suffix in _RETIRED:
        (out_dir / f"{stem}{suffix}").unlink(missing_ok=True)
    return paths


def write(cfg, result: pd.DataFrame, out_dir: Path, reference=None) -> dict:
    """Write every output file for one run and return the paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = cfg.stem
    paths = {
        "timeseries": out_dir / f"{stem}_timeseries.csv",
        "violations": out_dir / f"{stem}_violations.csv",
    }
    result.to_csv(paths["timeseries"], lineterminator="\n")
    violations(result).to_csv(paths["violations"], lineterminator="\n")
    paths.update(write_summary(cfg, result, out_dir, reference))
    return paths
