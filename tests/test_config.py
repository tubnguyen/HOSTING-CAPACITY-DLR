"""Configuration, presets and validation."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from corridor_sim import constants as C
from corridor_sim.config import (
    DER_NAMES,
    PRESETS,
    build_config,
    from_record,
    normalise_der,
    parse_args,
    to_argv,
    to_record,
)


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
    {"energy_price_eur_mwh": -1.0},
    {"dlr_wind_factor": 0.0},
    {"dlr_wind_factor": 1.5},
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


def test_no_rating_cap_flag_is_read_from_the_parser():
    """argparse accepts an unambiguous prefix, so the flag cannot be found by text.

    Searching argv for the literal flag missed --no-rating and silently kept
    the default cap, and let a conflicting --dlr-cap-ratio pass unnoticed.
    """
    for argv in (["--no-rating-cap"], ["--no-rating"]):
        cfg, _ = parse_args(argv)
        assert cfg.dlr_cap_ratio is None, argv
    custom, _ = parse_args(["--dlr-cap-ratio", "1.3"])
    assert custom.dlr_cap_ratio == pytest.approx(1.3)
    default, _ = parse_args([])
    assert default.dlr_cap_ratio == C.DLR_CAP_RATIO
    with pytest.raises(SystemExit):
        parse_args(["--dlr-cap-ratio", "1.3", "--no-rating-cap"])


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


def test_a_run_reports_itself_alone_unless_told_to_compare():
    """The static case is slow to simulate, so it is only run when asked for."""
    _, options = parse_args([])
    assert options.plots and not options.compare
    _, options = parse_args(["--compare", "--no-plots"])
    assert not options.plots and options.compare


def test_defaults_droop_45_degree_bearings_and_a_2x_cap():
    cfg, _ = parse_args([])
    assert cfg.control_mode == "droop" and cfg.pv_mode == "droop"
    assert cfg.storage_q_mode == "droop"
    assert all(PRESETS[name]["control_mode"] == "droop" for name in PRESETS)
    assert cfg.zone_azimuth_deg == {"Z1": 45.0, "Z2": 45.0}
    assert cfg.rating_cap_a == pytest.approx(2 * cfg.static_rating_a)
    assert cfg.equipment_rating_a > cfg.rating_cap_a, "the cap, not the switchgear, limits"


def test_wind_factor_from_the_command_line():
    cfg, _ = parse_args(["--wind-factor", "0.6"])
    assert cfg.dlr_wind_factor == pytest.approx(0.6)
    default, _ = parse_args([])
    assert default.dlr_wind_factor == C.DLR_WIND_FACTOR


def test_prices_for_the_technical_cost_can_be_set():
    cfg, _ = parse_args(["--energy-price", "80", "--reactive-price", "2.5"])
    assert cfg.energy_price_eur_mwh == pytest.approx(80.0)
    assert cfg.reactive_price_eur_mvarh == pytest.approx(2.5)
    default, _ = parse_args([])
    assert default.energy_price_eur_mwh == C.ENERGY_PRICE_EUR_MWH


def test_a_saved_record_rebuilds_the_same_configuration():
    """The metrics file records the run's settings; reading them back must be exact."""
    cfg = build_config("dlr2_der4_bess", days=3, label="case", export_cap_mw=200.0,
                       der_enabled=["WF_1", "PV_1"], azimuth_z1_deg=45.0)
    assert from_record(json.loads(json.dumps(to_record(cfg)))) == cfg


def test_a_record_from_another_version_still_loads():
    record = to_record(build_config("dlr1_der2"))
    record["a_setting_from_the_future"] = 1
    del record["energy_price_eur_mwh"]
    assert from_record(record).energy_price_eur_mwh == C.ENERGY_PRICE_EUR_MWH


def test_conductor_labels_name_the_builds():
    assert build_config(conductor="single").conductor_label == "1-Duck"
    assert build_config(conductor="twin").conductor_label == "2-Duck"


