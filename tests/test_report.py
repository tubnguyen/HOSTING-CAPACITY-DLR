"""Derived metrics, the summary tables and the text summary.

A metric that is wrong is worse than one that is missing: it is read, quoted
and acted on. These check the arithmetic of the headline numbers directly,
against hand-countable inputs.
"""
from __future__ import annotations

import pandas as pd
import pytest
from synthetic import result_frame

from corridor_sim import constants as C
from corridor_sim import report
from corridor_sim.config import build_config
from corridor_sim.reference import Reference, static_config

_frame = result_frame


def _compared(static_frame, preset="dlr2_der4", **over):
    """A DLR configuration and a static reference made from a hand-built frame."""
    cfg = build_config(preset, **over)
    return cfg, Reference(static_config(cfg), static_frame, "reused")


def test_over_temperature_counts_intervals_not_zone_exceedances():
    """Both zones hot in one quarter-hour is one quarter-hour of exposure.

    Summing the per-zone counts charges the interval twice, which inflates the
    one metric that says whether the line was ever run outside its design
    temperature - the number a rating study exists to keep at zero.
    """
    cfg = build_config("dlr2_der4")
    frame = _frame(8, t_cond_Z1_c=[95.0, 95.0] + [40.0] * 6,
                   t_cond_Z2_c=[95.0, 95.0] + [40.0] * 6)
    assert report.metrics(cfg, frame)["hours_over_temperature"] == pytest.approx(2 * C.DT_H)


def test_a_conductor_at_its_design_temperature_is_not_over_it():
    """Curtailed exactly onto its rating, the solve returns 80 C plus a hair."""
    cfg = build_config("dlr2_der4")
    frame = _frame(4, t_cond_Z2_c=[80.000008, 80.005, 80.5, 40.0])
    assert report.metrics(cfg, frame)["hours_over_temperature"] == pytest.approx(C.DT_H)


def test_over_temperature_counts_a_single_hot_zone():
    cfg = build_config("dlr2_der4")
    frame = _frame(8, t_cond_Z1_c=[95.0] + [40.0] * 7)
    assert report.metrics(cfg, frame)["hours_over_temperature"] == pytest.approx(C.DT_H)


def test_actuator_operations_are_committed_moves_not_loop_writes():
    """The summary's "tap operations" must be switch duty, not solver iterations."""
    cfg = build_config("dlr2_der4")
    frame = _frame(4, oltc_operations=[1, 0, 0, 1], oltc_loop_moves=[6, 4, 5, 6],
                   reactor_operations=[0, 1, 0, 0], reactor_loop_moves=[2, 3, 2, 2])
    m = report.metrics(cfg, frame)
    assert m["oltc_operations"] == 2
    assert m["reactor_operations"] == 1
    assert m["oltc_loop_moves"] == 21          # kept, but as a diagnostic
    assert m["oltc_operations"] < m["oltc_loop_moves"]


def test_headroom_metrics_separate_what_weather_offered_from_what_was_usable():
    cfg = build_config("dlr2_der4")
    frame = _frame(4, rating_headroom_limited=[1, 1, 0, 0])
    m = report.metrics(cfg, frame)
    assert m["hours_headroom_limited"] == pytest.approx(2 * C.DT_H)
    assert m["headroom_limited_pct"] == pytest.approx(50.0)
    assert m["rating_uplift_uncapped_pct"] > m["rating_uplift_pct"]
    assert m["rating_cap_a"] == cfg.rating_cap_a
    assert m["rating_equipment_a"] == cfg.equipment_rating_a


def test_energy_totals_are_time_weighted():
    cfg = build_config("dlr2_der4")
    frame = _frame(4, available_total_mw=[10.0] * 4, curtailed_total_mw=[2.0] * 4,
                   p_WF_1_mw=[8.0] * 4)
    m = report.metrics(cfg, frame)
    assert m["available_mwh"] == pytest.approx(4 * 10.0 * C.DT_H)
    assert m["curtailed_mwh"] == pytest.approx(4 * 2.0 * C.DT_H)
    assert m["delivered_mwh"] + m["curtailed_mwh"] == pytest.approx(m["available_mwh"])


def _titled(tables: dict, prefix: str) -> pd.DataFrame:
    """The table whose title starts with `prefix`, whatever its comparison suffix."""
    return next(table for title, table in tables.items() if title.startswith(prefix))


