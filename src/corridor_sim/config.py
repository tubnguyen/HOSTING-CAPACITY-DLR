"""Study configuration: one immutable Config, a preset table and a CLI.

Every knob the study exposes lives here. No module below reads a setting from
a global, so a run is fully described by the Config it was given.
"""
from __future__ import annotations

import argparse
import dataclasses
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

import pandas as pd

from . import constants as C

DER_NAMES = tuple(C.DER_RATING_MW)
ALL_ON = MappingProxyType(dict.fromkeys(DER_NAMES, True))


def _default_data_dir() -> Path:
    """Where the shipped dataset lives, for a source or editable checkout.

    Installed non-editable the package has no dataset beside it, so this falls
    back to ./data and the loader's own "not found" message takes over. It is
    only a default; --data-dir is the supported way to point at your own.
    """
    candidate = Path(__file__).resolve().parents[2] / "data"
    return candidate if candidate.is_dir() else Path("data")


DATA_DIR = _default_data_dir()


def _number(value: float) -> str:
    """A number as short text that reads back as the same number."""
    text = f"{value:g}"
    return text if float(text) == value else repr(float(value))


@dataclass(frozen=True)
class AddedPlant:
    """A wind or solar plant added at one of the corridor's connection points.

    Its output follows the shipped weather (wind) or solar profile, scaled to
    its own rating; see dataio.available_power.
    """

    name: str                                       # WF_4, WF_5, ... or PV_2, PV_3, ...
    kind: str                                       # "wind" | "solar"
    mw: float
    at: str                                         # one of C.CONNECTION_POINTS

    @property
    def spec(self) -> str:
        """The --add-der text that adds this plant."""
        return f"{self.kind}:{_number(self.mw)}@{self.at}"

    @property
    def tag(self) -> str:
        """Its part of the run name, e.g. wf50-SUB_C."""
        return f"{C.ADDED_DER_PREFIX[self.kind].lower()}{_number(self.mw)}-{self.at}"


@dataclass(frozen=True)
class AddedLoad:
    """A large constant demand on a corridor busbar, such as a data centre."""

    name: str                                       # LOAD_1, LOAD_2, ...
    mw: float
    at: str                                         # one of C.CONNECTION_POINTS
    pf: float = 1.0                                 # lagging; 1.0 draws no reactive power

    @property
    def q_mvar(self) -> float:
        return self.mw * math.tan(math.acos(self.pf))

    @property
    def spec(self) -> str:
        """The --add-load text that adds this load."""
        text = f"{_number(self.mw)}@{self.at}"
        return text if self.pf == 1.0 else f"{text}:{_number(self.pf)}"

    @property
    def tag(self) -> str:
        return f"ld{_number(self.mw)}-{self.at}"


