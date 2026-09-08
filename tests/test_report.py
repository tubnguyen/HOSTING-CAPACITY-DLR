"""Derived metrics.

A metric that is wrong is worse than one that is missing: it is read, quoted
and acted on. These check the arithmetic of the headline numbers directly,
against hand-countable inputs.
"""
from __future__ import annotations

import pandas as pd
import pytest

from corridor_sim import constants as C
from corridor_sim import report
from corridor_sim.config import build_config


def _frame(n: int, **columns) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    base = {
        "converged": [True] * n, "reg_converged": [True] * n,
        "available_total_mw": [10.0] * n, "curtailed_total_mw": [0.0] * n,
        "p_WF_1_mw": [10.0] * n, "p_WF_2_mw": [0.0] * n,
        "p_WF_3_mw": [0.0] * n, "p_PV_1_mw": [0.0] * n,
        "loading_Z1_pct": [50.0] * n, "loading_Z2_pct": [50.0] * n,
        "rating_Z1_a": [1170.0] * n, "rating_Z2_a": [1170.0] * n,
        "rating_Z1_weather_a": [2000.0] * n, "rating_Z2_weather_a": [2000.0] * n,
        "rating_headroom_limited": [0] * n,
        "t_cond_Z1_c": [40.0] * n, "t_cond_Z2_c": [40.0] * n,
        "pcc_p_mw": [100.0] * n, "viol_L1": [0] * n, "viol_L2": [0] * n,
        "q_within_window": [1] * n, "loss_line_mw": [1.0] * n, "loss_trafo_mw": [0.5] * n,
        "oltc_operations": [0] * n, "reactor_operations": [0] * n,
        "oltc_loop_moves": [0] * n, "reactor_loop_moves": [0] * n,
        "q_tracking_error_mvar": [0.1] * n, "curtail_cause": ["none"] * n,
    }
    base.update(columns)
    return pd.DataFrame(base, index=idx)


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
    assert "hours ceiling binds" in text
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
