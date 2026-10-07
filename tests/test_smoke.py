"""End-to-end runs over a short window."""
from __future__ import annotations

import json

import pytest

from corridor_sim import dataio, network, plots, report, simulate
from corridor_sim.cli import main, run_scenario
from corridor_sim.config import build_config
from corridor_sim.reference import Reference, static_config

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
    """A real on-load tap changer does not operate hundreds of times a day.

    Measured after the first interval. The run starts with every tap at neutral,
    so the opening step carries the whole settling transient and a short window
    that includes it does not describe steady duty.
    """
    _, result = full_run
    steady = result.iloc[1:]
    days = len(steady) * 0.25 / 24.0
    rate = steady["oltc_operations"].sum() / days
    assert rate < 100, f"tap duty implausible: {rate:.0f}/day"
    assert (result["oltc_operations"] <= result["oltc_loop_moves"]).all()
    # The regression this exists for: loop writes were reported as duty and
    # overstated it by more than an order of magnitude.
    assert result["oltc_loop_moves"].sum() > result["oltc_operations"].sum()


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
    assert set(paths) == {"timeseries", "violations", "metrics", "summary_csv", "summary"}
    meta = json.loads(paths["metrics"].read_text())
    assert meta["scenario"] == cfg.stem
    assert meta["config"]["dlr_mode"] == 2 and meta["static_key"]
    figures = {f.name for f in (tmp_path / cfg.stem / "figures").glob("*.png")}
    for name in ("rating", "ampacity", "duration", "voltage", "rating_drivers", "storage"):
        assert f"{cfg.stem}_{name}.png" in figures
    assert all(f.stat().st_size > 5000 for f in (tmp_path / cfg.stem / "figures").glob("*.png"))


def test_summary_tables_and_violations(full_run):
    cfg, result = full_run
    tables = report.summary_tables(cfg, result)
    for title in ("RUN", "HEADLINE", "TECHNICAL COST", "LINE RATING", "RATING SET BY",
                  "OPERATIVE AMPACITY BY MONTH", "CURRENT DURATION", "CHECKS", "STORAGE"):
        assert title in tables and not tables[title].empty, title
    report.violations(result)          # must not raise on a clean window
    assert "curtailed" in report.summary_text(cfg, result).lower()


def test_the_cost_figure_is_drawn_only_against_a_reference(full_run, tmp_path):
    """A cost figure left from an earlier run would show a comparison never made."""
    cfg, result = full_run
    cost = tmp_path / f"{cfg.stem}_cost.png"
    plots.run_figures(cfg, result, tmp_path, Reference(static_config(cfg), result, "reused"))
    assert cost.exists() and cost.stat().st_size > 5000
    plots.run_figures(cfg, result, tmp_path)
    assert not cost.exists()


def test_a_dlr_run_is_compared_with_a_static_run_made_alongside_then_reused(tmp_path):
    """The static reference runs in a second process once, and is reused after."""
    cfg = build_config("dlr2_der4", days=None, start="2024-03-01",
                       end="2024-03-01T02:00:00", out_dir=tmp_path)
    run_scenario(cfg, make_plots=False, progress=False, compare=True)
    static = static_config(cfg)
    static_series = tmp_path / static.stem / f"{static.stem}_timeseries.csv"
    assert static_series.exists(), "the reference is written as a run of its own"
    meta = json.loads((tmp_path / cfg.stem / f"{cfg.stem}_metrics.json").read_text())
    assert meta["static_reference"] == static.stem
    summary = (tmp_path / cfg.stem / f"{cfg.stem}_summary.csv").read_text(encoding="utf-8-sig")
    assert "HEADLINE: DLR VS STATIC" in summary and static.stem in summary

    written = static_series.stat().st_mtime_ns
    run_scenario(cfg, make_plots=False, progress=False, compare=True)
    assert static_series.stat().st_mtime_ns == written, "a matching reference is reused"
    summary = (tmp_path / cfg.stem / f"{cfg.stem}_summary.csv").read_text(encoding="utf-8-sig")
    assert "(reused)" in summary


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
