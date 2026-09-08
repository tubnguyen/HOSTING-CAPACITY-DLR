"""End-to-end runs over a short window."""
from __future__ import annotations

import json

import pytest

from corridor_sim import dataio, network, plots, report, simulate
from corridor_sim.cli import main, run_scenario
from corridor_sim.config import build_config

HOURS = 6


def _short(preset, **overrides):
    return build_config(preset, days=None, start="2024-03-01",
                        end=f"2024-03-01T{HOURS:02d}:00:00", **overrides)


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    cfg = _short("dlr2_der4_bess", out_dir=tmp_path_factory.mktemp("runs"))
    net, buses = network.build(cfg)
    result = simulate.run(cfg, net, buses, dataio.load_inputs(cfg), progress=False)
    return cfg, result


def test_every_step_converges(full_run):
    cfg, result = full_run
    assert len(result) == HOURS * 4
    assert result["converged"].all(), "no step should fail on a normal window"
    assert result["reg_converged"].all()


def test_energy_balance_is_consistent(full_run):
    """Delivered plus curtailed must equal what was available."""
    _, result = full_run
    delivered = simulate.realised_generation(result)
    total = delivered + result["curtailed_total_mw"]
    assert total.values == pytest.approx(result["available_total_mw"].values, abs=1e-6)


def test_reported_reactive_power_is_a_solved_value(full_run):
    _, result = full_run
    assert (result["q_tracking_error_mvar"] <= 0.51).all()


def test_conductor_stays_within_design_temperature(full_run):
    cfg, result = full_run
    for zone in network.CORRIDOR_ZONES:
        assert (result[f"t_cond_{zone}_c"] <= cfg.t_cond_max_c + 1e-6).all()


def test_operative_rating_never_exceeds_what_the_scheme_may_use(full_run):
    """End to end: no timestep is permitted more than the ceilings allow."""
    cfg, result = full_run
    for zone in network.CORRIDOR_ZONES:
        rating = result[f"rating_{zone}_a"]
        assert (rating <= cfg.rating_cap_a + 1e-6).all()
        assert (rating <= cfg.equipment_rating_a + 1e-6).all()
        assert (rating <= result[f"rating_{zone}_weather_a"] + 1e-6).all()


def test_the_binding_ceiling_is_recorded_for_every_step(full_run):
    _, result = full_run
    for zone in network.CORRIDOR_ZONES:
        recorded = set(result[f"rating_{zone}_binding"].unique())
        assert recorded <= {"weather", "cap", "equipment", "static"}
        assert not recorded & {""}, "every step must name what bound it"


def test_corridor_current_stays_under_the_series_equipment_rating(full_run):
    """The point of modelling the equipment: nothing may exceed its nameplate."""
    cfg, result = full_run
    for zone in network.CORRIDOR_ZONES:
        assert (result[f"i_{zone}_a"].dropna() <= cfg.equipment_rating_a + 1.0).all()


def test_committed_actuator_operations_are_physically_plausible(full_run):
    """A real on-load tap changer does not operate hundreds of times a day."""
    _, result = full_run
    days = len(result) * 0.25 / 24.0
    assert result["oltc_operations"].sum() / days < 60, "tap duty implausible"
    assert (result["oltc_operations"] <= result["oltc_loop_moves"]).all()


def test_state_of_charge_stays_inside_its_limits(full_run):
    cfg, result = full_run
    soc = result["storage_soc_mwh"]
    assert (soc >= cfg.soc_min_mwh - 1e-9).all()
    assert (soc <= cfg.soc_max_mwh + 1e-9).all()


def test_no_column_is_entirely_missing(full_run):
    _, result = full_run
    empty = [c for c in result.columns if result[c].isna().all()]
    assert not empty, f"columns never populated: {empty}"


def test_static_rating_curtails_more_than_dynamic(tmp_path):
    """The headline comparison the study exists to make."""
    curtailed = {}
    for preset in ("static_der4", "dlr2_der4"):
        cfg = _short(preset, out_dir=tmp_path)
        net, buses = network.build(cfg)
        result = simulate.run(cfg, net, buses, dataio.load_inputs(cfg), progress=False)
        curtailed[preset] = report.metrics(cfg, result)["curtailed_mwh"]
    assert curtailed["static_der4"] > curtailed["dlr2_der4"]


def test_removing_the_ceilings_can_only_help(tmp_path):
    """A capped study must never deliver more than an uncapped one.

    Cheap to state and easy to get wrong: it is the property that says the cap
    is a restriction on the same model rather than a different model.
    """
    delivered = {}
    for label, over in (("capped", {}),
                        ("bare", dict(dlr_cap_ratio=None, equipment_limit=False))):
        cfg = _short("dlr2_der4", out_dir=tmp_path, label=f"ceiling_{label}", **over)
        net, buses = network.build(cfg)
        result = simulate.run(cfg, net, buses, dataio.load_inputs(cfg), progress=False)
        delivered[label] = report.metrics(cfg, result)["delivered_mwh"]
    assert delivered["bare"] >= delivered["capped"] - 1e-6


def test_input_window_beyond_the_dataset_is_refused(tmp_path):
    """The guarantee the README makes, checked against the shipped dataset."""
    cfg = build_config("dlr2_der4", start="2024-12-28", days=30, out_dir=tmp_path)
    with pytest.raises(ValueError, match="does not span"):
        dataio.load_inputs(cfg)


def test_reports_and_figures_are_written(tmp_path):
    cfg = _short("dlr2_der4_bess", out_dir=tmp_path)
    metrics, paths = run_scenario(cfg, make_plots=True, progress=False)
    for path in paths.values():
        assert path.exists() and path.stat().st_size > 0
    assert json.loads(paths["metrics"].read_text())["scenario"] == cfg.stem
    figures = list((tmp_path / cfg.stem / "figures").glob("*.png"))
    assert len(figures) >= 3
    assert all(f.stat().st_size > 5000 for f in figures)


def test_seasonal_and_violation_tables(full_run):
    cfg, result = full_run
    assert not report.seasonal(cfg, result).empty
    report.violations(result)          # must not raise on a clean window
    assert "curtailed" in report.summary_text(cfg, result).lower()


def test_command_line_entry_point(tmp_path):
    assert main(["--preset", "dlr1_der2", "--start", "2024-03-01", "--days", "1",
                 "--out", str(tmp_path), "--label", "cli_check", "--no-plots"]) == 0
    written = tmp_path / "cli_check"
    assert written.exists()
    assert (written / "cli_check_metrics.json").exists()


def test_matrix_figure(tmp_path):
    import pandas as pd
    table = pd.DataFrame([
        {"scenario": "static_der4", "conductor": "single", "dlr_mode": 0,
         "n_der": 4, "storage": 0, "curtailed_pct": 14.1},
        {"scenario": "dlr2_der4", "conductor": "single", "dlr_mode": 2,
         "n_der": 4, "storage": 0, "curtailed_pct": 0.4},
    ])
    out = tmp_path / "matrix.png"
    plots.curtailment_matrix(table, out)
    assert out.exists() and out.stat().st_size > 5000