def test_rating_set_by_reports_the_ceiling_when_one_applies():
    tables = report.summary_tables(build_config("dlr2_der4"), _frame(8))
    shares = tables["RATING SET BY"].set_index("Limit")["Share of time (%)"]
    assert shares["Cap (2 × static)"] == pytest.approx(100.0)
    assert shares["Heat balance (weather)"] == pytest.approx(0.0)


def test_a_static_run_has_no_weather_tables():
    """Mode 0 is at nameplate; there is no heat balance and no ceiling to report."""
    tables = report.summary_tables(build_config("static_der4"), _frame(8, static=True))
    assert list(tables["HEADLINE"].columns) == ["Metric", "Unit", "Static"]
    assert "RATING SET BY" not in tables
    currents = tables["CORRIDOR CURRENT"]["Current"].tolist()
    assert not any("heat balance" in name for name in currents)


def test_checks_surface_counters_that_would_otherwise_be_silent():
    """Counters are computed every run; a non-zero one gets its own row."""
    frame = _frame(8)
    frame.attrs["counters"] = {"not_converged": 3, "t_cond_saturated": 2, "soc_clamped": 0}
    checks = report.summary_tables(build_config("dlr2_der4"), frame)["CHECKS"]
    names = checks["Check"].tolist()
    assert "Steps that did not converge" in names
    assert "Conductor temperature solves saturated" in names
    assert not any("state-of-charge" in name for name in names), "zero counters stay quiet"
    assert "per day" in checks["Unit"].tolist(), "actuator duty is given as a rate"


def test_technical_cost_is_the_energy_quantities_at_their_prices():
    cfg = build_config("dlr2_der4", energy_price_eur_mwh=40.0, reactive_price_eur_mvarh=8.0)
    frame = _frame(4, curtailed_total_mw=[2.0] * 4, q_exceedance_mvar=[4.0, 0.0, 4.0, 0.0])
    m = report.metrics(cfg, frame)
    assert m["reactive_outside_window_mvarh"] == pytest.approx(8.0 * C.DT_H)
    assert m["cost_curtailed_keur"] == pytest.approx(m["curtailed_mwh"] * 40.0 / 1000.0)
    assert m["cost_losses_keur"] == pytest.approx(m["losses_mwh"] * 40.0 / 1000.0)
    assert m["cost_reactive_keur"] == pytest.approx(8.0 * C.DT_H * 8.0 / 1000.0)
    assert m["technical_cost_keur"] == pytest.approx(
        m["cost_curtailed_keur"] + m["cost_losses_keur"] + m["cost_reactive_keur"])
    cost = report.summary_tables(cfg, frame)["TECHNICAL COST"].set_index("Item")
    assert cost.loc["Total", "Cost (k€)"] == pytest.approx(m["technical_cost_keur"], abs=0.05)
    assert cost.loc["Curtailed energy", "Price"] == "40 €/MWh"


def test_pushed_current_is_measured_against_every_limit():
    """780 A exactly is at the rating, not above it."""
    frame = _frame(4, i_Z1_uncurtailed_a=[800.0, 700.0, 1600.0, 780.0],
                   i_Z2_uncurtailed_a=[500.0, 500.0, 500.0, 1200.0])
    m = report.metrics(build_config("dlr2_der4"), frame)
    assert m["pushed_current_max_a"] == pytest.approx(1600.0)
    assert m["hours_pushed_above_single_static"] == pytest.approx(3 * C.DT_H)
    assert m["pushed_above_single_static_pct"] == pytest.approx(75.0)
    assert m["pushed_above_rating_pct"] == pytest.approx(50.0)      # 1600 and 1200 > 1170
    assert m["pushed_above_twin_static_pct"] == pytest.approx(25.0)


def test_a_result_saved_without_the_pushed_current_still_reports():
    frame = _frame(4).drop(columns=["i_Z1_uncurtailed_a", "i_Z2_uncurtailed_a"])
    m = report.metrics(build_config("dlr2_der4"), frame)
    assert m["pushed_current_max_a"] != m["pushed_current_max_a"]      # nan
    tables = report.summary_tables(build_config("dlr2_der4"), frame)
    assert tables["CORRIDOR CURRENT"]["Current"].iloc[0].startswith("Carried")


