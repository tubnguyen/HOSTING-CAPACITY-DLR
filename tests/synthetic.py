"""A hand-built per-step result, for tests of what is computed from one.

It carries every column the reports and the matrix runner read, at round values
that can be counted by hand. Keyword arguments replace whole columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def result_frame(n: int = 8, static: bool = False, **columns) -> pd.DataFrame:
    """`static` gives the columns of a static-rating run: 780 A, no weather terms."""
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    nan = [np.nan] * n
    rating = 780.0 if static else 1170.0
    base = {
        "converged": [True] * n, "reg_converged": [True] * n,
        "available_total_mw": [10.0] * n, "curtailed_total_mw": [0.0] * n,
        "p_WF_1_mw": [10.0] * n, "p_WF_2_mw": [0.0] * n,
        "p_WF_3_mw": [0.0] * n, "p_PV_1_mw": [0.0] * n,
        "i_Z1_a": [500.0] * n, "i_Z2_a": [500.0] * n,
        "loading_Z1_pct": [50.0] * n, "loading_Z2_pct": [50.0] * n,
        "rating_Z1_a": [rating] * n, "rating_Z2_a": [rating] * n,
        "rating_Z1_weather_a": nan if static else [2000.0] * n,
        "rating_Z2_weather_a": nan if static else [2000.0] * n,
        "rating_Z1_binding": ["static" if static else "cap"] * n,
        "rating_Z2_binding": ["static" if static else "cap"] * n,
        "rating_headroom_limited": [0] * n,
        "t_cond_Z1_c": nan if static else [40.0] * n,
        "t_cond_Z2_c": nan if static else [40.0] * n,
        "pcc_p_mw": [100.0] * n, "pcc_q_mvar": [-20.0] * n,
        "q_within_window": [1] * n, "q_exceedance_mvar": [0.0] * n,
        "viol_L1": [0] * n, "viol_L2": [0] * n, "viol_L3": [0] * n, "viol_L4": [0] * n,
        "loss_line_mw": [1.0] * n, "loss_trafo_mw": [0.5] * n,
        "oltc_operations": [0] * n, "reactor_operations": [0] * n,
        "oltc_loop_moves": [0] * n, "reactor_loop_moves": [0] * n,
        "q_tracking_error_mvar": [0.1] * n, "curtail_cause": ["none"] * n,
    }
    base.update(columns)
    return pd.DataFrame(base, index=idx)
