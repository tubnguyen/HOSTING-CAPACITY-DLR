"""Dynamic line rating: IEEE 738-2012 steady-state thermal model.

The forward solve fixes the conductor at its design temperature and returns the
ampacity the weather supports. The inverse solve fixes the current the network
is actually carrying and returns the temperature the conductor reaches, which
is what tells you whether a rating was genuinely safe.

Three rating modes, in increasing order of data appetite:
    0  static      nameplate ampacity, weather ignored
    1  ambient     measured air temperature and irradiance, wind held at a
                   conservative fixed low value perpendicular to the line
    2  full        measured air temperature, irradiance, wind speed and the
                   per-zone wind angle of attack

The heat balance rates the conductor. The operative rating is the lower of
that, an administrative cap on the uplift over the static rating, and the
nameplate of the substation plant in series with the line. Which of the three
binds is recorded per zone and per timestep, because "the weather allowed more
but we could not use it" is a different study result from "the weather did not
allow it".
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import constants as C

ZONES = tuple(C.DLR_ZONE_AZIMUTH_DEG)


# ── IEEE 738-2012 heat balance terms (per sub-conductor, W/m) ─────────────────
def _film_temp(t_air, t_cond):
    return (t_cond + t_air) / 2.0


def air_viscosity(t_air, t_cond):
    tf = _film_temp(t_air, t_cond)
    return 1.458e-6 * (tf + 273.0) ** 1.5 / (tf + 383.4)


def air_density(t_air, t_cond, elev_m):
    tf = _film_temp(t_air, t_cond)
    return (1.293 - 1.525e-4 * elev_m + 6.379e-9 * elev_m ** 2) / (1.0 + 0.00367 * tf)


def air_conductivity(t_air, t_cond):
    tf = _film_temp(t_air, t_cond)
    return 2.424e-2 + 7.477e-5 * tf - 4.407e-9 * tf ** 2


def reynolds(t_air, v_ms, t_cond, elev_m, diameter_m=C.COND_DIAMETER_M):
    return diameter_m * air_density(t_air, t_cond, elev_m) * v_ms / air_viscosity(t_air, t_cond)


def forced_convection(t_air, v_ms, phi_rad, t_cond, elev_m):
    """Wind-driven convective loss; IEEE 738 takes the larger of two correlations."""
    k_angle = (1.194 - np.cos(phi_rad) + 0.194 * np.cos(2 * phi_rad)
               + 0.368 * np.sin(2 * phi_rad))
    n_re = reynolds(t_air, v_ms, t_cond, elev_m)
    k_f = air_conductivity(t_air, t_cond)
    dt = t_cond - t_air
    qc_low = k_angle * (1.01 + 1.35 * n_re ** 0.52) * k_f * dt
    qc_high = k_angle * 0.754 * n_re ** 0.60 * k_f * dt
    return np.maximum(qc_low, qc_high)


def natural_convection(t_air, t_cond, elev_m, diameter_m=C.COND_DIAMETER_M):
    """Buoyancy-driven loss, the floor that applies in still air."""
    return (3.645 * air_density(t_air, t_cond, elev_m) ** 0.5
            * diameter_m ** 0.75 * np.maximum(t_cond - t_air, 0.0) ** 1.25)


def convective_loss(t_air, v_ms, phi_rad, t_cond, elev_m):
    return np.maximum(forced_convection(t_air, v_ms, phi_rad, t_cond, elev_m),
                      natural_convection(t_air, t_cond, elev_m))


def radiated_loss(t_air, t_cond, diameter_m=C.COND_DIAMETER_M):
    return (17.8 * diameter_m * C.COND_EMISSIVITY
            * (((t_cond + 273.0) / 100.0) ** 4 - ((t_air + 273.0) / 100.0) ** 4))


def solar_gain(irradiance_wm2, diameter_m=C.COND_DIAMETER_M):
    return C.COND_ABSORPTIVITY * irradiance_wm2 * diameter_m


def _normalise_attack_angle(phi_deg):
    """Fold any bearing difference onto [0, 90] degrees; 0 is parallel flow."""
    return 90.0 - np.abs((phi_deg % 180.0) - 90.0)


# ── Forward and inverse solves ───────────────────────────────────────────────
def ampacity(t_air_c, v_ms, phi_deg, irradiance_wm2, t_cond_max_c=C.T_COND_MAX_C,
             elev_m=C.DLR_SITE_ELEVATION_M):
    """Steady-state sub-conductor ampacity [A] and the heat balance behind it.

    Returns (current, terms). A zero current means the solar gain alone exceeds
    what convection and radiation can shed at the design temperature: a real
    adverse-weather derating, so it is returned rather than floored.
    """
    phi_norm = _normalise_attack_angle(phi_deg)
    phi_rad = math.radians(float(phi_norm))
    qc = convective_loss(t_air_c, v_ms, phi_rad, t_cond_max_c, elev_m)
    qr = radiated_loss(t_air_c, t_cond_max_c)
    qs = solar_gain(irradiance_wm2)
    r_ac = C.conductor_resistance(t_cond_max_c)
    current = float(np.sqrt(np.maximum((qc + qr - qs) / r_ac, 0.0)))
    terms = {"phi_norm_deg": float(phi_norm), "qc_wm": float(qc), "qr_wm": float(qr),
             "qs_wm": float(qs), "r_ohm_per_m": float(r_ac),
             "t_cond_max_c": float(t_cond_max_c)}
    return current, terms


def conductor_temperature(current_a, t_air_c, v_ms, phi_deg, irradiance_wm2,
                          elev_m=C.DLR_SITE_ELEVATION_M,
                          t_cap_c=C.T_COND_SOLVE_CAP_C):
    """Temperature [C] a sub-conductor reaches carrying `current_a`.

    Solves I^2 R(T) + qs = qc(T) + qr(T) by bisection on [t_air, t_cap].
    Returns the bracket ceiling if the balance does not close below it; use
    `conductor_temperature_saturated` to tell that case from a real answer,
    because a returned 150 C would otherwise read as a solved temperature.
    """
    if not math.isfinite(current_a):
        return float("nan")
    phi_rad = math.radians(float(_normalise_attack_angle(phi_deg)))
    qs = solar_gain(irradiance_wm2)

    def imbalance(t_cond):
        gain = current_a * current_a * C.conductor_resistance(t_cond) + qs
        loss = (convective_loss(t_air_c, v_ms, phi_rad, t_cond, elev_m)
                + radiated_loss(t_air_c, t_cond))
        return gain - loss

    lo, hi = float(t_air_c), float(t_cap_c)
    if imbalance(lo) <= 0.0:
        return lo
    if imbalance(hi) > 0.0:
        return hi                       # saturated: see the helper below
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if imbalance(mid) > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.1:
            break
    return 0.5 * (lo + hi)


def conductor_temperature_saturated(t_cond_c, t_cap_c=C.T_COND_SOLVE_CAP_C) -> bool:
    """Whether an inverse solve hit its bracket ceiling instead of converging."""
    return bool(math.isfinite(t_cond_c) and t_cond_c >= t_cap_c - 1e-9)


def bundle_ampacity(cfg, t_air_c, v_ms, phi_deg, irradiance_wm2):
    """Total ampacity [A] of the bundle, plus per-sub-conductor heat terms."""
    i_sub, terms = ampacity(t_air_c, v_ms, phi_deg, irradiance_wm2,
                            t_cond_max_c=cfg.t_cond_max_c, elev_m=cfg.site_elevation_m)
    terms["i_sub_a"] = i_sub
    return cfg.bundle_n * i_sub, terms


# ── Wind field preparation ───────────────────────────────────────────────────
def log_law(v_ref_ms, z_ref_m, z_target_m, roughness_m, displacement_m=0.0):
    """Neutral-stability log-law extrapolation between two heights."""
    z_eff = z_target_m - displacement_m
    z_ref_eff = z_ref_m - displacement_m
    if z_eff <= roughness_m or z_ref_eff <= roughness_m:
        raise ValueError("log law invalid: effective height must exceed the roughness length")
    ratio = math.log(z_eff / roughness_m) / math.log(z_ref_eff / roughness_m)
    return np.maximum(np.asarray(v_ref_ms, dtype=float) * ratio, 0.0)


def align_to_index(df: pd.DataFrame, index: pd.DatetimeIndex, name: str) -> pd.DataFrame:
    """Reindex onto the simulation grid, interpolating coarser inputs in time.

    Interpolation fills *between* observations only. Anything outside the span
    of the file is a gap in the study's inputs, not something to extend the
    edge value across, so the requested window is checked against the file's
    own first and last timestamp before any filling happens.

    Doing it the other way round - filling first and testing for NaN afterwards
    - looks like a coverage check and is not one: a forward/backward fill
    leaves no NaN to find, and the run proceeds on a flat line held over from
    the edge of the data. That is harder to notice than a crash and harder
    still to notice than zeros, because it plots as perfectly plausible weather.
    """
    if df.empty:
        raise ValueError(f"{name} is empty")
    first, last = df.index[0], df.index[-1]
    if index[0] < first or index[-1] > last:
        raise ValueError(
            f"{name} covers {first} to {last}, which does not span the requested "
            f"window {index[0]} to {index[-1]}. Supply data for the whole window "
            f"or shorten it with --start/--days.")
    out = (df.reindex(index.union(df.index))
             .interpolate(method="time", limit_area="inside")
             .reindex(index))
    if out.isna().any().any():
        gaps = sorted(out.columns[out.isna().any()])
        raise ValueError(f"{name} has gaps inside the requested window: columns {gaps}")
    return out


def prepare_weather(cfg, weather: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Resample weather to the simulation index and derive the per-zone wind field.

    Produces air temperature, irradiance, and for each rating zone the wind
    speed at conductor height and its angle of attack against that zone's mean
    bearing.
    """
    need = {"t_air_c", "u100_ms", "v100_ms", "ghi_wm2"}
    missing = need - set(weather.columns)
    if missing:
        raise KeyError(f"weather file is missing columns: {sorted(missing)}")

    w = align_to_index(weather, index, "weather")

    speed_ref = np.hypot(w["u100_ms"].to_numpy(), w["v100_ms"].to_numpy())
    # Meteorological convention: the direction the wind blows from.
    bearing = (np.degrees(np.arctan2(-w["u100_ms"].to_numpy(),
                                     -w["v100_ms"].to_numpy())) + 360.0) % 360.0
    speed = log_law(speed_ref, C.DLR_REF_HEIGHT_M, cfg.conductor_height_m,
                    cfg.roughness_m, cfg.displacement_m)

    out = pd.DataFrame(index=index)
    out["t_air_c"] = w["t_air_c"].to_numpy()
    out["ghi_wm2"] = np.clip(w["ghi_wm2"].to_numpy(), 0.0, None)
    out["wind_ms"] = speed
    out["wind_bearing_deg"] = bearing
    for zone, azimuth in C.DLR_ZONE_AZIMUTH_DEG.items():
        out[f"phi_{zone}_deg"] = _normalise_attack_angle(bearing - azimuth)
    return out