def test_export_and_reactive_exchange_are_time_weighted_and_signed_as_measured():
    frame = _frame(4, pcc_p_mw=[100.0, -20.0, 50.0, 0.0], pcc_q_mvar=[-40.0, 10.0, -40.0, 0.0])
    m = report.metrics(build_config("dlr2_der4"), frame)
    assert m["net_export_mwh"] == pytest.approx(130.0 * C.DT_H)
    assert m["reactive_exchange_mvarh"] == pytest.approx(90.0 * C.DT_H)
    pcc = report.summary_tables(build_config("dlr2_der4"), frame)["GRID INTERFACE (PCC)"]
    pcc = pcc.set_index("Metric")["Value"]
    assert pcc["Energy exported"] == pytest.approx(150.0 * C.DT_H)
    assert pcc["Energy imported"] == pytest.approx(20.0 * C.DT_H)
    assert pcc["Reactive energy imported"] == pytest.approx(80.0 * C.DT_H)


def test_the_lower_zone_decides_what_set_the_rating():
    frame = _frame(4, rating_Z2_a=[1100.0, 1100.0, 1170.0, 1170.0],
                   rating_Z2_binding=["weather", "weather", "cap", "cap"])
    m = report.metrics(build_config("dlr2_der4"), frame)
    assert m["rating_by_weather_pct"] == pytest.approx(50.0)
    assert m["rating_by_cap_pct"] == pytest.approx(50.0)
    assert m["rating_by_equipment_pct"] == pytest.approx(0.0)


def test_network_tables_follow_the_corridor():
    frame = _frame(4, i_CORR_W_C_a=[1170.0, 900.0, 600.0, 300.0],
                   v_PCC_pu=[1.06, 1.0, 1.0, 0.94], loading_T_WF2_1_pct=[101.0, 90.0, 80.0, 70.0])
    tables = report.summary_tables(build_config("dlr2_der4"), frame)
    flow = tables["CORRIDOR POWER FLOW"].set_index("Segment")
    assert list(flow.index) == ["SUB_A to TAP_PV", "TAP_PV to TAP_W", "TAP_W to SUB_C",
                                "SUB_C to TAP_B", "TAP_B to PCC"]
    assert flow.loc["TAP_W to SUB_C", "Max I (A)"] == 1170
    assert flow.loc["TAP_W to SUB_C", "Max loading (%)"] == pytest.approx(100.0)
    assert flow.loc["TAP_W to SUB_C", "Time above 780 A (%)"] == pytest.approx(50.0)
    voltage = tables["VOLTAGE"].set_index("Bus")
    assert voltage.loc["PCC", "Time above 1.05 pu (h)"] == pytest.approx(C.DT_H)
    assert voltage.loc["PCC", "Time below 0.95 pu (h)"] == pytest.approx(C.DT_H)
    trafo = tables["TRANSFORMER LOADING"].set_index("Transformer")
    assert trafo.loc["T_WF2_1", "Time above 100 % (h)"] == pytest.approx(C.DT_H)


def test_generation_by_plant_adds_up():
    frame = _frame(4, available_WF_2_mw=[20.0] * 4, p_WF_2_mw=[15.0] * 4,
                   curtailed_WF_2_mw=[5.0] * 4)
    table = report.summary_tables(build_config("dlr2_der4"), frame)["GENERATION BY PLANT"]
    table = table.set_index("Plant")
    assert table.loc["WF_2", "Curtailed (%)"] == pytest.approx(25.0)
    assert table.loc["Total connected", "Available (MWh)"] == pytest.approx(30.0 * 4 * C.DT_H)
    assert table.loc["Total connected", "Delivered (MWh)"] == pytest.approx(25.0 * 4 * C.DT_H)


def test_comparison_gives_the_change_in_unit_and_in_per_cent():
    cfg, ref = _compared(_frame(4, static=True, curtailed_total_mw=[5.0] * 4,
                                p_WF_1_mw=[5.0] * 4))
    tables = report.summary_tables(cfg, _frame(4, curtailed_total_mw=[2.0] * 4,
                                               p_WF_1_mw=[8.0] * 4), ref)
    headline = tables["HEADLINE: DLR VS STATIC"].set_index("Metric")
    assert list(headline.columns) == ["Unit", "Static", "DLR", "Change", "Change %"]
    assert headline.loc["Mean operative rating", "Change"] == 390
    assert headline.loc["Mean operative rating", "Change %"] == pytest.approx(50.0)
    # A share is already a percentage: its change is in points, not per cent.
    assert headline.loc["Curtailed share of available", "Change %"] is None
    cost = tables["TECHNICAL COST: DLR VS STATIC"].set_index("Metric")
    assert cost.loc["Curtailed energy", "Static"] == pytest.approx(5.0)
    assert cost.loc["Curtailed energy", "DLR"] == pytest.approx(2.0)
    assert cost.loc["Curtailed energy", "Change"] == pytest.approx(-3.0)
    assert cost.loc["Curtailed energy", "Change %"] == pytest.approx(-60.0)


