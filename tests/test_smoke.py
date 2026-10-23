"""End-to-end runs over a short window."""
from __future__ import annotations

import json

import pytest

from corridor_sim import constants as C
from corridor_sim import dataio, network, plots, report, simulate
from corridor_sim.cli import main, run_scenario
from corridor_sim.config import build_config
from corridor_sim.reference import static_config

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
        assert (result[f"t_cond_{zone}_c"] <= cfg.t_cond_max_c + C.T_COND_TOLERANCE_C).all()


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
    assert set(paths) == {"timeseries", "violations", "metrics", "summary_csv"}
    assert not (tmp_path / cfg.stem / f"{cfg.stem}_summary.txt").exists()
    meta = json.loads(paths["metrics"].read_text())
    assert meta["scenario"] == cfg.stem
    assert meta["config"]["dlr_mode"] == 2 and meta["static_key"]
    figures = {f.name for f in (tmp_path / cfg.stem / "figures").glob("*.png")}
    for name in ("current", "duration", "power", "segments", "voltage",
                 "rating_drivers", "storage"):
        assert f"{cfg.stem}_{name}.png" in figures
    assert f"{cfg.stem}_cost.png" not in figures, "the cost figure was retired"
    assert all(f.stat().st_size > 5000 for f in (tmp_path / cfg.stem / "figures").glob("*.png"))


def test_summary_tables_and_violations(full_run):
    cfg, result = full_run
    tables = report.summary_tables(cfg, result)
    for title in ("RUN", "HEADLINE", "CORRIDOR CURRENT", "CORRIDOR POWER FLOW",
                  "GENERATION BY PLANT", "GRID INTERFACE (PCC)", "VOLTAGE",
                  "TRANSFORMER LOADING", "TECHNICAL COST", "RATING SET BY", "PERIOD MEANS",
                  "CURRENT DURATION", "CHECKS", "STORAGE"):
        assert title in tables and not tables[title].empty, title
    report.violations(result)          # must not raise on a clean window
    assert "curtailed" in report.summary_text(cfg, result).lower()


def test_the_current_from_all_plants_is_what_curtailment_starts_from(full_run):
    """Before curtailment the plants produce at least what the corridor carries."""
    _, result = full_run
    plant = report.plant_current(result)
    carried = report.carried_current(result)
    untouched = result["curtailed_total_mw"] < 1e-9
    assert plant[untouched].to_numpy() == pytest.approx(carried[untouched].to_numpy())
    assert plant.max() >= carried.max() - 1e-6


def test_retired_figures_are_removed_when_a_run_is_drawn_again(full_run, tmp_path):
    """A folder never mixes two versions: the cost figure goes with the rest."""
    cfg, result = full_run
    for name in ("rating", "cost"):
        (tmp_path / f"{cfg.stem}_{name}.png").write_bytes(b"left by an earlier version")
    made = plots.run_figures(cfg, result, tmp_path)
    assert all(path.stat().st_size > 5000 for path in made)
    for name in ("rating", "cost"):
        assert not (tmp_path / f"{cfg.stem}_{name}.png").exists()


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


@pytest.fixture(scope="module")
def added_run(tmp_path_factory):
    """Plants and a load added at the connection points, at the static rating.

    Two hours rather than six: at the static rating the corridor is curtailed
    on every step, and that search is what a short window is short of.
    """
    cfg = build_config("static_der4", days=None, start="2024-03-01",
                       end="2024-03-01T02:00:00", out_dir=tmp_path_factory.mktemp("added"),
                       added_der=["wind:60@PCC", "solar:20@MID_A_C"],
                       added_loads=["40@MID_C_PCC"])
    metrics, paths = run_scenario(cfg, make_plots=True, progress=False)
    from corridor_sim.reference import load_timeseries
    return cfg, metrics, paths, load_timeseries(paths["timeseries"])


def test_a_run_with_added_plants_and_loads_converges_and_balances(added_run):
    cfg, _, _, result = added_run
    assert result["converged"].all() and result["reg_converged"].all()
    delivered = simulate.realised_generation(result)
    assert (delivered + result["curtailed_total_mw"]).to_numpy() == pytest.approx(
        result["available_total_mw"].to_numpy(), abs=1e-6)
    assert set(simulate.units_in(result)) == set(cfg.units)
    assert (result["p_LOAD_1_mw"] == 40.0).all()
    # Both midpoints are in use, so both segments they sit on are in halves.
    for line in ("CORR_PV_W_1", "CORR_PV_W_2", "CORR_C_B_1", "CORR_C_B_2"):
        assert f"i_{line}_a" in result.columns
    assert "i_CORR_PV_W_a" not in result.columns and "v_MID_C_PCC_pu" in result.columns


def test_a_plant_at_the_pcc_is_not_cut_when_the_corridor_alone_binds(added_run):
    _, _, _, result = added_run
    alone = ((result["curtail_cause"] == "corridor")
             & ~result["binding_all_before"].str.contains("L4|L1", regex=True))
    assert alone.any(), "the window has to curtail for the corridor alone to test this"
    assert result.loc[alone, "curtailed_WF_1_mw"].sum() > 0.0
    assert result.loc[alone, "curtailed_WF_4_mw"].sum() == 0.0
    assert (result["i_Z1_a"] <= result["rating_Z1_a"] + 1.0).all()
    assert (result["i_Z2_a"] <= result["rating_Z2_a"] + 1.0).all()


def test_every_plant_cut_gives_up_the_same_share_of_its_output(added_run):
    """Proportional to instantaneous output, step by step."""
    cfg, _, _, result = added_run
    cut = result[result["curtailed_total_mw"] > 0]
    assert len(cut), "the window has to curtail to test this"
    for unit in cfg.units:
        available = cut[f"available_{unit}_mw"]
        curtailed = cut[f"curtailed_{unit}_mw"]
        taken = curtailed > 0
        share = curtailed[taken] / available[taken]
        assert share.to_numpy() == pytest.approx(cut.loc[taken, "curtail_fraction"].to_numpy())


def test_a_run_with_added_items_writes_every_output(added_run):
    cfg, metrics, paths, _ = added_run
    assert metrics["added_der_mw"] == 80.0 and metrics["added_load_mw"] == 40.0
    summary = paths["summary_csv"].read_text(encoding="utf-8-sig")
    assert "ADDED LOADS" in summary and "LOAD_1,MID_C_PCC" in summary
    assert "WF_4,wind,PCC" in summary and "T_WF_4" in summary
    assert "TAP_PV to MID_A_C" in summary and "MID_C_PCC to TAP_B" in summary
    figures = {f.name for f in (paths["summary_csv"].parent / "figures").glob("*.png")}
    for name in ("current", "duration", "power", "segments", "voltage"):
        assert f"{cfg.stem}_{name}.png" in figures
    meta = json.loads(paths["metrics"].read_text())
    assert meta["config"]["added_der"][0] == {"name": "WF_4", "kind": "wind", "mw": 60.0,
                                              "at": "PCC"}