@dataclass(frozen=True)
class Config:
    """A complete study specification."""

    # Network build
    conductor: str = "single"                       # "single" | "twin"
    wf_trafo_units: int = C.WF_TRAFO_UNITS          # 2 = as built, 1 = one unit out
    wf_trafo_rating: str = "onaf"                   # "onaf" (forced) | "onan" (natural)

    # Generation
    der_enabled: Mapping[str, bool] = field(
        default_factory=lambda: MappingProxyType(dict(ALL_ON)))
    added_der: tuple[AddedPlant, ...] = ()          # plants beyond the four built in
    control_mode: str = "droop"                     # "droop" | "cosphi"
    pv_control_mode: str | None = None           # None inherits control_mode
    cosphi_sign: str = "absorb"                     # "absorb" | "inject"
    droop_p_min_frac: float = C.DROOP_P_MIN_FRAC
    droop_measurement: str = "local"                # "local" | "pilot_tap_w" | "pilot_sub_a"

    # Line rating
    dlr_mode: int = 1                               # 0 static | 1 ambient-adjusted | 2 full weather
    dlr_cap_ratio: float | None = C.DLR_CAP_RATIO   # None rates the bare conductor
    equipment_limit: bool = True                    # honour the series-equipment nameplate
    azimuth_z1_deg: float = C.DLR_ZONE_AZIMUTH_DEG["Z1"]   # zone bearing, one of
    azimuth_z2_deg: float = C.DLR_ZONE_AZIMUTH_DEG["Z2"]   # C.DLR_AZIMUTH_OPTIONS_DEG
    conductor_height_m: float = C.DLR_CONDUCTOR_HEIGHT_M
    roughness_m: float = C.DLR_ROUGHNESS_M
    displacement_m: float = C.DLR_DISPLACEMENT_M
    low_wind_ms: float = C.DLR_LOW_WIND_MS
    dlr_wind_factor: float = C.DLR_WIND_FACTOR      # share of the wind that cools (mode 2)
    site_elevation_m: float = C.DLR_SITE_ELEVATION_M
    t_cond_max_c: float = C.T_COND_MAX_C

    # Demand beyond the shipped load profiles
    added_loads: tuple[AddedLoad, ...] = ()

    # Congestion management
    curtailment: bool = True
    export_cap_mw: float = C.EXPORT_CAP_MW
    export_cap_basis: str = "net"                   # "net" | "gross"

    # Storage
    storage_enabled: bool = False
    storage_p_mw: float = C.BESS_P_MW
    storage_e_mwh: float = C.BESS_E_MWH
    storage_soc_init: float = 0.50
    storage_at: str = C.STORAGE_DEFAULT_POINT        # one of C.STORAGE_POINTS
    storage_connection: str = "tie"                 # "tie" | "direct"
    storage_charge_source: str = "surplus_then_grid"
    storage_reserve_mw: float = C.BESS_P_MW
    storage_contract_mw: float = C.BESS_P_MW
    storage_q_mode: str = "droop"                   # "droop" | "cosphi" | "fixed" | "unity"
    storage_may_curtail_der: bool = False
    storage_relieves_congestion: bool = True        # hold back or charge before curtailing

    # Window and I/O
    start: str = "2024-01-01"
    days: int | None = 30                        # None uses `end`
    end: str = "2025-01-01"
    data_dir: Path = DATA_DIR
    out_dir: Path = Path("runs")
    label: str = ""

    # Reporting: prices for the technical cost, which change no result
    energy_price_eur_mwh: float = C.ENERGY_PRICE_EUR_MWH
    reactive_price_eur_mvarh: float = C.REACTIVE_PRICE_EUR_MVARH

    # ── Derived ──────────────────────────────────────────────────────────────
    @property
    def pv_mode(self) -> str:
        return self.pv_control_mode or self.control_mode

    @property
    def units(self) -> tuple:
        """Every generating unit in the network: the four built in, then any added."""
        return DER_NAMES + tuple(p.name for p in self.added_der)

    @property
    def unit_rating_mw(self) -> dict:
        ratings = dict(C.DER_RATING_MW)
        ratings.update({p.name: p.mw for p in self.added_der})
        return ratings

    @property
    def unit_q_limit_mvar(self) -> dict:
        """Reactive capability of each unit: cos phi 0.95 at rated power."""
        return {unit: mw * C.TAN_PHI for unit, mw in self.unit_rating_mw.items()}

    @property
    def unit_kind(self) -> dict:
        kinds = dict(C.DER_KIND)
        kinds.update({p.name: p.kind for p in self.added_der})
        return kinds

    @property
    def unit_bus(self) -> dict:
        """The corridor bus each unit joins."""
        buses = dict(C.DER_JOIN_BUS)
        buses.update({p.name: p.at for p in self.added_der})
        return buses

    def unit_mode(self, unit: str) -> str:
        """Reactive control of one unit: solar follows pv_mode, wind control_mode."""
        return self.pv_mode if self.unit_kind[unit] == "solar" else self.control_mode

    def unit_enabled(self, unit: str) -> bool:
        """A built-in plant is connected if enabled; an added one always is."""
        return bool(self.der_enabled[unit]) if unit in self.der_enabled else True

    @property
    def active_der(self) -> list:
        return ([n for n in DER_NAMES if self.der_enabled.get(n, False)]
                + [p.name for p in self.added_der])

    @property
    def n_der(self) -> int:
        return len(self.active_der)

    @property
    def der_fleet_mw(self) -> float:
        ratings = self.unit_rating_mw
        return sum(ratings[n] for n in self.active_der)

    @property
    def added_der_mw(self) -> float:
        return sum(p.mw for p in self.added_der)

    @property
    def added_load_mw(self) -> float:
        return sum(load.mw for load in self.added_loads)

    @property
    def storage_bus(self) -> str:
        """The corridor bus the battery joins. A battery that is switched off
        stays at TAP_B, so it never changes the network it is not part of."""
        return self.storage_at if self.storage_enabled else C.STORAGE_DEFAULT_POINT

    @property
    def midpoints_used(self) -> set:
        """Midpoint buses something connects to, and so has to be built."""
        points = {p.at for p in self.added_der} | {load.at for load in self.added_loads}
        return (points | {self.storage_bus}) & set(C.MIDPOINT_BUSES)

    @property
    def line(self) -> dict:
        return C.CONDUCTOR_OPTIONS[self.conductor]

    @property
    def bundle_n(self) -> int:
        return self.line["bundle_n"]

    @property
    def conductor_label(self) -> str:
        """Name of the build in tables and figures, e.g. 1-Duck."""
        return self.line["label"]

    @property
    def static_rating_a(self) -> float:
        """Static (seasonal) bundle ampacity used when dlr_mode == 0."""
        return self.line["max_i_ka"] * 1000.0

    @property
    def equipment_rating_a(self) -> float | None:
        """Nameplate of the substation plant in series with the line [A].

        None when the study is deliberately rating the bare conductor.
        """
        return self.line["equipment_i_ka"] * 1000.0 if self.equipment_limit else None

    @property
    def rating_cap_a(self) -> float | None:
        """Administrative ceiling on the dynamic rating [A], if any."""
        return (self.dlr_cap_ratio * self.static_rating_a
                if self.dlr_cap_ratio is not None else None)

    @property
    def zone_azimuth_deg(self) -> dict:
        """Mean bearing of each rating zone [degrees clockwise from north]."""
        return {"Z1": self.azimuth_z1_deg, "Z2": self.azimuth_z2_deg}

    @property
    def cosphi_sign_factor(self) -> float:
        return -1.0 if self.cosphi_sign == "absorb" else 1.0

    @property
    def storage_droop_active(self) -> bool:
        return self.storage_enabled and self.storage_q_mode == "droop"

    @property
    def max_outer_iter(self) -> int:
        """Droop chases a moving reactive target and needs a longer budget."""
        droop = (self.control_mode == "droop" or self.pv_mode == "droop"
                 or self.storage_droop_active)
        return C.DROOP_MAX_ITER if droop else C.COSPHI_MAX_ITER

    @property
    def start_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.start, tz="UTC")

    @property
    def end_ts(self) -> pd.Timestamp:
        if self.days is not None:
            return self.start_ts + pd.Timedelta(days=self.days)
        return pd.Timestamp(self.end, tz="UTC")

    @property
    def n_steps(self) -> int:
        return int(round((self.end_ts - self.start_ts).total_seconds() / 3600 / C.DT_H))

    @property
    def q_window_mvar(self) -> float:
        """Permitted reactive exchange at the PCC, set by the installed fleet."""
        return min(C.Q_WINDOW_FRAC * (C.FLEET_MW + self.added_der_mw), C.Q_WINDOW_CAP_MVAR)

    @property
    def soc_min_mwh(self) -> float:
        return C.BESS_SOC_MIN * self.storage_e_mwh

    @property
    def soc_max_mwh(self) -> float:
        return C.BESS_SOC_MAX * self.storage_e_mwh

    @property
    def soc_reserve_mwh(self) -> float:
        return max(C.BESS_SOC_MIN, C.BESS_SOC_RESERVE_FLOOR) * self.storage_e_mwh

    @property
    def soc_recharge_target_mwh(self) -> float:
        """Energy the battery refills to: enough to deliver the contracted
        reserve for a full hour on top of the physical floor."""
        return min(self.soc_max_mwh,
                   self.soc_min_mwh + self.storage_contract_mw / C.BESS_ETA_DIS)

    @property
    def q_limit_storage(self) -> float:
        return self.storage_p_mw * C.TAN_PHI

    @property
    def stem(self) -> str:
        """Filename stem describing this run.

        Each added plant and load appends its kind, size and connection point,
        so runs that differ only in where something connects keep apart.
        """
        if self.label:
            return self.label
        stem = (f"{self.conductor}_der{self.n_der}_{self.control_mode}"
                f"_dlr{self.dlr_mode}_bess{int(self.storage_enabled)}")
        if self.storage_bus != C.STORAGE_DEFAULT_POINT:
            stem += f"-{self.storage_bus}"
        tags = [p.tag for p in self.added_der] + [load.tag for load in self.added_loads]
        return "_".join([stem] + tags)


