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


def test_summary_reports_the_ceiling_when_one_applies():
    cfg = build_config("dlr2_der4")
    text = report.summary_text(cfg, _frame(8, rating_headroom_limited=[1] * 8))
    assert "weather alone would" in text
    assert "Rating set by the cap 100.0 % of the time" in text
    assert "/ day" in text, "actuator duty should be given as a rate too"


def test_summary_omits_the_ceiling_for_a_static_study():
    """Mode 0 is at nameplate; there is no headroom for a ceiling to take."""
    text = report.summary_text(build_config("static_der4"), _frame(8))
    assert "weather alone would" not in text


def test_summary_surfaces_counters_that_would_otherwise_be_silent():
    """Counters were computed every run and printed by nothing."""
    frame = _frame(8)
    frame.attrs["counters"] = {"not_converged": 3, "t_cond_saturated": 2, "soc_clamped": 0}
    text = report.summary_text(build_config("dlr2_der4"), frame)
    assert "FLAGS" in text
    assert "did not converge" in text and "saturated" in text
    assert "state-of-charge" not in text, "zero counters stay quiet"


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


def test_time_above_the_single_static_rating_counts_only_steps_over_it():
    """780 A exactly is at the rating, not above it."""
    frame = _frame(4, i_Z1_a=[800.0, 700.0, 500.0, 780.0], i_Z2_a=[500.0, 500.0, 900.0, 500.0])
    m = report.metrics(build_config("dlr2_der4"), frame)
    assert m["hours_above_single_static"] == pytest.approx(2 * C.DT_H)
    assert m["above_single_static_pct"] == pytest.approx(50.0)
    assert m["corridor_current_max_a"] == pytest.approx(900.0)


def test_export_and_reactive_exchange_are_time_weighted_and_signed_as_measured():
    frame = _frame(4, pcc_p_mw=[100.0, -20.0, 50.0, 0.0], pcc_q_mvar=[-40.0, 10.0, -40.0, 0.0])
    m = report.metrics(build_config("dlr2_der4"), frame)
    assert m["net_export_mwh"] == pytest.approx(130.0 * C.DT_H)
    assert m["reactive_exchange_mvarh"] == pytest.approx(90.0 * C.DT_H)


def test_the_lower_zone_decides_what_set_the_rating():
    frame = _frame(4, rating_Z2_a=[1100.0, 1100.0, 1170.0, 1170.0],
                   rating_Z2_binding=["weather", "weather", "cap", "cap"])
    m = report.metrics(build_config("dlr2_der4"), frame)
    assert m["rating_by_weather_pct"] == pytest.approx(50.0)
    assert m["rating_by_cap_pct"] == pytest.approx(50.0)
    assert m["rating_by_equipment_pct"] == pytest.approx(0.0)


def test_comparison_gives_the_change_in_unit_and_in_per_cent():
    cfg, ref = _compared(_frame(4, static=True, curtailed_total_mw=[5.0] * 4,
                                p_WF_1_mw=[5.0] * 4))
    tables = report.summary_tables(cfg, _frame(4, curtailed_total_mw=[2.0] * 4,
                                               p_WF_1_mw=[8.0] * 4), ref)
    headline = tables["HEADLINE: DLR VS STATIC"].set_index("Metric")
    assert list(headline.columns) == ["Unit", "Static", "DLR", "Change", "Change %"]
    assert headline.loc["Mean operative rating", "Change"] == 390
    assert headline.loc["Mean operative rating", "Change %"] == pytest.approx(50.0)
    cost = tables["TECHNICAL COST"].set_index("Metric")
    assert cost.loc["Curtailed energy", "Static"] == pytest.approx(5.0)
    assert cost.loc["Curtailed energy", "DLR"] == pytest.approx(2.0)
    assert cost.loc["Curtailed energy", "Change"] == pytest.approx(-3.0)
    assert cost.loc["Curtailed energy", "Change %"] == pytest.approx(-60.0)
    # A share is already a percentage: its change is in points, not per cent.
    assert cost.loc["Curtailed share of available", "Change %"] is None


