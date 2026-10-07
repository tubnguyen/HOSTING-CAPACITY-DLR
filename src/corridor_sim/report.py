"""Run outputs: a summary in tables and text, per-step results and metrics.

A dynamic-rating run is reported beside its static reference (reference.py),
so each headline number comes with the change DLR made to it, in the
quantity's own unit and in per cent. The technical cost - curtailed energy,
losses and reactive exchange - is given in energy units and, at indicative
prices, in euros.
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
from .network import CORRIDOR_ZONES
from .reference import static_key
from .simulate import realised_generation

RATING_METHODS = {0: "Static rating", 1: "Ambient-adjusted DLR", 2: "Full-weather DLR"}

# The single and twin builds' static ratings are references in every DLR
# summary, whichever build the run itself uses.
SINGLE = C.CONDUCTOR_OPTIONS["single"]
TWIN = C.CONDUCTOR_OPTIONS["twin"]
SINGLE_STATIC_A = SINGLE["max_i_ka"] * 1000.0
TWIN_STATIC_A = TWIN["max_i_ka"] * 1000.0

DURATION_POINTS_PCT = (0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)

# Decimals per unit in the summary tables.
_DECIMALS = {"A": 0, "MW": 1, "MWh": 1, "MVArh": 1, "h": 2, "%": 1, "% of time": 1,
             "°C": 1, "k€": 1, "per day": 1, "steps": 0, "cycles": 1}


def _energy_mwh(series: pd.Series) -> float:
    return float(series.sum() * C.DT_H)


def corridor_series(frame: pd.DataFrame, template: str, how: str) -> pd.Series:
    """Combine the per-zone columns named by `template` into one corridor series.

    The export path is one series thermal path: its rating is the lower zone's
    and its current the higher zone's.
    """
    zones = pd.concat([frame[template.format(zone=z)] for z in CORRIDOR_ZONES], axis=1)
    return zones.max(axis=1) if how == "max" else zones.min(axis=1)


def governing_binding(frame: pd.DataFrame) -> np.ndarray:
    """What set the corridor rating at each step: weather, cap, equipment or static."""
    ratings = pd.concat([frame[f"rating_{z}_a"] for z in CORRIDOR_ZONES], axis=1).to_numpy()
    labels = pd.concat([frame[f"rating_{z}_binding"] for z in CORRIDOR_ZONES], axis=1).to_numpy()
    return labels[np.arange(len(frame)), np.argmin(ratings, axis=1)]


def metrics(cfg, result: pd.DataFrame) -> dict:
    """Headline numbers for one run, used by the summary and the matrix table."""
    ok = result[result["converged"]]
    n = len(ok)
    if n == 0:
        return {"scenario": cfg.stem, "converged_steps": 0}

    available = _energy_mwh(ok["available_total_mw"])
    curtailed = _energy_mwh(ok["curtailed_total_mw"])
    delivered = _energy_mwh(realised_generation(ok))
    losses = _energy_mwh(ok["loss_line_mw"] + ok["loss_trafo_mw"])
    reactive_outside = _energy_mwh(ok["q_exceedance_mvar"])
    loading = corridor_series(ok, "loading_{zone}_pct", "max")
    rating = corridor_series(ok, "rating_{zone}_a", "min")
    current = corridor_series(ok, "i_{zone}_a", "max")
    above_single = current > SINGLE_STATIC_A
    binding = governing_binding(ok)
    rating_mean = float(rating.mean())
    weather_cols = [f"rating_{z}_weather_a" for z in CORRIDOR_ZONES
                    if f"rating_{z}_weather_a" in ok.columns]
    weather_mean = (float(pd.concat([ok[c] for c in weather_cols], axis=1).min(axis=1).mean())
                    if weather_cols else float("nan"))
    t_cond = pd.concat([ok[f"t_cond_{z}_c"] for z in CORRIDOR_ZONES], axis=1)

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
        "rating_cap_a": cfg.rating_cap_a if cfg.rating_cap_a is not None else float("nan"),
        "rating_equipment_a": (cfg.equipment_rating_a if cfg.equipment_rating_a is not None
                               else float("nan")),
        "hours_headroom_limited": float(ok["rating_headroom_limited"].sum() * C.DT_H),
        "headroom_limited_pct": 100.0 * float(ok["rating_headroom_limited"].mean()),
        # Share of steps each limit set the corridor rating; static runs are
        # set by none of the three.
        "rating_by_weather_pct": 100.0 * float(np.mean(binding == "weather")),
        "rating_by_cap_pct": 100.0 * float(np.mean(binding == "cap")),
        "rating_by_equipment_pct": 100.0 * float(np.mean(binding == "equipment")),
        "corridor_current_mean_a": float(current.mean()),
        "corridor_current_max_a": float(current.max()),
        # Time the corridor carried more than a single conductor's static
        # rating: how long a statically rated single conductor would have
        # been overloaded by the same flow.
        "hours_above_single_static": float(above_single.sum() * C.DT_H),
        "above_single_static_pct": 100.0 * float(above_single.mean()),
        "corridor_loading_mean_pct": float(loading.mean()),
        "corridor_loading_p95_pct": float(loading.quantile(0.95)),
        "hours_corridor_over_limit": float((loading > 100.0).sum() * C.DT_H),
        # Counted per interval, not per zone. Summing the zones would charge a
        # quarter-hour twice for a step in which both zones ran hot, which is
        # one interval of exposure, not two.
        "hours_over_temperature": float(
            (t_cond > cfg.t_cond_max_c).any(axis=1).sum() * C.DT_H),
        "t_cond_max_c": float(t_cond.max().max()),
        "pcc_peak_mw": float(ok["pcc_p_mw"].max()),
        "hours_over_export_cap": float((ok["pcc_p_mw"] > cfg.export_cap_mw).sum() * C.DT_H),
        "hours_voltage_high": float(ok["viol_L1"].sum() * C.DT_H),
        "hours_voltage_low": float(ok["viol_L2"].sum() * C.DT_H),
        "losses_mwh": losses,
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
        "runtime_s": float(result.attrs.get("runtime_s", float("nan"))),
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


def monthly_ampacity(result: pd.DataFrame) -> pd.DataFrame:
    """Operative rating and heat-balance ampacity by calendar month [A].

    One row per month in the window, with the mean, minimum and maximum of the
    corridor (lower-zone) rating and of what the weather alone allowed.
    """
    ok = result[result["converged"]]
    index = ok.index
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    weather_cols = [f"rating_{z}_weather_a" for z in CORRIDOR_ZONES]
    weather = (corridor_series(ok, "rating_{zone}_weather_a", "min")
               if all(c in ok.columns for c in weather_cols)
               else pd.Series(np.nan, index=ok.index))
    frame = pd.DataFrame({"rating": corridor_series(ok, "rating_{zone}_a", "min").to_numpy(),
                          "weather": weather.to_numpy()}, index=index.to_period("M"))
    stats = frame.groupby(level=0).agg(["mean", "min", "max"])
    stats.columns = [f"{name}_{stat}" for name, stat in stats.columns]
    return stats


def duration_points(result: pd.DataFrame) -> pd.DataFrame:
    """Corridor current and operative rating exceeded for a given share of time."""
    ok = result[result["converged"]]
    current = corridor_series(ok, "i_{zone}_a", "max")
    rating = corridor_series(ok, "rating_{zone}_a", "min")
    q = [1.0 - p / 100.0 for p in DURATION_POINTS_PCT]
    return pd.DataFrame({"pct": DURATION_POINTS_PCT,
                         "current_a": current.quantile(q).to_numpy(),
                         "rating_a": rating.quantile(q).to_numpy()})


# ── Summary tables ───────────────────────────────────────────────────────────
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


def _labels(cfg) -> dict:
    return {"single": SINGLE["label"], "single_a": f"{SINGLE_STATIC_A:.0f}",
            "window": f"{cfg.q_window_mvar:g}", "energy": f"{cfg.energy_price_eur_mwh:g}",
            "reactive": f"{cfg.reactive_price_eur_mvarh:g}"}


# (metric, label, unit, whether a relative change means anything). A share is
# already a percentage: its change is given in percentage points only.
HEADLINE_ROWS = (
    ("rating_mean_a", "Mean operative rating", "A", True),
    ("above_single_static_pct", "Time above {single_a} A ({single})", "% of time", False),
    ("available_mwh", "Generation available", "MWh", True),
    ("delivered_mwh", "Generation delivered", "MWh", True),
    ("net_export_mwh", "Net export to grid (PCC)", "MWh", True),
)
COST_ROWS = (
    ("curtailed_mwh", "Curtailed energy", "MWh", True),
    ("curtailed_pct", "Curtailed share of available", "%", False),
    ("losses_mwh", "Network losses", "MWh", True),
    ("reactive_exchange_mvarh", "Reactive exchange at PCC", "MVArh", True),
    ("reactive_outside_window_mvarh", "Reactive outside ±{window} MVAr", "MVArh", True),
    ("hours_q_outside_window", "Time outside reactive window", "h", True),
    ("cost_curtailed_keur", "Curtailment cost at {energy} €/MWh", "k€", True),
    ("cost_losses_keur", "Loss cost at {energy} €/MWh", "k€", True),
    ("cost_reactive_keur", "Reactive cost at {reactive} €/MVArh", "k€", True),
    ("technical_cost_keur", "Total technical cost", "k€", True),
)
# The text summary keeps the rows a reader acts on.
_TEXT_COST_KEYS = ("curtailed_mwh", "curtailed_pct", "losses_mwh",
                   "reactive_outside_window_mvarh", "hours_q_outside_window",
                   "technical_cost_keur")


def _days(result: pd.DataFrame) -> str:
    days = len(result) * C.DT_H / 24.0
    return f"{days:g} day" + ("" if days == 1 else "s")


def _run_column(cfg) -> str:
    return "Static" if cfg.dlr_mode == 0 else "DLR"


def _comparison(cfg, rows, m: dict, r: dict | None) -> pd.DataFrame:
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
    return pd.DataFrame(data, columns=columns, dtype=object)


def _ceilings(cfg) -> str:
    cap = ("no cap" if cfg.rating_cap_a is None
           else f"cap {cfg.rating_cap_a:.0f} A ({cfg.dlr_cap_ratio:g} × static)")
    equipment = ("equipment limit off" if cfg.equipment_rating_a is None
                 else f"series equipment {cfg.equipment_rating_a:.0f} A")
    return f"{cap}, {equipment}"


def _run_table(cfg, result: pd.DataFrame, reference) -> pd.DataFrame:
    storage = (f"{cfg.storage_p_mw:.0f} MW / {cfg.storage_e_mwh:.0f} MWh"
               if cfg.storage_enabled else "none")
    if cfg.dlr_mode == 0:
        rating = ["Operative rating", f"static {cfg.static_rating_a:.0f} A", "fixed"]
    else:
        rating = ["Operative rating", "lowest of heat balance, cap, equipment",
                  _ceilings(cfg)]
    rows = [
        ["Scenario", cfg.stem, RATING_METHODS[cfg.dlr_mode]],
        ["Window", f"{result.index[0]:%Y-%m-%d} to {result.index[-1]:%Y-%m-%d}",
         f"{_days(result)}, {len(result)} steps of {C.DT_H * 60:.0f} min"],
        ["Corridor", cfg.conductor_label,
         f"{C.CORRIDOR_LENGTH_KM:.0f} km, static rating {cfg.static_rating_a:.0f} A"],
        ["Generation", f"{cfg.n_der} of {len(cfg.der_enabled)} plants, "
                       f"{cfg.der_fleet_mw:.0f} MW", f"{cfg.control_mode} control"],
        ["Storage", storage, ""],
        rating,
    ]
    if reference is not None:
        rows.append(["Compared with", reference.cfg.stem,
                     f"static rating, otherwise identical ({reference.origin})"])
    elif cfg.dlr_mode > 0:
        rows.append(["Compared with", "nothing", "no static reference available"])
    rows.append(["Prices", f"{cfg.energy_price_eur_mwh:g} €/MWh, "
                           f"{cfg.reactive_price_eur_mvarh:g} €/MVArh",
                 "indicative, for the technical cost"])
    return pd.DataFrame(rows, columns=["Item", "Value", "Detail"], dtype=object)


def _line_rating_table(cfg, ok: pd.DataFrame) -> pd.DataFrame:
    series = []
    if cfg.dlr_mode > 0:
        series.append(("Heat-balance ampacity (weather)", "A",
                       corridor_series(ok, "rating_{zone}_weather_a", "min")))
    series += [("Operative rating", "A", corridor_series(ok, "rating_{zone}_a", "min")),
               ("Corridor current", "A", corridor_series(ok, "i_{zone}_a", "max")),
               ("Corridor loading", "%", corridor_series(ok, "loading_{zone}_pct", "max"))]
    if cfg.dlr_mode > 0:
        series.append(("Conductor temperature", "°C",
                       corridor_series(ok, "t_cond_{zone}_c", "max")))
    rows = [[name, unit, _num(s.mean(), unit), _num(s.min(), unit), _num(s.max(), unit)]
            for name, unit, s in series]
    return pd.DataFrame(rows, columns=["Quantity", "Unit", "Mean", "Min", "Max"], dtype=object)


def _binding_table(cfg, m: dict) -> pd.DataFrame:
    cap = "none" if cfg.rating_cap_a is None else _num(cfg.rating_cap_a, "A")
    equipment = "none" if cfg.equipment_rating_a is None else _num(cfg.equipment_rating_a, "A")
    cap_name = ("Cap" if cfg.dlr_cap_ratio is None
                else f"Cap ({cfg.dlr_cap_ratio:g} × static)")
    rows = [["Heat balance (weather)", "varies", _num(m["rating_by_weather_pct"], "%")],
            [cap_name, cap, _num(m["rating_by_cap_pct"], "%")],
            ["Series equipment", equipment, _num(m["rating_by_equipment_pct"], "%")]]
    return pd.DataFrame(rows, columns=["Limit", "A", "Share of time %"], dtype=object)


def _monthly_table(result: pd.DataFrame) -> pd.DataFrame:
    stats = monthly_ampacity(result)
    rows = [[period.strftime("%b %Y"), _num(SINGLE_STATIC_A, "A"), _num(TWIN_STATIC_A, "A")]
            + [_num(stats.at[period, f"{name}_{stat}"], "A")
               for name in ("rating", "weather") for stat in ("mean", "min", "max")]
            for period in stats.index]
    columns = ["Month", f"{SINGLE['label']} static (A)", f"{TWIN['label']} static (A)",
               "DLR mean (A)", "DLR min (A)", "DLR max (A)",
               "Heat balance mean (A)", "Heat balance min (A)", "Heat balance max (A)"]
    return pd.DataFrame(rows, columns=columns, dtype=object)


def _duration_table(result: pd.DataFrame) -> pd.DataFrame:
    points = duration_points(result)
    rows = [[int(p), _num(i, "A"), _num(r, "A")]
            for p, i, r in points[["pct", "current_a", "rating_a"]].itertuples(index=False)]
    return pd.DataFrame(rows, columns=["Time exceeded (%)", "Corridor current (A)",
                                       "Operative rating (A)"], dtype=object)


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
        ["Current above operative rating", "h", m["hours_corridor_over_limit"]],
        [f"Conductor above {cfg.t_cond_max_c:g} °C", "h", m["hours_over_temperature"]],
        [f"Voltage above {C.V_MAX_PU:g} pu", "h", m["hours_voltage_high"]],
        [f"Voltage below {C.V_MIN_PU:g} pu", "h", m["hours_voltage_low"]],
        [f"Export above {cfg.export_cap_mw:g} MW cap", "h", m["hours_over_export_cap"]],
        ["Peak export at PCC", "MW", m["pcc_peak_mw"]],
        ["Tap operations", "per day", m["oltc_operations"] / days],
        ["Reactor operations", "per day", m["reactor_operations"] / days],
    ]
    counters = result.attrs.get("counters", {})
    rows += [[text, "steps", counters[key]] for key, text in _FLAGS if counters.get(key)]
    return pd.DataFrame([[name, unit, _num(v, unit)] for name, unit, v in rows],
                        columns=["Check", "Unit", "Value"], dtype=object)


def _storage_table(m: dict) -> pd.DataFrame:
    rows = [["Discharged", "MWh", m["storage_discharged_mwh"]],
            ["Charged", "MWh", m["storage_charged_mwh"]],
            ["Equivalent cycles", "cycles", m["storage_cycles"]],
            ["Delivery shortfall", "MWh", m["storage_shortfall_mwh"]],
            ["Hours short of reserve", "h", m["hours_reserve_short"]],
            ["Mean state of charge", "%", m["storage_soc_mean_pct"]]]
    return pd.DataFrame([[name, unit, _num(v, unit)] for name, unit, v in rows],
                        columns=["Metric", "Unit", "Value"], dtype=object)


def _reference_metrics(reference) -> dict | None:
    if reference is None:
        return None
    r = metrics(reference.cfg, reference.result)
    return r if r.get("converged_steps") else None


def summary_tables(cfg, result: pd.DataFrame, reference=None) -> dict:
    """The run summary as titled tables, in reading order.

    With a static reference the headline and technical cost tables give
    static, DLR, the change in the quantity's unit and the change in per cent.
    """
    m = metrics(cfg, result)
    r = _reference_metrics(reference)
    tables = {"RUN": _run_table(cfg, result, reference if r else None)}
    if not m.get("converged_steps"):
        tables["RESULT"] = pd.DataFrame([["No converged timesteps"]], columns=["Note"])
        return tables
    ok = result[result["converged"]]
    tables["HEADLINE: DLR VS STATIC" if r else "HEADLINE"] = _comparison(cfg, HEADLINE_ROWS, m, r)
    tables["TECHNICAL COST"] = _comparison(cfg, COST_ROWS, m, r)
    tables["LINE RATING"] = _line_rating_table(cfg, ok)
    if cfg.dlr_mode > 0:
        tables["RATING SET BY"] = _binding_table(cfg, m)
        tables["OPERATIVE AMPACITY BY MONTH"] = _monthly_table(result)
    tables["CURRENT DURATION"] = _duration_table(result)
    tables["CHECKS"] = _checks_table(cfg, result, m)
    if cfg.storage_enabled:
        tables["STORAGE"] = _storage_table(m)
    return tables


def write_tables(tables: dict, path: Path) -> None:
    """Write titled tables one after another into a single CSV file.

    Each table is its title, a header row and its rows, followed by a blank
    line. Written with a byte-order mark so a spreadsheet shows € and °C.
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for i, (title, table) in enumerate(tables.items()):
            if i:
                writer.writerow([])
            writer.writerow([title])
            writer.writerow(list(table.columns))
            for row in table.itertuples(index=False):
                writer.writerow(["" if v is None or (isinstance(v, float) and math.isnan(v))
                                 else v for v in row])