# ── Scenario matrix ──────────────────────────────────────────────────────────
# Rows sweep DER penetration and the storage layer; columns sweep the line
# rating method and the conductor build.
_DER_STEPS = {
    1: ["WF_2"],
    2: ["WF_2", "WF_1"],
    3: ["WF_2", "WF_1", "WF_3"],
    4: ["WF_2", "WF_1", "WF_3", "PV_1"],
}

_MATRIX_COLUMNS = {
    "static": ("single", 0),
    "dlr1": ("single", 1),
    "dlr2": ("single", 2),
    "twin": ("twin", 0),
}

PRESETS = {
    "baseline": dict(conductor="single", control_mode="droop", dlr_mode=0, der_enabled=[]),
}
for _tag, (_cond, _dlr) in _MATRIX_COLUMNS.items():
    for _n, _ders in _DER_STEPS.items():
        PRESETS[f"{_tag}_der{_n}"] = dict(conductor=_cond, dlr_mode=_dlr,
                                          control_mode="droop", der_enabled=list(_ders))
    PRESETS[f"{_tag}_der4_bess"] = dict(conductor=_cond, dlr_mode=_dlr, control_mode="droop",
                                        der_enabled=list(_DER_STEPS[4]), storage_enabled=True)


def normalise_der(der) -> Mapping[str, bool]:
    """Accept a list of enabled names or a partial dict; return a full map.

    The result is read-only. Config is a frozen dataclass, but a plain dict
    field is still mutable through the field, so a caller could quietly change
    which plants a "frozen" study had connected after it was validated.
    """
    if der is None:
        return MappingProxyType(dict(ALL_ON))
    if isinstance(der, (list, tuple, set)):
        unknown = set(der) - set(DER_NAMES)
        if unknown:
            raise ValueError(f"unknown DER {sorted(unknown)}; valid: {list(DER_NAMES)}")
        return MappingProxyType({n: (n in der) for n in DER_NAMES})
    unknown = set(der) - set(DER_NAMES)
    if unknown:
        raise ValueError(f"unknown DER key {sorted(unknown)}; valid: {list(DER_NAMES)}")
    return MappingProxyType({n: bool(der.get(n, True)) for n in DER_NAMES})