# ── Added plants and loads ───────────────────────────────────────────────────
def test_added_plants_and_loads_are_named_after_their_kind():
    cfg = build_config(added_der=["wind:50@sub_c", "Solar:30@PCC", "wind:12.5@MID_A_C"],
                       added_loads=["80@MID_C_PCC", "20@SUB_A:0.95"])
    assert [(p.name, p.kind, p.mw, p.at) for p in cfg.added_der] == [
        ("WF_4", "wind", 50.0, "SUB_C"), ("PV_2", "solar", 30.0, "PCC"),
        ("WF_5", "wind", 12.5, "MID_A_C")]
    assert [(q.name, q.mw, q.at, q.pf) for q in cfg.added_loads] == [
        ("LOAD_1", 80.0, "MID_C_PCC", 1.0), ("LOAD_2", 20.0, "SUB_A", 0.95)]
    assert cfg.units == DER_NAMES + ("WF_4", "PV_2", "WF_5")
    assert cfg.n_der == 7 and cfg.der_fleet_mw == pytest.approx(C.FLEET_MW + 92.5)
    assert cfg.unit_bus["WF_4"] == "SUB_C" and cfg.unit_bus["WF_3"] == "SUB_A"
    assert cfg.unit_mode("PV_2") == cfg.pv_mode and cfg.unit_mode("WF_5") == cfg.control_mode
    assert cfg.unit_q_limit_mvar["WF_4"] == pytest.approx(50.0 * C.TAN_PHI)
    assert cfg.midpoints_used == {"MID_A_C", "MID_C_PCC"}
    assert cfg.added_loads[1].q_mvar == pytest.approx(20.0 * C.TAN_PHI)


@pytest.mark.parametrize("spec", ["wind:50", "hydro:50@SUB_A", "wind:abc@SUB_A",
                                  "wind:50@TAP_W", "wind:-5@SUB_A", "wind:5000@SUB_A",
                                  "50@SUB_A"])
def test_a_bad_added_plant_is_refused(spec):
    with pytest.raises(ValueError):
        build_config(added_der=[spec])


@pytest.mark.parametrize("spec", ["80", "80@NOWHERE", "0@SUB_C", "80@SUB_C:1.5",
                                  "80@SUB_C:x"])
def test_a_bad_added_load_is_refused(spec):
    with pytest.raises(ValueError):
        build_config(added_loads=[spec])


@pytest.mark.parametrize("plant", [
    {"name": "SUB_A", "kind": "wind", "mw": 10, "at": "PCC"},       # would clash with a bus
    {"name": "WF_1", "kind": "wind", "mw": 10, "at": "PCC"},        # a built-in plant
    {"name": "PV_7", "kind": "wind", "mw": 10, "at": "PCC"},        # wrong prefix for wind
])
def test_an_added_plant_name_must_be_its_kind_and_a_number(plant):
    with pytest.raises(ValueError):
        build_config(added_der=[plant])


def test_added_items_change_the_run_name_but_not_a_plain_one():
    assert build_config("dlr2_der4").stem == "single_der4_droop_dlr2_bess0"
    cfg = build_config("dlr2_der4", added_der=["wind:50@SUB_C"], added_loads=["80@PCC"])
    assert cfg.stem == "single_der5_droop_dlr2_bess0_wf50-SUB_C_ld80-PCC"
    elsewhere = build_config("dlr2_der4", added_der=["wind:50@PCC"], added_loads=["80@PCC"])
    assert elsewhere.stem != cfg.stem, "runs differing in where a plant connects keep apart"


def test_the_reactive_window_grows_with_the_installed_fleet():
    assert build_config().q_window_mvar == pytest.approx(C.Q_WINDOW_FRAC * C.FLEET_MW)
    bigger = build_config(added_der=["wind:50@SUB_C"])
    assert bigger.q_window_mvar == pytest.approx(C.Q_WINDOW_FRAC * (C.FLEET_MW + 50.0))
    assert build_config(added_der=["wind:900@SUB_C"]).q_window_mvar == C.Q_WINDOW_CAP_MVAR


