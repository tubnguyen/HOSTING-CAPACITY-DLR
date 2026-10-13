# DLR Hosting Capacity Simulator

[![CI](https://github.com/tubnguyen/HOSTING-CAPACITY-DLR/actions/workflows/ci.yml/badge.svg)](https://github.com/tubnguyen/HOSTING-CAPACITY-DLR/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Dynamic Line Rating in 110 kV Distribution Network, Increase Hosting Capacity for DERs connection**

## Purposes

Power flow analysis by simulates a period of a 110 kV generation heavy corridor at 15-minute intervals.

* **AC power flow** every step with [pandapower](https://www.pandapower.org/).
* **Line rating** from the IEEE 738 heat balance in three modes: static (0),
  ambient-adjusted (1: measured temperature and sun) and full weather (2: plus wind).
* **Ceilings** on the dynamic rating: an administrative cap (2 × static, the static
  rating of a twin-bundle reinforcement) and the substation equipment rating. The tool
  records which limit bound at every step.
* **Voltage control** by tap changers, a shunt reactor and Q(V) droop.
* **Constraint checks** for over-voltage, under-voltage, thermal overload and the export cap.
* **Minimal curtailment**: proportional curtailment wherever DLR cannot help.
* **Battery** (optional) with state of charge and reserve accounting.

A synthetic data year comes with the repository. 
The weather, demand, wind farms and solar farms, and network are meant to be replaced with own data.

## Example network

- Wind and solar exporting through one 41 km, 110 kV corridor (Al/St 340/30, 780 A,
149 MVA). 
- 330 MW of generation.
- Two rating zones

```
 WF_3 72MW         WF_1 90MW      WF_2 108MW
    │                       ╲     ╱
    │ 20 km           20 km  ╲   ╱ 20 km
    ▼                         ▼ ▼
  SUB_A ══════ TAP_PV ══════ TAP_W ══════ SUB_C ══════ TAP_B ══════ PCC ══> grid
    │   10 km    │    10 km        10 km        10 km    │    1 km
  load           │ PV_1 60MW                             │ BESS 30MW / 60MWh

    └──────────── zone Z1, 30 km ───────────┴──── zone Z2, 11 km ────┘
```

Parameters: [docs/network.md](docs/network.md).

## Input

Four CSV files in one folder (`--data-dir`). All four are required.
The first column is the timestamp; any resolution works and is
interpolated to 15 minutes, each file must cover the whole simulated window.

| File | Columns | Notes |
|---|---|---|
| `weather.csv` | `t_air_c` °C, `u100_ms` `v100_ms` `u10_ms` `v10_ms` m/s, `ghi_wm2` W/m² | one point for the corridor; drives both the rating and the wind farms |
| `load.csv` | `p_sub_a_mw` `q_sub_a_mvar`, `p_sub_b_mw` `q_sub_b_mvar`, `p_agg_mw` `q_agg_mvar`, `q_shunt_a_mvar` | demand at SUB_A, SUB_B and beyond the PCC; capacitor step at SUB_A |
| `pv_generation.csv` | `p_ac_mw` MW | solar plant AC output |
| `reserve_activation.csv` | `activation_up_mw` MW | balancing signal for the battery; zeros are fine |

Wind is given as east (`u`) and north (`v`) components, according to ERA5 format. From a
met mast: `u = -speed·sin(dir)`, `v = -speed·cos(dir)`. There is no wind-power file:
farm output is modelled from `weather.csv` ([`ders.py`](src/corridor_sim/ders.py)).
How the data was built: [data/README.md](data/README.md).

## Output

Each run writes to `runs/<label>/`:

| File | Contents |
|---|---|
| `<label>_summary.csv` | the summary as titled tables: headline, corridor current, power flow by segment, generation by plant, grid interface, voltage, transformer loading, technical cost, monthly (or period) means, current duration, checks, storage |
| `<label>_timeseries.csv` | every quantity per step: flows, voltages, ratings and what bound them, the current before and after curtailment, conductor temperature, curtailment and its cause, tap and reactor operations |
| `<label>_violations.csv` | every step still violating a limit after control |
| `<label>_metrics.json` | every headline number, plus the run's full settings |
| `figures/` | corridor current (15-minute series and monthly means), current duration, power flow, current along the corridor, voltage, rating drivers (mode 2), battery operation |

**Every column name, table and figure explained: [docs/outputs.md](docs/outputs.md).**

The corridor current is reported two ways. The **current from all plants** is what
every connected plant would produce at full available output, read before
curtailment; the **current after curtailment** is what the corridor then carries.
Both are measured against the ratings the corridor is held to: 780 A static on the
as-built conductor, the DLR heat balance and the operative rating above it, and the
1560 A twin-bundle reinforcement.

The figures are built around the first of those and around thermal loading. They
give how far above each rating the generation reaches and for how long, as a share
of the window and in hours, with a second scale reading amperes as a percentage of
the 780 A static rating. The excess over the operative rating is exactly what
curtailment removes, so the exceedance sizes the curtailment without a curve of its
own.

The technical cost - curtailed energy, network losses and reactive energy outside
the PCC window, in MWh and MVArh and, at indicative prices (`--energy-price`,
`--reactive-price`), in k€ - is in the summary tables and the metrics. It has no
figure.

A run reports itself alone. `--compare` also runs the same case at the static rating
and sets the two side by side in the headline and cost tables; the static case is
slow, so it is reused if a matching run is already in the output folder. The figures
describe one run each.

The matrix runner adds `matrix_summary.csv` and comparison figures.

## Quick start

```bash
git clone https://github.com/tubnguyen/HOSTING-CAPACITY-DLR
cd HOSTING-CAPACITY-DLR
pip install -e ".[dev]"

corridor-sim --preset dlr2_der4_bess --days 3            # full-weather DLR, all plants, battery
corridor-sim --preset dlr2_der4_bess --days 3 --compare  # the same, beside the static rating
python scripts/run_matrix.py --days 30 --jobs 8    # all 21 presets in parallel
```

**Every command, option, preset and Python setting: [docs/commands.md](docs/commands.md).**

## Documentation

| File | What is in it |
|---|---|
| [docs/commands.md](docs/commands.md) | all commands and options, presets, Python API |
| [docs/outputs.md](docs/outputs.md) | output files, what every column name means, tables and figures |
| [docs/methodology.md](docs/methodology.md) | methodology |
| [docs/network.md](docs/network.md) | example network and parameters |
| [docs/modules.md](docs/modules.md) | purpose of each Python file |
| [data/README.md](data/README.md) | synthetic dataset |

## Limitations

Planning study only: quasi-static, intact network (no N-1), no protection or
stability, no sag calculation, one weather point per corridor. The shipped site is
cold and windy, so it favours DLR more than a calmer site would.

## License

MIT — see [LICENSE](LICENSE).