def connection_point(text: str) -> str:
    """A connection point named by the user, in its canonical spelling."""
    point = str(text).strip().upper()
    if point not in C.CONNECTION_POINTS:
        raise ValueError(f"unknown connection point {text!r}; "
                         f"valid: {list(C.CONNECTION_POINTS)}")
    return point


def _megawatts(text, what: str) -> float:
    try:
        mw = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: {text!r} is not a number of MW") from None
    if not (0.0 < mw <= C.ADDED_MAX_MW):
        raise ValueError(f"{what}: {_number(mw)} MW is outside 0 to {C.ADDED_MAX_MW:g} MW")
    return mw


def parse_der_spec(text: str) -> dict:
    """Read an --add-der value, KIND:MW@POINT, e.g. wind:50@SUB_C."""
    kind, sep, rest = str(text).partition(":")
    mw, at_sign, point = rest.partition("@")
    if not sep or not at_sign:
        raise ValueError(f"--add-der {text!r}: expected KIND:MW@POINT, e.g. wind:50@SUB_C")
    kind = kind.strip().lower()
    if kind not in C.ADDED_DER_KINDS:
        raise ValueError(f"--add-der {text!r}: unknown kind {kind!r}; "
                         f"valid: {list(C.ADDED_DER_KINDS)}")
    return {"kind": kind, "mw": _megawatts(mw, f"--add-der {text!r}"),
            "at": connection_point(point)}


def parse_load_spec(text: str) -> dict:
    """Read an --add-load value, MW@POINT or MW@POINT:PF, e.g. 80@SUB_C."""
    mw, at_sign, rest = str(text).partition("@")
    point, colon, pf = rest.partition(":")
    if not at_sign:
        raise ValueError(f"--add-load {text!r}: expected MW@POINT, e.g. 80@SUB_C")
    spec = {"mw": _megawatts(mw, f"--add-load {text!r}"), "at": connection_point(point)}
    if colon:
        try:
            spec["pf"] = float(pf)
        except ValueError:
            raise ValueError(f"--add-load {text!r}: power factor {pf!r} "
                             f"is not a number") from None
    return spec


def _fill_names(items: list, prefix_of, taken: set) -> list:
    """Give every item without a name the next free <prefix>_<n>."""
    taken = set(taken) | {item["name"] for item in items if item.get("name")}
    for item in items:
        if not item.get("name"):
            prefix, n = prefix_of(item), 1
            while f"{prefix}_{n}" in taken:
                n += 1
            item["name"] = f"{prefix}_{n}"
            taken.add(item["name"])
    return items


def _as_dict(item, parse, record_type) -> dict:
    if isinstance(item, record_type):
        return dataclasses.asdict(item)
    if isinstance(item, str):
        return parse(item)
    if isinstance(item, Mapping):
        return dict(item)
    raise ValueError(f"cannot read {item!r} as a {record_type.__name__}")


def normalise_added_der(items) -> tuple:
    """Accept AddedPlant records, dicts or --add-der texts; return named records.

    An item without a name is called after its kind, numbered on from the
    built-in plants: the first added wind farm is WF_4, the first solar plant
    PV_2.
    """
    dicts = [_as_dict(item, parse_der_spec, AddedPlant) for item in (items or ())]
    for item in dicts:
        item["kind"] = str(item.get("kind", "")).strip().lower()
        item["at"] = str(item.get("at", "")).strip().upper()
        item["mw"] = float(item["mw"]) if "mw" in item else float("nan")
    dicts = _fill_names(dicts, lambda item: C.ADDED_DER_PREFIX.get(item["kind"], "DER"),
                        set(DER_NAMES))
    try:
        return tuple(AddedPlant(**item) for item in dicts)
    except TypeError as exc:
        raise ValueError(f"bad added plant: {exc}") from None


