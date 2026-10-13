# Output files and how to read a column name

Every run writes to `runs/<label>/`:

| File | Contents |
|---|---|
| `<label>_summary.csv` | the run as titled tables, in reading order |
| `<label>_timeseries.csv` | every quantity at every 15-minute step |
| `<label>_violations.csv` | only the steps still outside a limit after control |
| `<label>_metrics.json` | the headline numbers, plus the run's full settings |
| `figures/` | the figures listed at the end of this page |

## The two currents

The corridor current is reported twice, and the two are named for what they
are rather than for the curtailment between them.

| Name | What it is |
|---|---|
| **Current from all plants** | the corridor current with every connected plant at its full available output, read before curtailment acts. The demand the corridor is asked to carry. |
| **Current after curtailment** | the same current once curtailment has brought it onto the operative rating. What the corridor carries. |

The gap between them is the curtailment, so the exceedance is enough on its
own: how far and how long the current from all plants stays above a rating is
how much curtailment that rating costs. The figures therefore plot the current
from all plants against each rating, and give every exceedance as a share of
the window and in hours.

**Thermal loading** is the same quantity as a percentage: current ÷ rating ×
100. The current figures carry it as a second scale on the right, against the
as-built single conductor's 780 A, so 150 % means half as much current again
as the conductor is rated to carry.

## Reading a column name in the time series

A column name is built from `quantity_element_unit`. Both halves use short
codes, and the codes are the same everywhere - in the time series, the summary
tables and the network diagram.

### Quantity, the first token

| Token | Quantity | Example |
|---|---|---|
| `v` | bus voltage | `v_PCC_pu` |
| `i` | current | `i_CORR_W_C_a` |
| `p` | active power | `p_WF_1_mw` |
| `q` | reactive power | `q_WF_1_mvar` |
| `loading` | loading against the element's own rating | `loading_T_SUB_B_pct` |
| `rating` | line rating and what set it | `rating_Z1_a` |
| `t_cond`, `t_air` | conductor and air temperature | `t_cond_Z1_c` |
| `available`, `curtailed` | plant output offered, and cut | `curtailed_PV_1_mw` |
| `tap`, `reactor` | actuator position and duty | `tap_T_SUB_A1` |
| `storage` | battery state and dispatch | `storage_soc_pct` |
| `pcc` | the exchange at the grid interface | `pcc_p_mw` |

### Element, the middle token

| Token | Element | Members |
|---|---|---|
| **`CORR`** | **corridor** - a 110 kV segment of the export corridor itself, named after the two points it runs between | `CORR_A_PV` (SUB_A → TAP_PV), `CORR_PV_W`, `CORR_W_C`, `CORR_C_B`, `CORR_B_PCC` |
| **`T`** | **transformer** - a two-winding transformer, named after the substation or plant it belongs to | `T_SUB_A1`, `T_SUB_A2`, `T_SUB_B` (110/20 kV distribution), `T_WF1_1`, `T_WF1_2`, `T_WF2_1`, `T_WF2_2`, `T_WF3_1`, `T_WF3_2` (wind step-up), `T_PV`, `T_BESS` |
| `SUB` | substation busbar | `SUB_A`, `SUB_B`, `SUB_C`, `SUB_E`, and `SUB_A_MV`, `SUB_B_MV` on the 20 kV side |
| `TAP` | tap point on the corridor, where a plant joins it | `TAP_PV`, `TAP_W`, `TAP_B` |
| `PCC` | point of common coupling, the grid interface | `PCC` |
| `LAT`, `CAB`, `SPUR`, `LINK` | lateral overhead line, collector cable, spur, interconnector | `LAT_WF1_TAP`, `CAB_PV` |
| `WF`, `PV`, `BESS` | wind farm, solar plant, battery | `WF_1`, `WF_2`, `WF_3`, `PV_1` |
| `RX` | shunt reactor | `RX_SUB_A`, `RX_SUB_B` |
| **`Z1`, `Z2`** | **rating zone** - the corridor is rated in two zones, each with its own bearing and weather. `Z1` is SUB_A to SUB_C (30 km), `Z2` SUB_C to the PCC (11 km). A zone column is the worst of its segments: the highest current, the lowest rating | `i_Z1_a`, `rating_Z2_a` |

So `i_CORR_W_C_a` is the current in amperes on the corridor segment from
TAP_W to SUB_C, and `loading_T_SUB_A1_pct` is the loading of the first
110/20 kV transformer at SUB_A as a percentage of its own rating.

### Unit, the last token

`_a` amperes · `_ka` kiloamperes · `_mw` megawatts · `_mvar` megavolt-amperes
reactive · `_mva` megavolt-amperes · `_mwh` megawatt-hours · `_pu` per unit ·
`_pct` per cent · `_c` degrees Celsius · `_ms` metres per second · `_deg`
degrees · `_wm2` watts per square metre · `_wm` watts per metre.

### Columns that do not follow the pattern

| Column | Meaning |
|---|---|
| `i_Z1_uncurtailed_a`, `i_Z2_uncurtailed_a` | the zone current before curtailment: the source of the current from all plants |
| `rating_Z1_binding` | which ceiling set the rating at this step: `weather`, `cap`, `equipment` or `static` |
| `rating_Z1_weather_a` | what the heat balance alone allowed, before any ceiling |
| `qc_Z1_wm`, `qr_Z1_wm`, `qs_Z1_wm` | the IEEE 738 heat terms per metre: convection, radiation, solar gain |
| `phi_Z1_deg`, `wind_Z1_ms` | wind angle to the conductor, and the cooling wind speed at it |
| `converged`, `reg_*`, `curtail_*`, `viol_L1`…`viol_L4` | solver, control loop, curtailment and violation diagnostics |

## Summary tables

In the order they appear in `<label>_summary.csv`: `RUN`, `HEADLINE`,
`CORRIDOR CURRENT`, `CORRIDOR POWER FLOW`, `GENERATION BY PLANT`,
`GRID INTERFACE (PCC)`, `VOLTAGE`, `TRANSFORMER LOADING`, `TECHNICAL COST`,
`RATING SET BY`, `MONTHLY MEANS` (or `PERIOD MEANS` under four weeks),
`CURRENT DURATION`, `CHECKS`, `STORAGE`. Each carries a one-line note on the
title row. With `--compare` the headline and cost tables gain a static column,
a DLR column and the change in both the quantity's own unit and in per cent.

## Figures

| Figure | What it shows |
|---|---|
| `<label>_current.png` | the current from all plants at 15 minutes against every rating, with the exceedance shaded; below it, the monthly mean with the range each month covered |
| `<label>_duration.png` | how long that current stays above each rating, in per cent of the window and in hours |
| `<label>_power.png` | active power from the plants to the grid, and reactive power at the interface |
| `<label>_segments.png` | mean and maximum current on each corridor segment |
| `<label>_voltage.png` | voltage at every corridor bus |
| `<label>_rating_drivers.png` | heat-balance ampacity against wind and temperature (full-weather runs only) |
| `<label>_storage.png` | interface export and state of charge (runs with a battery) |

The technical cost has no figure. It is three numbers on four quantities, so
it reads better as the `TECHNICAL COST` table in the summary and the
`cost_*_keur` entries in the metrics than as a bar chart.
