"""IEEE 738 thermal model."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from corridor_sim import constants as C
from corridor_sim import dlr
from corridor_sim.config import build_config

REF = C.STATIC_REF_CONDITIONS


def test_model_reproduces_the_static_rating():
    """Mode 0 and modes 1-2 must be the same physics under different weather."""
    for conductor in C.CONDUCTOR_OPTIONS:
        cfg = build_config(conductor=conductor)
        check = dlr.calibration(cfg)
        assert abs(check["deviation_pct"]) < 0.5, (conductor, check)


def test_ampacity_rises_with_wind():
    previous = 0.0
    for wind in (0.5, 1.0, 2.0, 5.0, 10.0):
        current, _ = dlr.ampacity(10.0, wind, 90.0, 0.0)
        assert current > previous
        previous = current


def test_ampacity_falls_with_air_temperature_and_sun():
    warm, _ = dlr.ampacity(30.0, 2.0, 90.0, 0.0)
    cold, _ = dlr.ampacity(-10.0, 2.0, 90.0, 0.0)
    assert cold > warm

    sunny, _ = dlr.ampacity(20.0, 2.0, 90.0, 1000.0)
    shaded, _ = dlr.ampacity(20.0, 2.0, 90.0, 0.0)
    assert shaded > sunny


def test_perpendicular_wind_cools_best():
    parallel, _ = dlr.ampacity(20.0, 5.0, 0.0, 0.0)
    perpendicular, _ = dlr.ampacity(20.0, 5.0, 90.0, 0.0)
    assert perpendicular > parallel


@pytest.mark.parametrize("phi,expected", [(0, 0), (90, 90), (180, 0), (270, 90),
                                          (135, 45), (-30, 30)])
def test_attack_angle_folds_onto_the_first_quadrant(phi, expected):
    assert dlr._normalise_attack_angle(phi) == pytest.approx(expected)


def test_forward_and_inverse_solves_agree():
    """The current a rating allows must heat the conductor to exactly the
    design temperature the rating was computed at."""
    for t_air, wind, phi, ghi in [(10.0, 2.0, 90.0, 0.0), (25.0, 0.6, 45.0, 800.0),
                                  (-15.0, 8.0, 70.0, 0.0)]:
        current, _ = dlr.ampacity(t_air, wind, phi, ghi, t_cond_max_c=C.T_COND_MAX_C)
        reached = dlr.conductor_temperature(current, t_air, wind, phi, ghi)
        assert reached == pytest.approx(C.T_COND_MAX_C, abs=0.2)


def test_conductor_temperature_never_below_ambient():
    assert dlr.conductor_temperature(0.0, 12.0, 3.0, 90.0, 0.0) == pytest.approx(12.0)


def test_adverse_weather_can_derate_below_static():
    """Hot, sunny, still air with the wind along the line is a real derating."""
    cfg = build_config(conductor="single")
    current, _ = dlr.bundle_ampacity(cfg, 35.0, 0.4, 0.0, 1000.0)
    assert current < cfg.static_rating_a


def test_bundle_scales_with_conductor_count():
    single = build_config(conductor="single")
    twin = build_config(conductor="twin")
    a, _ = dlr.bundle_ampacity(single, 5.0, 3.0, 90.0, 100.0)
    b, _ = dlr.bundle_ampacity(twin, 5.0, 3.0, 90.0, 100.0)
    assert b == pytest.approx(2.0 * a)


def test_log_law_reduces_speed_toward_the_ground():
    at_conductor = dlr.log_law(10.0, 100.0, 15.0, 0.30, 0.0)
    assert 0.0 < at_conductor < 10.0
    assert dlr.log_law(10.0, 100.0, 100.0, 0.30, 0.0) == pytest.approx(10.0)


def test_log_law_rejects_an_invalid_geometry():
    with pytest.raises(ValueError):
        dlr.log_law(10.0, 100.0, 15.0, roughness_m=20.0)


def test_static_mode_ignores_weather():
    cfg = build_config(conductor="single", dlr_mode=0)
    limits, diag, source = dlr.operative_limits(cfg, None, None)
    assert source == "static"
    assert all(v == cfg.static_rating_a for v in limits.values())
    assert all(math.isnan(diag[z]["qc_wm"]) for z in limits)


def test_declared_static_rating_matches_the_conductor_datasheet():
    """The static rating is derived, not asserted: it is what the model returns
    at the conditions it is declared for, for a real published conductor."""
    assert C.COND_R20_OHM_PER_KM == pytest.approx(0.0851)   # Al/St 340/30, DIN 48204
    assert C.COND_DIAMETER_M == pytest.approx(0.025)
    assert build_config(conductor="single").static_rating_a == 780.0


# ── Ceilings above the heat balance ──────────────────────────────────────────
def _cold_windy_row():
    return pd.Series({"t_air_c": -12.0, "ghi_wm2": 0.0, "wind_ms": 11.0,
                      "phi_Z1_deg": 88.0, "phi_Z2_deg": 74.0})


def test_weather_alone_would_exceed_what_the_scheme_may_use():
    """The premise of the cap: the conductor is not the binding element."""
    cfg = build_config("dlr2_der4")
    operative, terms = dlr.rating(cfg, _cold_windy_row(), "Z1")
    assert terms["weather_a"] > cfg.static_rating_a * 2.0
    assert operative < terms["weather_a"]


@pytest.mark.parametrize("kwargs,expected", [
    ({}, "cap"),                                   # 1.5x static binds first
    ({"dlr_cap_ratio": None}, "equipment"),        # then the substation plant
    ({"conductor": "twin"}, "equipment"),          # twin outruns its switchgear
    ({"dlr_cap_ratio": None, "equipment_limit": False}, "weather"),
])
def test_the_binding_ceiling_is_recorded(kwargs, expected):
    """Which limit bound is a study result, not an implementation detail."""
    cfg = build_config("dlr2_der4", **kwargs)
    _, terms = dlr.rating(cfg, _cold_windy_row(), "Z1")
    assert terms["binding"] == expected


def test_operative_rating_is_the_lowest_ceiling():
    cfg = build_config("dlr2_der4")
    operative, terms = dlr.rating(cfg, _cold_windy_row(), "Z1")
    assert operative == pytest.approx(
        min(terms["weather_a"], cfg.rating_cap_a, cfg.equipment_rating_a))


def test_a_cap_never_derates_below_the_static_rating():
    """Switching DLR on must not make the line worse than leaving it off."""
    row = pd.Series({"t_air_c": 35.0, "ghi_wm2": 1000.0, "wind_ms": 0.3,
                     "phi_Z1_deg": 5.0, "phi_Z2_deg": 5.0})
    for name in ("dlr1_der4", "dlr2_der4"):
        cfg = build_config(name)
        assert cfg.rating_cap_a >= cfg.static_rating_a
        operative, terms = dlr.rating(cfg, row, "Z1")
        # Adverse weather may still derate below static - that is real physics -
        # but it must be the weather doing it, never the ceiling.
        if operative < cfg.static_rating_a:
            assert terms["binding"] == "weather"


def test_static_mode_is_untouched_by_the_ceilings():
    """Mode 0 is already at nameplate, so nothing above it can bind."""
    cfg = build_config("static_der4")
    limits, diag, source = dlr.operative_limits(cfg, None, None)
    assert source == "static"
    assert all(v == cfg.static_rating_a for v in limits.values())
    assert all(diag[z]["binding"] == "static" for z in limits)


def test_calibration_ignores_the_ceilings():
    """Calibration checks the conductor model; a cap would mask a drifted one."""
    capped = dlr.calibration(build_config("dlr2_der4"))
    bare = dlr.calibration(build_config("dlr2_der4", dlr_cap_ratio=None,
                                        equipment_limit=False))
    assert capped == bare
    assert abs(capped["deviation_pct"]) < 0.5


def test_inverse_solve_flags_saturation_instead_of_returning_the_ceiling():
    """A returned 150 C must be distinguishable from a solved 150 C."""
    hot = dlr.conductor_temperature(20000.0, 30.0, 0.2, 0.0, 1000.0)
    assert dlr.conductor_temperature_saturated(hot)
    normal = dlr.conductor_temperature(600.0, 10.0, 3.0, 90.0, 0.0)
    assert not dlr.conductor_temperature_saturated(normal)


def test_current_at_its_own_rating_reports_the_design_temperature_exactly():
    """A solver artefact must not be reported as an exceedance.

    The inverse solve is a bisection, so its tolerance lands directly on the one
    metric a rating study exists to keep at zero: a current sitting exactly on
    its own rating has to come back at the design temperature, not a fraction
    above it.
    """
    for t_air, wind, phi, ghi in [(10.0, 2.0, 90.0, 0.0), (-12.0, 9.0, 85.0, 0.0),
                                  (25.0, 0.6, 45.0, 800.0), (0.0, 4.0, 30.0, 200.0)]:
        current, _ = dlr.ampacity(t_air, wind, phi, ghi, t_cond_max_c=C.T_COND_MAX_C)
        reached = dlr.conductor_temperature(current, t_air, wind, phi, ghi)
        assert reached <= C.T_COND_MAX_C + 1e-3, (t_air, wind, reached)
        assert reached == pytest.approx(C.T_COND_MAX_C, abs=1e-3)


def test_ac_resistance_exceeds_dc():
    """Skin effect is modelled; ignoring it overstates ampacity."""
    assert C.COND_AC_DC_RATIO > 1.0
    r_ac = C.conductor_resistance(20.0)
    assert r_ac == pytest.approx(C.COND_R20_OHM_PER_KM * 1e-3 * C.COND_AC_DC_RATIO)