def normalise_added_loads(items) -> tuple:
    """Accept AddedLoad records, dicts or --add-load texts; return named records."""
    dicts = [_as_dict(item, parse_load_spec, AddedLoad) for item in (items or ())]
    for item in dicts:
        item["at"] = str(item.get("at", "")).strip().upper()
        item["mw"] = float(item["mw"]) if "mw" in item else float("nan")
        item["pf"] = float(item.get("pf", 1.0))
    dicts = _fill_names(dicts, lambda item: C.ADDED_LOAD_PREFIX, set())
    try:
        return tuple(AddedLoad(**item) for item in dicts)
    except TypeError as exc:
        raise ValueError(f"bad added load: {exc}") from None


def build_config(preset: str | None = None, **overrides) -> Config:
    """Field defaults, then the named preset, then explicit overrides.

    Overrides are applied as given, `None` included, so a caller can ask for
    `days=None` and mean it. Callers that use `None` to mean "not specified"
    must strip those keys themselves; `parse_args` does.
    """
    fields = dict(PRESETS[preset]) if preset else {}
    fields.update(overrides)
    fields["der_enabled"] = normalise_der(fields.get("der_enabled"))
    fields["added_der"] = normalise_added_der(fields.get("added_der"))
    fields["added_loads"] = normalise_added_loads(fields.get("added_loads"))
    if "storage_at" in fields:
        fields["storage_at"] = str(fields["storage_at"]).strip().upper()
    cfg = Config(**fields)
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    """Fail fast on any inconsistent knob, before a run costs time.

    Raises ValueError rather than asserting. `python -O` strips assertions, and
    a validator that silently disappears under an optimisation flag is worse
    than no validator: the run proceeds on a configuration nobody checked.
    """
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    require(cfg.conductor in C.CONDUCTOR_OPTIONS,
            f"bad conductor {cfg.conductor!r}; valid: {sorted(C.CONDUCTOR_OPTIONS)}")
    require(cfg.control_mode in {"droop", "cosphi"},
            f"bad control_mode {cfg.control_mode!r}; valid: ['cosphi', 'droop']")
    require(cfg.pv_control_mode in {None, "droop", "cosphi"},
            f"bad pv_control_mode {cfg.pv_control_mode!r}; valid: [None, 'cosphi', 'droop']")
    require(cfg.cosphi_sign in {"absorb", "inject"},
            f"bad cosphi_sign {cfg.cosphi_sign!r}; valid: ['absorb', 'inject']")
    require(cfg.dlr_mode in {0, 1, 2}, f"bad dlr_mode {cfg.dlr_mode!r}; valid: [0, 1, 2]")
    require(cfg.dlr_cap_ratio is None or cfg.dlr_cap_ratio >= 1.0,
            "dlr_cap_ratio must be at least 1.0 (or None for no cap); a cap below the "
            "static rating would derate the line the moment DLR was switched on")
    for zone, azimuth in cfg.zone_azimuth_deg.items():
        require(azimuth in C.DLR_AZIMUTH_OPTIONS_DEG,
                f"bad azimuth {azimuth!r} for zone {zone}; valid: "
                f"{[int(a) for a in C.DLR_AZIMUTH_OPTIONS_DEG]}")
    require(cfg.export_cap_basis in {"net", "gross"},
            f"bad export_cap_basis {cfg.export_cap_basis!r}; valid: ['gross', 'net']")
    require(cfg.droop_measurement in {"local", "pilot_tap_w", "pilot_sub_a"},
            f"bad droop_measurement {cfg.droop_measurement!r}")
    require(cfg.wf_trafo_rating in {"onan", "onaf"},
            f"bad wf_trafo_rating {cfg.wf_trafo_rating!r}; valid: ['onaf', 'onan']")
    require(cfg.wf_trafo_units in {1, 2}, "wf_trafo_units must be 1 or 2")
    require(set(cfg.der_enabled) == set(DER_NAMES),
            f"der_enabled must cover every DER {list(DER_NAMES)}")
    _validate_added(cfg, require)
    require(cfg.storage_at in C.STORAGE_POINTS,
            f"bad storage_at {cfg.storage_at!r}; valid: {list(C.STORAGE_POINTS)}")
    require(cfg.storage_connection in {"tie", "direct"},
            f"bad storage_connection {cfg.storage_connection!r}; valid: ['direct', 'tie']")
    require(cfg.storage_q_mode in {"droop", "cosphi", "fixed", "unity"},
            f"bad storage_q_mode {cfg.storage_q_mode!r}")
    require(cfg.storage_charge_source in {"surplus_then_grid", "surplus_only", "grid_only"},
            f"bad storage_charge_source {cfg.storage_charge_source!r}")
    require(0.0 <= cfg.storage_soc_init <= 1.0, "storage_soc_init must be in [0, 1]")
    require(cfg.storage_p_mw > 0 and cfg.storage_e_mwh > 0,
            "storage ratings must be positive")
    require(0.0 <= cfg.storage_reserve_mw <= cfg.storage_p_mw,
            "storage_reserve_mw exceeds the storage rating")
    require(0.0 <= cfg.storage_contract_mw <= cfg.storage_p_mw,
            "storage_contract_mw exceeds the storage rating")
    require(cfg.roughness_m > 0, "roughness_m must be positive")
    require(0.0 < cfg.dlr_wind_factor <= 1.0, "dlr_wind_factor must be in (0, 1]")
    require(cfg.conductor_height_m - cfg.displacement_m > cfg.roughness_m,
            "conductor height above displacement must exceed the roughness length")
    require(cfg.days is None or cfg.days > 0, "days must be positive")
    require(cfg.end_ts > cfg.start_ts, "end must be after start")
    require(cfg.energy_price_eur_mwh >= 0 and cfg.reactive_price_eur_mvarh >= 0,
            "prices must not be negative")