# ── Text summary ─────────────────────────────────────────────────────────────
def _fmt(value, unit: str, signed: bool = False) -> str:
    if value is None:
        return "-"
    value = float(value)
    if not math.isfinite(value):
        return "-"
    if unit in ("A", "steps"):
        decimals = 0
    elif unit in ("MWh", "MVArh"):
        decimals = 0 if abs(value) >= 100 else 1
    else:
        decimals = 0 if abs(value) >= 1000 else 1
    value = round(value, decimals) + 0.0        # no "-0"
    return f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"


_TEXT_WIDTH = 80


def _text_rows(cfg, rows, m: dict, r: dict | None, title: str = "") -> list:
    """A header line, then one aligned line per metric."""
    names = _labels(cfg)
    if r is None:
        lines = [f"  {title:<39s}{_run_column(cfg):>10s}"]
    else:
        lines = [f"  {title:<39s}{'Static':>10s}{'DLR':>10s}{'Change':>11s}{'%':>8s}"]
    for key, label, unit, relative in rows:
        label = label.format(**names)
        if r is None:
            lines.append(f"  {label:<30s}{unit:<9s}{_fmt(m.get(key), unit):>10s}")
            continue
        delta, pct = _change(r.get(key), m.get(key), relative, unit)
        change = _fmt(delta, unit, signed=True) + (" pp" if unit.startswith("%") else "")
        lines.append(f"  {label:<30s}{unit:<9s}{_fmt(r.get(key), unit):>10s}"
                     f"{_fmt(m.get(key), unit):>10s}{change:>11s}"
                     f"{'' if pct is None else _fmt(pct, '%', signed=True):>8s}")
    return lines