def test_added_items_from_the_command_line():
    cfg, _ = parse_args(["--add-der", "wind:50@SUB_C", "--add-der", "solar:20@PCC",
                         "--add-load", "80@MID_A_C:0.98", "--der", "wf_1,PV_1"])
    assert [p.spec for p in cfg.added_der] == ["wind:50@SUB_C", "solar:20@PCC"]
    assert [q.spec for q in cfg.added_loads] == ["80@MID_A_C:0.98"]
    assert cfg.active_der == ["WF_1", "PV_1", "WF_4", "PV_2"]
    assert parse_args(["--der", "none"])[0].active_der == []
    assert parse_args(["--der", "ALL"])[0].active_der == list(DER_NAMES)


def test_a_record_with_added_items_rebuilds_the_same_configuration():
    cfg = build_config("dlr2_der4_bess", added_der=["wind:50@SUB_C", "solar:12.5@MID_C_PCC"],
                       added_loads=["80@SUB_A:0.95"])
    assert from_record(json.loads(json.dumps(to_record(cfg)))) == cfg


@pytest.mark.parametrize("argv", [
    [],
    ["--preset", "dlr2_der4_bess", "--days", "3"],
    ["--conductor", "twin", "--dlr", "2", "--azimuth", "0", "--wind-factor", "0.55",
     "--der", "WF_2,PV_1", "--add-der", "wind:50@SUB_C", "--add-load", "80@PCC:0.95",
     "--storage", "--no-curtailment", "--export-cap", "200", "--start", "2024-03-01",
     "--end", "2024-03-05", "--label", "case", "--energy-price", "80", "--no-plots",
     "--compare"],
    ["--dlr", "2", "--azimuth-z1", "0", "--azimuth-z2", "90", "--no-rating-cap",
     "--no-equipment-limit", "--der", "none", "--reactive-price", "2.5"],
    ["--dlr-cap-ratio", "1.3", "--control", "cosphi", "--out", "elsewhere"],
    ["--storage-at", "MID_C_PCC", "--add-der", "solar:30@SUB_A"],
])
def test_the_printed_command_reproduces_the_run(argv):
    """to_argv is the inverse of parse_args, so the interactive mode can print it."""
    cfg, options = parse_args(argv)
    again, again_options = parse_args(to_argv(cfg, options))
    assert again == cfg
    assert again_options == options


def test_a_default_run_needs_no_options():
    cfg, options = parse_args([])
    assert to_argv(cfg, options) == []


def test_the_battery_can_be_placed_at_any_connection_point_or_tap_b():
    for point in C.STORAGE_POINTS:
        cfg, _ = parse_args(["--storage-at", point.lower()])
        assert cfg.storage_enabled and cfg.storage_bus == point
    assert build_config(storage_enabled=True).storage_bus == "TAP_B"
    with pytest.raises(SystemExit):
        parse_args(["--storage-at", "TAP_W"])
    with pytest.raises(ValueError, match="storage_at"):
        build_config(storage_at="SUB_B")


def test_a_moved_battery_names_its_place_in_the_run_name():
    assert build_config("dlr2_der4_bess").stem == "single_der4_droop_dlr2_bess1"
    moved = build_config("dlr2_der4_bess", storage_at="SUB_A")
    assert moved.stem == "single_der4_droop_dlr2_bess1-SUB_A"


def test_a_switched_off_battery_stays_at_tap_b():
    """Its place only matters when it is connected, so it cannot change the network."""
    cfg = build_config(storage_at="MID_A_C")
    assert not cfg.storage_enabled and cfg.storage_bus == "TAP_B"
    assert cfg.midpoints_used == set()
    assert build_config(storage_at="MID_A_C", storage_enabled=True).midpoints_used == {"MID_A_C"}
