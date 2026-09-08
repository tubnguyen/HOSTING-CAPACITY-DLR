"""Input loading, and the coverage rule it exists to enforce.

The failure these guard against is not a crash. It is a run that silently
extends the edge of a short file across the window it does not cover, and then
reports a perfectly plausible result computed from a flat line.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from corridor_sim import dataio, dlr
from corridor_sim.config import build_config


def _weather(start: str, hours: int, freq: str = "h") -> pd.DataFrame:
    idx = pd.date_range(start, periods=hours, freq=freq, tz="UTC")
    n = len(idx)
    return pd.DataFrame(
        {"t_air_c": np.linspace(-5.0, 5.0, n),
         "u100_ms": np.full(n, 4.0), "v100_ms": np.full(n, 3.0),
         "u10_ms": np.full(n, 2.5), "v10_ms": np.full(n, 1.9),
         "ghi_wm2": np.zeros(n)},
        index=idx)


def _index(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq="15min", tz="UTC")


def test_coarser_input_is_interpolated_onto_the_grid():
    """Hourly weather on a 15-minute grid is the normal case, not an error."""
    out = dlr.align_to_index(_weather("2024-01-01", 5), _index("2024-01-01", 17), "w")
    assert len(out) == 17
    assert out["t_air_c"].notna().all()
    assert out["t_air_c"].is_monotonic_increasing


def test_window_past_the_end_of_the_data_is_rejected():
    with pytest.raises(ValueError, match="does not span"):
        dlr.align_to_index(_weather("2024-01-01", 5), _index("2024-01-01", 40), "weather.csv")


def test_window_before_the_start_of_the_data_is_rejected():
    with pytest.raises(ValueError, match="does not span"):
        dlr.align_to_index(_weather("2024-01-01", 5), _index("2023-12-31 22:00", 8), "w")


def test_uncovered_window_is_never_filled_with_a_held_edge_value():
    """The specific regression: a forward fill leaves no NaN to detect.

    A coverage check written as "fill, then look for NaN" passes silently here
    and hands back hours of constant weather. The temperature and wind held
    over from the last row would plot as a calm, cold spell and read as real.
    """
    short = _weather("2024-01-01", 5)
    long_window = _index("2024-01-01", 40)
    with pytest.raises(ValueError):
        dlr.align_to_index(short, long_window, "weather.csv")

    # Demonstrate what the rejected path would otherwise have produced.
    filled = (short.reindex(long_window.union(short.index))
              .interpolate(method="time", limit_direction="both")
              .reindex(long_window))
    beyond = filled.loc[short.index[-1] + pd.Timedelta("15min"):]
    assert filled.notna().all().all(), "the fill leaves nothing for an isna() check to find"
    assert beyond["t_air_c"].nunique() == 1, "and what it leaves behind is a flat line"


def test_a_gap_between_observations_is_interpolated_not_rejected():
    """Interior gaps are filled, and that is the intended behaviour.

    A missing hour inside an hourly file is not distinguishable from a file
    that was six-hourly to begin with, and rejecting it would rule out every
    legitimately coarse input. The line drawn here is the file's own span:
    inside it, interpolate; outside it, refuse.
    """
    w = _weather("2024-01-01", 6)
    w.loc[w.index[2:4], "t_air_c"] = np.nan
    out = dlr.align_to_index(w, _index("2024-01-01", 21), "weather.csv")
    assert out["t_air_c"].notna().all()
    assert out["t_air_c"].is_monotonic_increasing


def test_a_column_missing_at_the_leading_edge_is_rejected():
    """Nothing precedes the first row, so a NaN there cannot be interpolated."""
    w = _weather("2024-01-01", 6)
    w.loc[w.index[0], "t_air_c"] = np.nan
    with pytest.raises(ValueError, match="gaps inside"):
        dlr.align_to_index(w, _index("2024-01-01", 21), "weather.csv")


def test_an_entirely_empty_column_is_rejected():
    w = _weather("2024-01-01", 6)
    w["ghi_wm2"] = np.nan
    with pytest.raises(ValueError, match="gaps inside"):
        dlr.align_to_index(w, _index("2024-01-01", 21), "weather.csv")


def test_missing_column_is_named():
    with pytest.raises(KeyError, match="ghi_wm2"):
        dlr.prepare_weather(build_config("dlr2_der4"),
                            _weather("2024-01-01", 5).drop(columns=["ghi_wm2"]),
                            _index("2024-01-01", 17))


def test_shipped_dataset_spans_a_whole_leap_year():
    """A full-year run is the obvious thing to ask for, so it has to work.

    The 15-minute grid ends at 23:45 on 31 December; hourly inputs must reach
    the closing midnight for that last quarter-hour to be inside their span.
    """
    cfg = build_config("dlr2_der4", start="2024-01-01", days=366)
    index = dataio.simulation_index(cfg)
    assert len(index) == 366 * 96
    for name in ("weather.csv", "reserve_activation.csv"):
        raw = pd.read_csv(cfg.data_dir / name, index_col=0, parse_dates=True)
        assert raw.index[-1] >= index[-1], f"{name} stops short of the year"


def test_duplicate_timestamps_are_an_error(tmp_path):
    rows = "time,activation_up_mw\n2024-01-01 00:00:00+00:00,1\n2024-01-01 00:00:00+00:00,2\n"
    path = tmp_path / "reserve_activation.csv"
    path.write_text(rows, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate timestamps"):
        dataio._read(path)


def test_missing_file_names_the_remedy(tmp_path):
    with pytest.raises(FileNotFoundError, match="generate.py"):
        dataio._read(tmp_path / "weather.csv")