def _validate_added(cfg: Config, require) -> None:
    """Added plants and loads: known kinds and points, sizes, unique names.

    A name has to be <prefix>_<n>. The network names a plant's busbar and
    transformer after it, so a free-form name could land on an element that
    already exists and quietly take its place in the index.
    """
    points = list(C.CONNECTION_POINTS)
    for plant in cfg.added_der:
        require(isinstance(plant, AddedPlant), f"added_der holds {plant!r}, not an AddedPlant")
        require(plant.kind in C.ADDED_DER_KINDS,
                f"bad kind {plant.kind!r} for {plant.name}; valid: {list(C.ADDED_DER_KINDS)}")
        require(0.0 < plant.mw <= C.ADDED_MAX_MW,
                f"{plant.name} rating must be above 0 and at most {C.ADDED_MAX_MW:g} MW")
        require(plant.at in C.CONNECTION_POINTS,
                f"bad connection point {plant.at!r} for {plant.name}; valid: {points}")
        prefix = C.ADDED_DER_PREFIX[plant.kind]
        require(re.fullmatch(rf"{prefix}_\d+", plant.name) is not None,
                f"added {plant.kind} plant name {plant.name!r} must be {prefix}_<n>")
    for load in cfg.added_loads:
        require(isinstance(load, AddedLoad), f"added_loads holds {load!r}, not an AddedLoad")
        require(0.0 < load.mw <= C.ADDED_MAX_MW,
                f"{load.name} demand must be above 0 and at most {C.ADDED_MAX_MW:g} MW")
        require(load.at in C.CONNECTION_POINTS,
                f"bad connection point {load.at!r} for {load.name}; valid: {points}")
        require(0.0 < load.pf <= 1.0, f"{load.name} power factor must be in (0, 1]")
        require(re.fullmatch(rf"{C.ADDED_LOAD_PREFIX}_\d+", load.name) is not None,
                f"added load name {load.name!r} must be {C.ADDED_LOAD_PREFIX}_<n>")
    names = list(cfg.units) + [load.name for load in cfg.added_loads]
    require(len(names) == len(set(names)),
            f"added plant and load names must be unique and differ from {list(DER_NAMES)}")


def to_record(cfg: Config) -> dict:
    """Every field of a Config as plain JSON values, so a run can be rebuilt."""
    record = {}
    for f in dataclasses.fields(cfg):
        value = getattr(cfg, f.name)
        if f.name == "der_enabled":
            value = [n for n in DER_NAMES if value.get(n, False)]
        elif f.name in ("added_der", "added_loads"):
            value = [dataclasses.asdict(item) for item in value]
        elif isinstance(value, Path):
            value = str(value)
        record[f.name] = value
    return record


def from_record(record: Mapping) -> Config:
    """Rebuild the Config a run was made with from its saved record.

    Keys this version does not know are dropped and fields the record lacks
    take their defaults, so a record written by an older version still loads.
    """
    known = {f.name for f in dataclasses.fields(Config)}
    fields = {k: v for k, v in record.items() if k in known}
    for name in ("data_dir", "out_dir"):
        if name in fields:
            fields[name] = Path(fields[name])
    return build_config(**fields)


class RunOptions(NamedTuple):
    """Command-line choices about what a run produces, not what it models."""

    plots: bool = True
    compare: bool = False       # also simulate the static case and compare
    interactive: bool = False   # ask for the scenario instead of reading flags


