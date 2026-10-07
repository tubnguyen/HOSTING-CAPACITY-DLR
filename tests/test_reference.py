"""The static reference a DLR run is compared against.

Reusing a static run is only safe if it is the run the comparison needs. A
folder name records neither the export cap nor the window nor the data, so
these check that the fingerprint does, and that the lookup refuses anything
else.
"""
from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest

from corridor_sim import reference
from corridor_sim.config import build_config
from corridor_sim.dataio import FILES


def test_a_dlr_run_and_its_static_configuration_share_a_key():
    cfg = build_config("dlr2_der4_bess", days=3)
    assert reference.static_key(cfg) == reference.static_key(reference.static_config(cfg))


@pytest.mark.parametrize("override", [
    {"dlr_mode": 1},
    {"azimuth_z1_deg": 0.0},
    {"dlr_cap_ratio": None},
    {"equipment_limit": False},
    {"label": "my_case"},
    {"energy_price_eur_mwh": 90.0},
])
def test_settings_a_static_run_ignores_leave_the_key_alone(override, tmp_path):
    base = build_config("dlr2_der4", days=3)
    assert reference.static_key(build_config("dlr2_der4", days=3, out_dir=tmp_path,
                                             **override)) == reference.static_key(base)


@pytest.mark.parametrize("override", [
    {"export_cap_mw": 200.0},
    {"days": 4},
    {"start": "2024-02-01"},
    {"der_enabled": ["WF_1", "WF_2"]},
    {"storage_enabled": True},
    {"control_mode": "cosphi"},
    {"conductor": "twin"},
])
def test_anything_that_changes_a_static_result_changes_the_key(override):
    base = reference.static_key(build_config("dlr2_der4", days=3))
    assert reference.static_key(build_config("dlr2_der4", **{"days": 3, **override})) != base


def test_the_key_follows_the_input_data_not_its_folder(tmp_path):
    cfg = build_config("dlr2_der4", days=3)
    copy = tmp_path / "data"
    copy.mkdir()
    for name in FILES.values():
        shutil.copy(cfg.data_dir / name, copy / name)
    moved = build_config("dlr2_der4", days=3, data_dir=copy)
    assert reference.static_key(moved) == reference.static_key(cfg)
    with open(copy / FILES["load"], "a", encoding="utf-8") as fh:
        fh.write("\n")
    assert reference.static_key(moved) != reference.static_key(cfg)


def test_static_configuration_names():
    unlabelled = reference.static_config(build_config("dlr2_der4_bess"))
    assert unlabelled.dlr_mode == 0
    assert unlabelled.stem == "single_der4_droop_dlr0_bess1"
    labelled = reference.static_config(build_config("dlr2_der4", label="case"))
    assert labelled.stem == "case_static"


def _saved_run(out_dir, scenario, cfg, dlr_mode=0, key=None, steps=None):
    """A finished run on disk: the metrics and time series the lookup reads."""
    folder = out_dir / scenario
    folder.mkdir(parents=True)
    meta = {"scenario": scenario, "dlr_mode": dlr_mode,
            "static_key": key or reference.static_key(cfg)}
    (folder / f"{scenario}_metrics.json").write_text(json.dumps(meta), encoding="utf-8")
    index = pd.date_range(cfg.start_ts, periods=steps or cfg.n_steps, freq="15min")
    pd.DataFrame({"converged": True, "pcc_p_mw": 1.0}, index=index).to_csv(
        folder / f"{scenario}_timeseries.csv")


def test_find_reuses_the_matching_static_run_whatever_it_is_called(tmp_path):
    cfg = build_config("dlr2_der4", days=1, out_dir=tmp_path)
    _saved_run(tmp_path, "static_der4", cfg)
    found = reference.find(cfg)
    assert found is not None and found.origin == "reused"
    assert found.cfg.stem == "static_der4" and found.cfg.dlr_mode == 0
    assert len(found.result) == cfg.n_steps


def test_find_ignores_runs_that_are_not_the_reference(tmp_path):
    cfg = build_config("dlr2_der4", days=1, out_dir=tmp_path)
    _saved_run(tmp_path, "other_settings", cfg, key="0" * 16)
    _saved_run(tmp_path, "another_dlr_run", cfg, dlr_mode=2)
    _saved_run(tmp_path, "cut_short", cfg, steps=cfg.n_steps - 4)
    broken = tmp_path / "interrupted"
    broken.mkdir()
    (broken / "interrupted_metrics.json").write_text("{not json", encoding="utf-8")
    assert reference.find(cfg) is None


def test_find_on_an_empty_or_missing_folder_finds_nothing(tmp_path):
    assert reference.find(build_config("dlr2_der4", days=1, out_dir=tmp_path / "none")) is None