def test_no_percentage_against_a_static_value_that_rounds_to_zero():
    cfg, ref = _compared(_frame(4, static=True, q_exceedance_mvar=[0.01, 0.0, 0.0, 0.0]))
    dlr = _frame(4, q_exceedance_mvar=[10.0] * 4)
    cost = report.summary_tables(cfg, dlr, ref)["TECHNICAL COST"].set_index("Metric")
    row = cost.loc[f"Reactive outside ±{cfg.q_window_mvar:g} MVAr"]
    assert row["Change"] == pytest.approx(10.0 - 0.0025, abs=0.05)
    assert row["Change %"] is None


def test_without_a_reference_the_tables_show_the_run_alone():
    tables = report.summary_tables(build_config("dlr2_der4"), _frame(8))
    assert "HEADLINE" in tables and "HEADLINE: DLR VS STATIC" not in tables
    assert list(tables["HEADLINE"].columns) == ["Metric", "Unit", "DLR"]
    assert "RATING SET BY" in tables and "OPERATIVE AMPACITY BY MONTH" in tables


def test_a_static_run_has_no_weather_tables():
    tables = report.summary_tables(build_config("static_der4"), _frame(8, static=True))
    assert list(tables["HEADLINE"].columns) == ["Metric", "Unit", "Static"]
    assert "RATING SET BY" not in tables
    assert "OPERATIVE AMPACITY BY MONTH" not in tables


def test_monthly_ampacity_splits_the_window_by_calendar_month():
    idx_frame = _frame(4, rating_Z1_a=[1000.0, 1100.0, 1170.0, 1170.0])
    idx_frame.index = idx_frame.index[:2].append(
        idx_frame.index[2:] + pd.Timedelta(days=31))
    stats = report.monthly_ampacity(idx_frame)
    assert [p.strftime("%Y-%m") for p in stats.index] == ["2024-01", "2024-02"]
    assert stats["rating_mean"].tolist() == pytest.approx([1050.0, 1170.0])
    assert stats["rating_min"].tolist() == pytest.approx([1000.0, 1170.0])
    assert stats["weather_max"].tolist() == pytest.approx([2000.0, 2000.0])


def test_duration_points_run_from_the_maximum_to_the_minimum():
    frame = _frame(5, i_Z1_a=[100.0, 200.0, 300.0, 400.0, 500.0], i_Z2_a=[0.0] * 5)
    points = report.duration_points(frame).set_index("pct")
    assert points.loc[0, "current_a"] == pytest.approx(500.0)
    assert points.loc[50, "current_a"] == pytest.approx(300.0)
    assert points.loc[100, "current_a"] == pytest.approx(100.0)


def test_summary_csv_is_a_sequence_of_titled_tables(tmp_path):
    cfg, ref = _compared(_frame(8, static=True))
    path = tmp_path / "summary.csv"
    report.write_tables(report.summary_tables(cfg, _frame(8), ref), path)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "RUN"
    assert "HEADLINE: DLR VS STATIC" in lines and "TECHNICAL COST" in lines
    title = lines.index("TECHNICAL COST")
    assert lines[title - 1] == "", "tables are separated by a blank line"
    assert lines[title + 1] == "Metric,Unit,Static,DLR,Change,Change %"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf"), "spreadsheets need the BOM for €"


def test_summary_text_puts_static_and_dlr_side_by_side():
    cfg, ref = _compared(_frame(8, static=True))
    text = report.summary_text(cfg, _frame(8), ref)
    assert "compared with the static rating" in text
    assert "TECHNICAL COST" in text and "Static" in text and "DLR" in text
    assert ref.cfg.stem in text
    assert all(len(line) <= 80 for line in text.splitlines()), "fits an 80-column console"