# ── Operative limits: the heat balance is not the only ceiling ───────────────
def headroom_limits(cfg) -> dict:
    """The two ceilings that sit above the conductor heat balance.

    `cap` is the administrative limit on how far a dynamic rating may exceed
    the static one; `equipment` is the nameplate of the substation plant in
    series with the line. Either can be absent (None), in which case only the
    other applies.
    """
    cap = (cfg.dlr_cap_ratio * cfg.static_rating_a
           if cfg.dlr_cap_ratio is not None else None)
    return {"cap": cap, "equipment": cfg.equipment_rating_a}


def apply_headroom(cfg, weather_a: float):
    """Lower the weather rating onto the operative one, and say what binds."""
    limits = headroom_limits(cfg)
    operative, binding = weather_a, "weather"
    for name in ("cap", "equipment"):
        value = limits[name]
        if value is not None and value < operative:
            operative, binding = value, name
    return operative, binding, limits


def _static_terms():
    nan = float("nan")
    return {"phi_norm_deg": nan, "qc_wm": nan, "qr_wm": nan, "qs_wm": nan,
            "r_ohm_per_m": nan, "i_sub_a": nan, "t_cond_max_c": nan,
            "wind_ms": nan, "phi_deg": nan, "t_air_c": nan, "ghi_wm2": nan,
            "weather_a": nan, "cap_a": nan, "equipment_a": nan,
            "binding": "static", "source": "static"}