def test_no_percentage_against_a_static_value_that_rounds_to_zero():
    cfg, ref = _compared(_frame(4, static=True, q_exceedance_mvar=[0.01, 0.0, 0.0, 0.0]))
    dlr = _frame(4, q_exceedance_mvar=[10.0] * 4)
    cost = _titled(report.summary_tables(cfg, dlr, ref), "TECHNICAL COST").set_index("Metric")
    row = cost.loc[f"Reactive outside ±{cfg.q_window_mvar:g} MVAr"]
    assert row["Change"] == pytest.approx(10.0 - 0.0025, abs=0.05)
    assert row["Change %"] is None


def test_without_a_reference_the_tables_show_the_run_alone():
    tables = report.summary_tables(build_config("dlr2_der4"), _frame(8))
    assert "HEADLINE" in tables and "HEADLINE: DLR VS STATIC" not in tables
    assert list(tables["HEADLINE"].columns) == ["Metric", "Unit", "DLR"]
    assert list(tables["TECHNICAL COST"].columns) == ["Item", "Quantity", "Unit", "Price",
                                                      "Cost (k€)"]
    assert "RATING SET BY" in tables and "PERIOD MEANS" in tables


def test_means_are_by_calendar_month_once_the_window_is_four_weeks_or_more():
    n = 30 * 96
    rating = [1000.0] * (16 * 96) + [1170.0] * (14 * 96)      # 16 days of Jan, 14 of Feb
    frame = _frame(n, start="2024-01-16", rating_Z1_a=rating)
    means = report.period_means(frame)
    assert list(means.index) == ["Jan 2024", "Feb 2024"]
    assert means["rating_mean"].tolist() == pytest.approx([1000.0, 1170.0])
    assert means["hours"].tolist() == pytest.approx([16 * 24.0, 14 * 24.0])
    assert "MONTHLY MEANS" in report.summary_tables(build_config("dlr2_der4"), frame)


def test_a_short_window_has_one_mean_over_the_period():
    means = report.period_means(_frame(8, i_Z1_uncurtailed_a=[400.0, 600.0] * 4))
    assert list(means.index) == ["01 Jan to 01 Jan 2024"]
    assert means["pushed_mean"].iloc[0] == pytest.approx(550.0)    # Z2 is 500, so 500 or 600


def test_duration_points_run_from_the_maximum_to_the_minimum():
    frame = _frame(5, i_Z1_a=[100.0, 200.0, 300.0, 400.0, 500.0], i_Z2_a=[0.0] * 5)
    points = report.duration_points(frame).set_index("pct")
    assert points.loc[0, "carried_a"] == pytest.approx(500.0)
    assert points.loc[50, "carried_a"] == pytest.approx(300.0)
    assert points.loc[100, "carried_a"] == pytest.approx(100.0)


def test_summary_csv_is_a_sequence_of_titled_tables(tmp_path):
    cfg, ref = _compared(_frame(8, static=True))
    path = tmp_path / "summary.csv"
    report.write_tables(report.summary_tables(cfg, _frame(8), ref), path)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("RUN,"), "each title carries a one-line note"
    titles = [line.split(",")[0] for line in lines]
    assert "HEADLINE: DLR VS STATIC" in titles and "CORRIDOR CURRENT" in titles
    title = titles.index("TECHNICAL COST: DLR VS STATIC")
    assert lines[title - 1] == "", "tables are separated by a blank line"
    assert lines[title + 1] == "Metric,Unit,Static,DLR,Change,Change %"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf"), "spreadsheets need the BOM for €"


def test_console_summary_puts_static_and_dlr_side_by_side():
    cfg, ref = _compared(_frame(8, static=True))
    text = report.summary_text(cfg, _frame(8), ref)
    assert "HEADLINE: DLR VS STATIC" in text and "TECHNICAL COST: DLR VS STATIC" in text
    assert "Static" in text and "DLR" in text and ref.cfg.stem in text
    assert "CHECKS" in text and f"{cfg.stem}_summary.csv" in text
