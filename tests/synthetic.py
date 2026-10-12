"""A hand-built per-step result, for tests of what is computed from one.

It carries every column the reports and the matrix runner read, at round values
that can be counted by hand. Keyword arguments replace whole columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PLANTS = ("WF_1", "WF_2", "WF_3", "PV_1")
SEGMENTS = ("CORR_A_PV", "CORR_PV_W", "CORR_W_C", "CORR_C_B", "CORR_B_PCC")
BUSES = ("PCC", "SUB_C", "TAP_W", "TAP_PV", "SUB_A", "SUB_B", "SUB_E", "TAP_B",
         "SUB_A_MV", "SUB_B_MV")
TRANSFORMERS = ("T_SUB_A1", "T_SUB_A2", "T_SUB_B", "T_PV", "T_BESS", "T_WF1_1", "T_WF1_2",
                "T_WF2_1", "T_WF2_2", "T_WF3_1", "T_WF3_2")


def result_frame(n: int = 8, static: bool = False, start: str = "2024-01-01",
                 **columns) -> pd.DataFrame:
    """`static` gives the columns of a static-rating run: 780 A, no weather terms."""
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    nan = [np.nan] * n
    rating = 780.0 if static else 1170.0
    base = {
        "converged": [True] * n, "reg_converged": [True] * n,
        "available_total_mw": [10.0] * n, "curtailed_total_mw": [0.0] * n,
        "i_Z1_a": [500.0] * n, "i_Z2_a": [500.0] * n,
        "i_Z1_uncurtailed_a": [500.0] * n, "i_Z2_uncurtailed_a": [500.0] * n,
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
    for plant in PLANTS:
        output = 10.0 if plant == "WF_1" else 0.0
        base[f"p_{plant}_mw"] = [output] * n
        base[f"available_{plant}_mw"] = [output] * n
        base[f"curtailed_{plant}_mw"] = [0.0] * n
    for line in SEGMENTS:
        base[f"i_{line}_a"] = [500.0] * n
        base[f"p_{line}_mw"] = [90.0] * n
    for bus in BUSES:
        base[f"v_{bus}_pu"] = [1.0] * n
    for name in TRANSFORMERS:
        base[f"loading_{name}_pct"] = [40.0] * n
    base.update(columns)
    return pd.DataFrame(base, index=idx)