def parse_args(argv=None):
    """Parse the command line into (Config, RunOptions)."""
    p = argparse.ArgumentParser(
        prog="corridor-sim",
        description="Quasi-static AC power-flow study of a DLR-enabled export corridor")
    p.add_argument("--preset", choices=sorted(PRESETS), help="named scenario")
    p.add_argument("--conductor", choices=sorted(C.CONDUCTOR_OPTIONS))
    p.add_argument("--dlr", dest="dlr_mode", type=int, choices=[0, 1, 2],
                   help="0 static, 1 ambient-adjusted, 2 full weather")
    cap = p.add_mutually_exclusive_group()
    cap.add_argument("--dlr-cap-ratio", dest="dlr_cap_ratio", type=float,
                     help="administrative ceiling on the dynamic rating, as a multiple "
                          f"of the static rating (default {C.DLR_CAP_RATIO})")
    cap.add_argument("--no-rating-cap", action="store_true",
                     help="rate the bare conductor with no administrative cap")
    p.add_argument("--no-equipment-limit", dest="equipment_limit", action="store_false",
                   default=None, help="ignore the series substation equipment rating")
    azimuths = [int(a) for a in C.DLR_AZIMUTH_OPTIONS_DEG]
    p.add_argument("--azimuth", type=float, choices=azimuths,
                   help="mean bearing of the whole corridor, degrees from north "
                        "(sets both zones; 0 north-south, 90 east-west)")
    p.add_argument("--azimuth-z1", dest="azimuth_z1_deg", type=float, choices=azimuths,
                   help=f"bearing of zone Z1 (default {C.DLR_ZONE_AZIMUTH_DEG['Z1']:.0f})")
    p.add_argument("--azimuth-z2", dest="azimuth_z2_deg", type=float, choices=azimuths,
                   help=f"bearing of zone Z2 (default {C.DLR_ZONE_AZIMUTH_DEG['Z2']:.0f})")
    p.add_argument("--wind-factor", dest="dlr_wind_factor", type=float,
                   help="share of the conductor-height wind counted as cooling in mode 2 "
                        f"(default {C.DLR_WIND_FACTOR:g})")
    p.add_argument("--control", dest="control_mode", choices=["droop", "cosphi"])
    p.add_argument("--der", help="comma-separated built-in plants to connect, e.g. "
                                 "WF_1,WF_2,PV_1, or 'all' or 'none'")
    points = ", ".join(C.CONNECTION_POINTS)
    p.add_argument("--add-der", dest="add_der", action="append", metavar="KIND:MW@POINT",
                   help="add a wind or solar plant, e.g. wind:50@SUB_C; repeat to add "
                        f"more. POINT is one of {points}")
    p.add_argument("--add-load", dest="add_load", action="append", metavar="MW@POINT[:PF]",
                   help="add a large constant load, e.g. 80@SUB_C or 80@SUB_C:0.95; "
                        "repeat to add more")
    p.add_argument("--storage", dest="storage_enabled", action="store_true", default=None)
    p.add_argument("--storage-at", dest="storage_at", type=str.upper,
                   choices=list(C.STORAGE_POINTS), metavar="POINT",
                   help="where the battery connects (default TAP_B): "
                        + ", ".join(C.STORAGE_POINTS) + "; implies --storage")
    p.add_argument("--no-curtailment", dest="curtailment", action="store_false", default=None)
    p.add_argument("--export-cap", dest="export_cap_mw", type=float)
    p.add_argument("--start")
    p.add_argument("--days", type=int, help="window length in days (default 30)")
    p.add_argument("--end", help="explicit end date; overrides --days")
    p.add_argument("--data-dir", dest="data_dir", type=Path)
    p.add_argument("--out", dest="out_dir", type=Path)
    p.add_argument("--label", help="output filename stem")
    p.add_argument("--energy-price", dest="energy_price_eur_mwh", type=float,
                   help="EUR/MWh for curtailed energy and losses "
                        f"(default {C.ENERGY_PRICE_EUR_MWH:g})")
    p.add_argument("--reactive-price", dest="reactive_price_eur_mvarh", type=float,
                   help="EUR/MVArh for reactive energy outside the PCC window "
                        f"(default {C.REACTIVE_PRICE_EUR_MVARH:g})")
    p.add_argument("--no-plots", dest="plots", action="store_false", default=True)
    p.add_argument("--compare", action="store_true",
                   help="also run the same case at the static rating and compare the two")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="ask for the scenario question by question; any other options "
                        "given are the defaults offered. Running with no options in a "
                        "terminal does the same")
    a = p.parse_args(argv)

    # An unset flag means "do not override", so None values are dropped here
    # rather than in build_config. --no-rating-cap is the exception: it means
    # None on purpose, so it is applied after the filter. It is read from the
    # parsed flag, not by searching argv for its text, because argparse also
    # accepts an unambiguous prefix such as --no-rating.
    over = {k: v for k, v in vars(a).items()
            if k not in {"preset", "der", "plots", "compare", "azimuth", "no_rating_cap",
                         "add_der", "add_load", "interactive"}
            and v is not None}
    if a.no_rating_cap:
        over["dlr_cap_ratio"] = None
    if a.azimuth is not None:
        # A zone-specific flag wins over the corridor-wide one.
        over.setdefault("azimuth_z1_deg", a.azimuth)
        over.setdefault("azimuth_z2_deg", a.azimuth)
    if a.der is not None:
        over["der_enabled"] = parse_der_list(a.der)
    if a.add_der is not None:
        over["added_der"] = list(a.add_der)
    if a.add_load is not None:
        over["added_loads"] = list(a.add_load)
    if a.end is not None:
        over["days"] = None
    if a.storage_at is not None:
        over["storage_enabled"] = True
    return build_config(a.preset, **over), RunOptions(plots=a.plots, compare=a.compare,
                                                      interactive=a.interactive)


