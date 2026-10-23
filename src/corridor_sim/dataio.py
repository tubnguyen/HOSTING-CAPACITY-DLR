"""Input loading and alignment onto the simulation time grid.

A missing file, a gap in coverage or a duplicated timestamp raises here rather
than being filled in silently. The failure mode this guards against is not a
crash but its opposite: a run that quietly extends the edge of a short file
across the uncovered window produces a flat line that plots as entirely
plausible weather, and nothing downstream can tell it from a measurement.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import constants as C
from . import ders, dlr

FILES = {
    "weather": "weather.csv",
    "load": "load.csv",
    "pv": "pv_generation.csv",
    "reserve": "reserve_activation.csv",
}


def simulation_index(cfg) -> pd.DatetimeIndex:
    return pd.date_range(cfg.start_ts, cfg.end_ts, freq=f"{int(C.DT_H * 60)}min",
                         tz="UTC", inclusive="left")


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python data/generate.py` to create the dataset.")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    if not df.index.is_unique:
        raise ValueError(f"{path.name} has duplicate timestamps")
    return df.sort_index()


def coverage(data_dir) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The window every input file covers, as (first start, last end).

    The end is exclusive, like a run's: the last 15-minute step of a window
    ending there is the last timestamp all four files reach.
    """
    spans = [_read(Path(data_dir) / name).index for name in FILES.values()]
    first = max(span[0] for span in spans)
    last = min(span[-1] for span in spans)
    return first, last + pd.Timedelta(hours=C.DT_H)


# Alignment and the coverage rule it enforces live in dlr.align_to_index, so
# the rating weather and every other input are held to exactly the same rule.
_align = dlr.align_to_index


def load_inputs(cfg) -> dict:
    """Read every input and return frames aligned to the simulation index."""
    index = simulation_index(cfg)
    data_dir = Path(cfg.data_dir)
    raw = {k: _read(data_dir / v) for k, v in FILES.items()}

    weather_15 = _align(raw["weather"], index, "weather.csv")
    inputs = {
        "index": index,
        "weather": weather_15,
        "load": _align(raw["load"], index, "load.csv"),
        "wind": ders.wind_dispatch(weather_15),
        "reserve": _align(raw["reserve"], index, "reserve_activation.csv")["activation_up_mw"],
    }
    pv = _align(raw["pv"], index, "pv_generation.csv")["p_ac_mw"]
    inputs["pv"] = pv.clip(0.0, C.DER_RATING_MW["PV_1"])
    inputs["available"] = available_power(cfg, weather_15, inputs["wind"], pv)
    inputs["rating_weather"] = (dlr.prepare_weather(cfg, raw["weather"], index)
                                if cfg.dlr_mode > 0 else None)
    return inputs


def available_power(cfg, weather: pd.DataFrame, wind: pd.DataFrame,
                    pv: pd.Series) -> pd.DataFrame:
    """Output [MW] each unit could deliver at each step, before curtailment.

    The built-in plants keep their own series. An added wind farm is modelled
    from the weather like the built-in ones; an added solar plant follows the
    solar file scaled from PV_1's rating to its own, since the file is the
    output of one plant rather than a resource.
    """
    out = pd.DataFrame(index=weather.index)
    for unit in ("WF_1", "WF_2", "WF_3"):
        out[unit] = wind[unit]
    out["PV_1"] = pv.clip(0.0, C.DER_RATING_MW["PV_1"])
    per_unit_pv = (pv / C.DER_RATING_MW["PV_1"]).clip(0.0, 1.0)
    for plant in cfg.added_der:
        out[plant.name] = (ders.added_farm_dispatch(weather, plant.mw) if plant.kind == "wind"
                           else per_unit_pv * plant.mw)
    return out
