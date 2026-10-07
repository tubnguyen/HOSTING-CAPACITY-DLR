"""The scenario matrix runner.

This module had no tests, which is how a crash in it survived every run of the
suite and failed only in CI's smoke step. The cases below are the ones the
documented workflow actually produces: a runs/ directory that accumulates
whatever has been run into it, in whatever order.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from synthetic import result_frame

from corridor_sim import report
from corridor_sim.config import build_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_matrix  # noqa: E402


def _metrics(scenario: str, **over) -> dict:
    row = {"scenario": scenario, "conductor": "single", "dlr_mode": 2, "n_der": 4,
           "fleet_mw": 330.0, "storage": 0, "available_mwh": 1000.0,
           "delivered_mwh": 950.0, "curtailed_mwh": 50.0, "curtailed_pct": 5.0,
           "rating_mean_a": 1170.0, "rating_uplift_pct": 50.0,
           "corridor_loading_p95_pct": 80.0, "hours_corridor_over_limit": 0.0,
           "pcc_peak_mw": 200.0, "hours_over_export_cap": 0.0,
           "hours_voltage_high": 0.0, "hours_voltage_low": 0.0, "losses_mwh": 10.0,
           "converged_pct": 100.0, "reg_converged_pct": 100.0, "runtime_s": 1.0}
    row.update(over)
    return row


def _write_run(out_dir: Path, scenario: str, **over) -> None:
    d = out_dir / scenario
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{scenario}_metrics.json").write_text(
        json.dumps(_metrics(scenario, **over)), encoding="utf-8")


def test_collect_gathers_every_completed_run(tmp_path):
    _write_run(tmp_path, "static_der4", dlr_mode=0, curtailed_pct=24.0)
    _write_run(tmp_path, "dlr2_der4")
    table = run_matrix.collect(tmp_path)
    assert set(table["scenario"]) == {"static_der4", "dlr2_der4"}


def test_a_run_made_by_the_plain_cli_does_not_break_the_matrix(tmp_path):
    """The regression that broke CI on every commit.

    `corridor-sim --preset dlr2_der4_bess` names its output directory from the
    settings it ran with, not from the preset it was given. The README's
    quickstart runs exactly that before the matrix, so the matrix has to cope
    with a directory whose name is not a preset - and it used to look the name
    up in the preset table and raise KeyError after the whole matrix had run.
    """
    _write_run(tmp_path, "dlr2_der4")
    _write_run(tmp_path, "single_der4_droop_dlr2_bess1", storage=1)
    table = run_matrix.collect(tmp_path)
    assert "single_der4_droop_dlr2_bess1" in set(table["scenario"])
    for _, row in table.iterrows():
        cfg = run_matrix.config_for(row)          # must not raise
        assert cfg.stem == row["scenario"]


def test_a_run_made_with_an_arbitrary_label_is_also_fine(tmp_path):
    _write_run(tmp_path, "my_own_study_2031", conductor="twin", dlr_mode=0)
    row = run_matrix.collect(tmp_path).iloc[0]
    cfg = run_matrix.config_for(row)
    assert cfg.conductor == "twin" and cfg.dlr_mode == 0


def test_config_for_reads_the_metrics_not_the_preset_table(tmp_path):
    """Figures must describe the run that happened, not a same-named preset."""
    _write_run(tmp_path, "static_der4", conductor="twin", dlr_mode=1, storage=1)
    cfg = run_matrix.config_for(run_matrix.collect(tmp_path).iloc[0])
    assert (cfg.conductor, cfg.dlr_mode, cfg.storage_enabled) == ("twin", 1, True)


def test_a_half_written_result_is_skipped_not_fatal(tmp_path):
    _write_run(tmp_path, "dlr2_der4")
    broken = tmp_path / "interrupted"
    broken.mkdir()
    (broken / "interrupted_metrics.json").write_text("{not json", encoding="utf-8")
    partial = tmp_path / "crashed"
    partial.mkdir()
    (partial / "crashed_metrics.json").write_text(
        json.dumps({"scenario": "crashed", "status": "failed"}), encoding="utf-8")
    table = run_matrix.collect(tmp_path)
    assert list(table["scenario"]) == ["dlr2_der4"]


def test_empty_directory_says_so(tmp_path):
    with pytest.raises(FileNotFoundError, match="no metrics"):
        run_matrix.collect(tmp_path)


def test_build_outputs_writes_the_comparison_table(tmp_path):
    out, figs = tmp_path / "runs", tmp_path / "figures"
    _write_run(out, "static_der4", dlr_mode=0, curtailed_pct=24.0)
    _write_run(out, "dlr2_der4", curtailed_pct=1.0)
    _write_run(out, "single_der4_droop_dlr2_bess1", storage=1)
    table = run_matrix.build_outputs(out, figs, redraw=False)
    assert (out / "matrix_summary.csv").exists()
    assert (figs / "curtailment_matrix.png").exists()
    assert len(table) == 3


def test_every_preset_the_matrix_would_run_is_buildable():
    """--only takes preset names, so they all have to resolve."""
    for name in run_matrix.PRESETS:
        assert run_matrix.build_config(name, label=name).stem == name


def test_table_columns_exist_in_real_metrics():
    """TABLE_COLUMNS is a hand-written list; it drifts silently otherwise."""
    produced = set(report.metrics(build_config("dlr2_der4"), result_frame(1)))
    declared = set(run_matrix.TABLE_COLUMNS) - {"scenario"}
    assert declared <= produced, f"table columns not produced by metrics(): {declared - produced}"


def test_dlr_runs_are_paired_with_the_static_run_that_shares_their_key():
    table = pd.DataFrame([
        {"scenario": "static_der4", "dlr_mode": 0, "static_key": "a"},
        {"scenario": "dlr1_der4", "dlr_mode": 1, "static_key": "a"},
        {"scenario": "dlr2_der4", "dlr_mode": 2, "static_key": "a"},
        {"scenario": "dlr2_der2", "dlr_mode": 2, "static_key": "b"},
        {"scenario": "old_run", "dlr_mode": 2, "static_key": None},
    ])
    assert run_matrix.static_partners(table) == {"dlr1_der4": "static_der4",
                                                 "dlr2_der4": "static_der4"}


def test_build_outputs_compares_each_dlr_run_with_its_static_partner(tmp_path):
    """Matrix runs are made without a comparison and paired once all have finished."""
    out = tmp_path / "runs"
    for preset, static in (("static_der4", True), ("dlr2_der4", False)):
        cfg = build_config(preset, days=1, label=preset, out_dir=out)
        report.write(cfg, result_frame(8, static=static), out / preset)
    table = run_matrix.build_outputs(out, tmp_path / "figures", redraw=False, make_plots=False)

    summary = (out / "dlr2_der4" / "dlr2_der4_summary.csv").read_text(encoding="utf-8-sig")
    assert "HEADLINE: DLR VS STATIC" in summary and "static_der4" in summary
    row = table.set_index("scenario").loc["dlr2_der4"]
    assert row["static_reference"] == "static_der4"
    assert "config" not in table.columns, "a run's settings are not a table column"