def rating(cfg, row, zone: str):
    """Operative bundle ampacity [A] for one zone and one timestep.

    Returns the rating actually available to the network, which is the heat
    balance only while nothing above it binds first.
    """
    if cfg.dlr_mode == 0:
        return cfg.static_rating_a, _static_terms()

    t_air = float(row["t_air_c"])
    ghi = float(row["ghi_wm2"])
    if cfg.dlr_mode == 1:
        wind, phi = cfg.low_wind_ms, 90.0
    else:
        wind, phi = float(row["wind_ms"]), float(row[f"phi_{zone}_deg"])

    weather_a, terms = bundle_ampacity(cfg, t_air, wind, phi, ghi)
    operative, binding, limits = apply_headroom(cfg, weather_a)
    terms.update({"wind_ms": float(wind), "phi_deg": float(phi), "t_air_c": t_air,
                  "ghi_wm2": ghi, "source": "weather",
                  "weather_a": weather_a, "binding": binding,
                  "cap_a": limits["cap"] if limits["cap"] is not None else float("nan"),
                  "equipment_a": (limits["equipment"] if limits["equipment"] is not None
                                  else float("nan"))})
    return operative, terms


def operative_limits(cfg, weather: pd.DataFrame, ts):
    """Per-zone ampacity for one timestep; raises rather than falling back."""
    if cfg.dlr_mode == 0 or weather is None:
        terms = _static_terms()
        return {z: cfg.static_rating_a for z in ZONES}, {z: dict(terms) for z in ZONES}, "static"

    row = weather.loc[ts]
    if isinstance(row, pd.DataFrame):
        raise ValueError(f"weather index has duplicate timestamps at {ts}")

    limits, diag = {}, {}
    for zone in ZONES:
        limits[zone], diag[zone] = rating(cfg, row, zone)
        if not math.isfinite(limits[zone]):
            raise ValueError(f"non-finite rating for zone {zone} at {ts}")
    return limits, diag, "weather"


def calibration(cfg) -> dict:
    """Check the IEEE 738 model against the declared static rating.

    Evaluating the model at the reference conditions the static rating is
    quoted for should return that rating; a large deviation means the two are
    not describing the same conductor.

    Deliberately compares the *uncapped* heat balance. The cap and the series
    equipment limit sit above the conductor and would mask a model that had
    drifted away from the conductor it claims to describe.
    """
    ref = C.STATIC_REF_CONDITIONS
    modelled, _ = bundle_ampacity(cfg, ref["t_air_c"], ref["wind_ms"],
                                  ref["phi_deg"], ref["ghi_wm2"])
    static = cfg.static_rating_a
    return {"modelled_a": modelled, "static_a": static,
            "deviation_pct": 100.0 * (modelled - static) / static}
