"""The static-rating run a dynamic-rating run is compared against.

A dynamic rating is only worth what it adds over the static rating it replaces,
so a DLR run is reported beside a static run that differs in nothing else: the
same window, plants, storage, network, input data and model. That run is
reused when a matching one already sits in the output folder, and simulated
otherwise.

Matching is by fingerprint, not by folder name. A folder name records neither
the export cap nor the window nor the data, and a static run left over from a
different setup would otherwise be compared against without any sign of it.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from .config import Config
from .dataio import FILES

# Settings a static run ignores, or that only change how results are reported.
# Runs that differ only in these share one static reference.
_NOT_IN_STATIC_KEY = {
    "dlr_mode", "dlr_cap_ratio", "equipment_limit", "azimuth_z1_deg", "azimuth_z2_deg",
    "conductor_height_m", "roughness_m", "displacement_m", "low_wind_ms",
    "site_elevation_m", "t_cond_max_c", "start", "days", "end", "data_dir", "out_dir",
    "label", "energy_price_eur_mwh", "reactive_price_eur_mvarh",
}

# Modules that only report or orchestrate. Editing any other module may change
# a result, so it makes every earlier static run unusable as a reference.
_NOT_IN_MODEL = {"cli.py", "plots.py", "reference.py", "report.py"}


class Reference(NamedTuple):
    """A finished static-rating run and the configuration it ran with."""

    cfg: Config
    result: pd.DataFrame
    origin: str                 # "reused" or "simulated"


def static_config(cfg: Config) -> Config:
    """The same study under the static rating.

    An unlabelled run keeps the automatic name, which is what the static preset
    run from the command line is called too. A labelled run gets its own.
    """
    return dataclasses.replace(cfg, dlr_mode=0,
                               label=f"{cfg.label}_static" if cfg.label else "")


def _digest(paths) -> str:
    h = hashlib.sha256()
    for path in paths:
        h.update(path.name.encode())
        h.update(path.read_bytes() if path.is_file() else b"missing")
    return h.hexdigest()


def static_key(cfg: Config) -> str:
    """Fingerprint of everything that decides the result of a static-rating run.

    A DLR run and its static reference share a key, which is how one finds the
    other. It covers the settings, the window, the bytes of the input files and
    the model's source code.
    """
    fields = {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)
              if f.name not in _NOT_IN_STATIC_KEY}
    fields["der_enabled"] = dict(cfg.der_enabled)
    fields["window"] = [cfg.start_ts.isoformat(), cfg.end_ts.isoformat()]
    fields["data"] = _digest(Path(cfg.data_dir) / name for name in FILES.values())
    package = Path(__file__).resolve().parent
    fields["model"] = _digest(p for p in sorted(package.glob("*.py"))
                              if p.name not in _NOT_IN_MODEL)
    blob = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def load_timeseries(path: Path) -> pd.DataFrame:
    """Read a saved per-step result back into the frame the run produced."""
    return pd.read_csv(path, index_col=0, parse_dates=True)


def find(cfg: Config) -> Reference | None:
    """A finished static run under cfg.out_dir that matches cfg, if any."""
    key = static_key(cfg)
    found = []
    for path in Path(cfg.out_dir).glob("*/*_metrics.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("static_key") == key and meta.get("dlr_mode") == 0:
            found.append((path.stat().st_mtime, path, meta))
    for _, path, meta in sorted(found, key=lambda item: item[0], reverse=True):
        stem = str(meta.get("scenario", ""))
        series = path.parent / f"{stem}_timeseries.csv"
        if not series.is_file():
            continue
        try:
            result = load_timeseries(series)
        except (OSError, ValueError):
            continue
        if len(result) != cfg.n_steps or result.index[0] != cfg.start_ts:
            continue
        ref_cfg = dataclasses.replace(static_config(cfg), label=stem)
        return Reference(ref_cfg, result, "reused")
    return None
