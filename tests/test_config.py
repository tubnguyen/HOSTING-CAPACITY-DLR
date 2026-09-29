"""Configuration, presets and validation."""
from __future__ import annotations

import subprocess
import sys

import pytest

from corridor_sim import constants as C
from corridor_sim.config import DER_NAMES, PRESETS, build_config, normalise_der, parse_args


def test_every_preset_is_valid():
    for name in PRESETS:
        cfg = build_config(name)
        assert cfg.n_der == len(cfg.active_der)


def test_matrix_covers_every_rating_method_and_build():
    modes = {(build_config(p).conductor, build_config(p).dlr_mode)
             for p in PRESETS if p != "baseline"}
    assert ("single", 0) in modes and ("single", 1) in modes
    assert ("single", 2) in modes and ("twin", 0) in modes


def test_der_list_is_expanded_to_a_full_map():
    mapping = normalise_der(["WF_1", "PV_1"])
    assert set(mapping) == set(DER_NAMES)
    assert mapping["WF_1"] and mapping["PV_1"]
    assert not mapping["WF_2"] and not mapping["WF_3"]


def test_unknown_plant_name_is_rejected():
    with pytest.raises(ValueError, match="unknown DER"):
        normalise_der(["WF_9"])


@pytest.mark.parametrize("override", [
    {"conductor": "triple"},
    {"control_mode": "pid"},
    {"dlr_mode": 3},
    {"storage_soc_init": 1.5},
    {"wf_trafo_units": 3},
    {"roughness_m": 40.0},
    {"export_cap_basis": "guess"},
    {"dlr_cap_ratio": 0.8},
    {"days": -5},
])
def test_bad_settings_fail_fast(override):
    with pytest.raises(ValueError):
        build_config(**override)


def test_validation_survives_python_O():
    """Validation must not be assert-based.

    `python -O` strips assertions, so a validator written with them silently
    disappears under an optimisation flag and the run proceeds on a
    configuration nobody checked. Running the real interpreter is the only way
    to test this: importing with -O set does not re-strip already-loaded code.
    """
    result = subprocess.run(
        [sys.executable, "-O", "-c",
         "from corridor_sim.config import build_config; build_config(dlr_mode=99)"],
        capture_output=True, text=True)
    assert result.returncode != 0, "invalid config accepted under -O"
    assert "ValueError" in result.stderr


def test_der_enabled_cannot_be_mutated_after_validation():
    """Config is frozen; the mapping inside it must be too."""
    cfg = build_config("dlr2_der4")
    with pytest.raises(TypeError):
        cfg.der_enabled["WF_1"] = False


def test_derived_quantities():
    cfg = build_config("dlr2_der4_bess", days=10)
    assert cfg.der_fleet_mw == pytest.approx(C.FLEET_MW)
    assert cfg.n_steps == 10 * 96
    assert cfg.static_rating_a == 780.0
    assert cfg.bundle_n == 1
    assert cfg.storage_droop_active


def test_rating_ceilings_are_ordered_above_the_static_rating():
    """A cap below the static rating would derate the line on day one."""
    for name in ("dlr2_der4", "twin_der4"):
        cfg = build_config(name)
        assert cfg.rating_cap_a > cfg.static_rating_a
        assert cfg.equipment_rating_a > cfg.static_rating_a


def test_rating_ceilings_can_be_switched_off():
    bare = build_config("dlr2_der4", dlr_cap_ratio=None, equipment_limit=False)
    assert bare.rating_cap_a is None and bare.equipment_rating_a is None


def test_twin_conductor_doubles_the_rating():
    assert build_config(conductor="twin").static_rating_a == pytest.approx(
        2 * build_config(conductor="single").static_rating_a)


def test_recharge_target_covers_the_reserve_obligation():
    cfg = build_config("dlr2_der4_bess")
    deliverable = (cfg.soc_recharge_target_mwh - cfg.soc_min_mwh) * C.BESS_ETA_DIS
    assert deliverable == pytest.approx(cfg.storage_contract_mw * 1.0, rel=1e-6)


@pytest.mark.parametrize("azimuth", C.DLR_AZIMUTH_OPTIONS_DEG)
def test_every_offered_azimuth_is_accepted(azimuth):
    cfg = build_config(azimuth_z1_deg=azimuth, azimuth_z2_deg=azimuth)
    assert cfg.zone_azimuth_deg == {"Z1": azimuth, "Z2": azimuth}


@pytest.mark.parametrize("azimuth", [15.0, 110.0, -30.0, 180.0])
def test_an_azimuth_outside_the_offered_set_is_rejected(azimuth):
    with pytest.raises(ValueError, match="azimuth"):
        build_config(azimuth_z1_deg=azimuth)


def test_azimuth_flags_set_the_zone_bearings():
    both, _ = parse_args(["--azimuth", "45"])
    assert both.zone_azimuth_deg == {"Z1": 45.0, "Z2": 45.0}
    # A zone-specific flag wins over the corridor-wide one.
    mixed, _ = parse_args(["--azimuth", "0", "--azimuth-z2", "90"])
    assert mixed.zone_azimuth_deg == {"Z1": 0.0, "Z2": 90.0}
    default, _ = parse_args([])
    assert default.zone_azimuth_deg == C.DLR_ZONE_AZIMUTH_DEG
    with pytest.raises(SystemExit):
        parse_args(["--azimuth", "110"])
