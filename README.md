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
* **Export cap** on the dynamic rating: an administrative cap (1.5 × static)
  and the substation equipment rating. The tool records which limit bound at every step.
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
| `<label>_summary.csv` | the summary as tables: DLR vs static (change in units and %), technical cost, line rating, ampacity by month, current duration, checks, storage |
| `<label>_summary.txt` | the same headline numbers as a short text report, also printed |
| `<label>_timeseries.csv` | every quantity per step: flows, voltages, ratings and what bound them, conductor temperature, curtailment and its cause, tap and reactor operations |
| `<label>_violations.csv` | every step still violating a limit after control |
| `<label>_metrics.json` | every headline number, plus the run's full settings |
| `figures/` | rating vs current, ampacity by month, current duration, technical cost, voltage profile, rating drivers (mode 2), battery operation |

A DLR run is compared with a static-rating run that is identical in every other respect.
If a matching one is already in the output folder it is reused; otherwise it is
simulated alongside, in a second process, and saved as a run of its own. The
static case curtails at many more steps, so the first DLR run of a setup takes
about as long as that static run (`--no-compare` skips it).

The technical cost is curtailed energy, network losses and reactive energy
exchanged outside the PCC window. It is given in MWh and MVArh and, at indicative
prices (`--energy-price`, `--reactive-price`), in k€.

The matrix runner adds `matrix_summary.csv` and comparison figures.

## Quick start

```bash
git clone https://github.com/tubnguyen/HOSTING-CAPACITY-DLR
cd HOSTING-CAPACITY-DLR
pip install -e ".[dev]"

corridor-sim --preset dlr2_der4_bess --days 3      # full-weather DLR vs static, all plants, battery
corridor-sim --preset static_der4 --days 3         # static rating only
python scripts/run_matrix.py --days 30 --jobs 8    # all 21 presets in parallel
```

**Every command, option, preset and Python setting: [docs/commands.md](docs/commands.md).**

## Documentation

| File | What is in it |
|---|---|
| [docs/commands.md](docs/commands.md) | all commands and options, presets, Python API |
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