def summary_text(cfg, result: pd.DataFrame, reference=None) -> str:
    """Short human-readable run report; the summary CSV holds every table."""
    m = metrics(cfg, result)
    if not m.get("converged_steps"):
        return "No converged timesteps."
    r = _reference_metrics(reference)
    counters = result.attrs.get("counters", {})
    days = len(result) * C.DT_H / 24.0
    rule = "=" * _TEXT_WIDTH
    title = RATING_METHODS[cfg.dlr_mode] + (" compared with the static rating" if r else "")
    storage = (f"storage {cfg.storage_p_mw:.0f} MW / {cfg.storage_e_mwh:.0f} MWh"
               if cfg.storage_enabled else "no storage")
    lines = [
        rule, f"  {cfg.stem}", f"  {title}", rule,
        f"  window      {result.index[0]:%Y-%m-%d} to {result.index[-1]:%Y-%m-%d} "
        f"({_days(result)})",
        f"  corridor    {cfg.conductor_label}, {C.CORRIDOR_LENGTH_KM:.0f} km, "
        f"static rating {cfg.static_rating_a:.0f} A",
    ]
    if cfg.dlr_mode > 0:
        lines.append(f"  ceilings    {_ceilings(cfg)}")
    lines.append(f"  generation  {cfg.n_der}/{len(cfg.der_enabled)} plants, "
                 f"{cfg.der_fleet_mw:.0f} MW, {cfg.control_mode} control | {storage}")
    if r is not None:
        lines.append(f"  static run  {reference.cfg.stem} ({reference.origin})")
    elif cfg.dlr_mode > 0:
        lines.append("  static run  none, so no comparison")

    lines += [""] + _text_rows(cfg, HEADLINE_ROWS, m, r)
    cost_rows = [row for row in COST_ROWS if row[0] in _TEXT_COST_KEYS]
    lines += [""] + _text_rows(cfg, cost_rows, m, r, title="TECHNICAL COST")
    lines.append(f"  at {cfg.energy_price_eur_mwh:g} €/MWh (curtailment, losses) and "
                 f"{cfg.reactive_price_eur_mvarh:g} €/MVArh (reactive outside window)")

    if cfg.dlr_mode > 0:
        who, share = max((("the weather", m["rating_by_weather_pct"]),
                          ("the cap", m["rating_by_cap_pct"]),
                          ("the series equipment", m["rating_by_equipment_pct"])),
                         key=lambda item: item[1])
        lines += ["", f"  Rating set by {who} {share:.1f} % of the time.",
                  f"  The weather alone would allow a mean of {m['rating_weather_mean_a']:.0f} A "
                  f"({m['rating_uplift_uncapped_pct']:+.0f} % on static)."]

    per_day = max(days, 1e-9)
    lines += [
        "", "  CHECKS",
        f"    converged {m['converged_pct']:.1f} % | over rating "
        f"{m['hours_corridor_over_limit']:.1f} h | over {cfg.t_cond_max_c:g} °C "
        f"{m['hours_over_temperature']:.1f} h",
        f"    voltage out of band {m['hours_voltage_high'] + m['hours_voltage_low']:.1f} h | "
        f"export over {cfg.export_cap_mw:g} MW {m['hours_over_export_cap']:.1f} h "
        f"(peak {m['pcc_peak_mw']:.0f} MW)",
        f"    tap operations {m['oltc_operations'] / per_day:.1f} / day | "
        f"reactor operations {m['reactor_operations'] / per_day:.1f} / day",
    ]
    if cfg.storage_enabled:
        lines += ["", "  STORAGE",
                  f"    {m['storage_discharged_mwh']:.0f} MWh discharged, "
                  f"{m['storage_charged_mwh']:.0f} MWh charged, "
                  f"{m['storage_cycles']:.1f} cycles",
                  f"    short of reserve {m['hours_reserve_short']:.1f} h, "
                  f"mean state of charge {m['storage_soc_mean_pct']:.0f} %"]
    flagged = [(counters[key], text) for key, text in _FLAGS if counters.get(key)]
    if flagged:
        lines += ["", "  FLAGS"] + [f"    {n:6d}  {text.lower()}" for n, text in flagged]

    lines += ["", f"  every table: {cfg.stem}_summary.csv", rule]
    return "\n".join(line.rstrip() for line in lines)


# ── Files ────────────────────────────────────────────────────────────────────
def write_summary(cfg, result: pd.DataFrame, out_dir: Path, reference=None) -> dict:
    """Write the outputs that depend on the static reference, and return their paths.

    The metrics file also records the run's full configuration and the
    fingerprint its static reference is found by.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = cfg.stem
    paths = {
        "metrics": out_dir / f"{stem}_metrics.json",
        "summary_csv": out_dir / f"{stem}_summary.csv",
        "summary": out_dir / f"{stem}_summary.txt",
    }
    meta = metrics(cfg, result)
    meta["static_key"] = static_key(cfg)
    meta["static_reference"] = reference.cfg.stem if reference is not None else None
    meta["config"] = to_record(cfg)
    paths["metrics"].write_text(json.dumps(meta, indent=2), encoding="utf-8")
    write_tables(summary_tables(cfg, result, reference), paths["summary_csv"])
    paths["summary"].write_text(summary_text(cfg, result, reference) + "\n", encoding="utf-8")
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