def parse_der_list(text: str) -> list:
    """A --der value: built-in plant names separated by commas, 'all' or 'none'."""
    names = [s.strip().upper() for s in str(text).split(",") if s.strip()]
    if names == ["ALL"]:
        return list(DER_NAMES)
    if names == ["NONE"]:
        return []
    return names


def to_argv(cfg: Config, options: RunOptions = RunOptions()) -> list:
    """The command-line options that reproduce `cfg` and `options`.

    The inverse of parse_args: a setting at its default is left out, so the
    result is the shortest command for the run. Settings only reachable from
    Python have no option and are not represented.
    """
    d = Config()
    argv = []

    def flag(name: str, value) -> None:
        argv.extend([name, value if isinstance(value, str) else _number(value)])

    if cfg.conductor != d.conductor:
        flag("--conductor", cfg.conductor)
    if cfg.dlr_mode != d.dlr_mode:
        flag("--dlr", str(cfg.dlr_mode))
    if cfg.dlr_cap_ratio is None:
        argv.append("--no-rating-cap")
    elif cfg.dlr_cap_ratio != d.dlr_cap_ratio:
        flag("--dlr-cap-ratio", cfg.dlr_cap_ratio)
    if not cfg.equipment_limit:
        argv.append("--no-equipment-limit")
    z1, z2 = cfg.azimuth_z1_deg, cfg.azimuth_z2_deg
    if z1 == z2 and (z1, z2) != (d.azimuth_z1_deg, d.azimuth_z2_deg):
        flag("--azimuth", z1)
    else:
        if z1 != d.azimuth_z1_deg:
            flag("--azimuth-z1", z1)
        if z2 != d.azimuth_z2_deg:
            flag("--azimuth-z2", z2)
    if cfg.dlr_wind_factor != d.dlr_wind_factor:
        flag("--wind-factor", cfg.dlr_wind_factor)
    if cfg.control_mode != d.control_mode:
        flag("--control", cfg.control_mode)
    built = [n for n in DER_NAMES if cfg.der_enabled.get(n, False)]
    if built != list(DER_NAMES):
        flag("--der", ",".join(built) if built else "none")
    for plant in cfg.added_der:
        flag("--add-der", plant.spec)
    for load in cfg.added_loads:
        flag("--add-load", load.spec)
    if cfg.storage_enabled and cfg.storage_at != d.storage_at:
        flag("--storage-at", cfg.storage_at)
    elif cfg.storage_enabled:
        argv.append("--storage")
    if not cfg.curtailment:
        argv.append("--no-curtailment")
    if cfg.export_cap_mw != d.export_cap_mw:
        flag("--export-cap", cfg.export_cap_mw)
    if cfg.start != d.start:
        flag("--start", str(cfg.start))
    if cfg.days is None:
        flag("--end", str(cfg.end))
    elif cfg.days != d.days:
        flag("--days", str(cfg.days))
    if Path(cfg.data_dir) != Path(d.data_dir):
        flag("--data-dir", str(cfg.data_dir))
    if Path(cfg.out_dir) != Path(d.out_dir):
        flag("--out", str(cfg.out_dir))
    if cfg.label:
        flag("--label", cfg.label)
    if cfg.energy_price_eur_mwh != d.energy_price_eur_mwh:
        flag("--energy-price", cfg.energy_price_eur_mwh)
    if cfg.reactive_price_eur_mvarh != d.reactive_price_eur_mvarh:
        flag("--reactive-price", cfg.reactive_price_eur_mvarh)
    if not options.plots:
        argv.append("--no-plots")
    if options.compare:
        argv.append("--compare")
    return argv
