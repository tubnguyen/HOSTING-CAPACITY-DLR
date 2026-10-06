# DLR Hosting Capacity Simulator

[![CI](https://github.com/tubnguyen/HOSTING-CAPACITY-DLR/actions/workflows/ci.yml/badge.svg)](https://github.com/tubnguyen/HOSTING-CAPACITY-DLR/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**How much wind and solar can a 110 kV line export, and how much extra does dynamic line
rating (DLR) actually let you use?**

## What it does

Simulates a year (or any window) of a 110 kV export corridor at 15-minute steps and
reports how much generation was delivered, how much had to be curtailed, and why.

* **AC power flow** every step with [pandapower](https://www.pandapower.org/).
* **Line rating** from the IEEE 738 heat balance in three modes: static (0),
  ambient-adjusted (1: measured temperature and sun) and full weather (2: plus wind).
* **Real-world ceilings** on the dynamic rating: an administrative cap (1.5 × static)
  and the substation equipment rating. The tool records which limit bound at every step.
* **Voltage control** by tap changers, a shunt reactor and plant Q(V) droop.
* **Constraint checks** for over-voltage, under-voltage, thermal overload and the export cap.
* **Minimal curtailment**: the smallest pro-rata cut that clears every violation.
* **Battery** (optional) with state of charge and reserve accounting.

A synthetic year of data ships with the repository, so it runs out of the box. The
weather, demand, plants and network are all meant to be replaced with your own.

![Corridor rating against the current carried](docs/figures/rating.png)

*Dashed: what the weather alone allowed. Solid: what the scheme could use after the
cap and the substation equipment. The gap is headroom the weather offered but the
scheme could not take.*

## Example network

Wind and solar exporting through one 41 km, 110 kV corridor (Al/St 340/30, 780 A,
149 MVA) against 330 MW of generation. Round, generic distances; two rating zones
with selectable bearings.

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

Four CSV files in one folder (`--data-dir`). All four are required in every mode.
The first column is the timestamp (read as UTC); any resolution works and is
interpolated to 15 minutes, but each file must cover the whole simulated window.

| File | Columns | Notes |
|---|---|---|
| `weather.csv` | `t_air_c` °C, `u100_ms` `v100_ms` `u10_ms` `v10_ms` m/s, `ghi_wm2` W/m² | one point for the corridor; drives both the rating and the wind farms |
| `load.csv` | `p_sub_a_mw` `q_sub_a_mvar`, `p_sub_b_mw` `q_sub_b_mvar`, `p_agg_mw` `q_agg_mvar`, `q_shunt_a_mvar` | demand at SUB_A, SUB_B and beyond the PCC; capacitor step at SUB_A |
| `pv_generation.csv` | `p_ac_mw` MW | solar plant AC output |
| `reserve_activation.csv` | `activation_up_mw` MW | balancing signal for the battery; zeros are fine |

Wind is given as east (`u`) and north (`v`) components, as ERA5 publishes them. From a
met mast: `u = -speed·sin(dir)`, `v = -speed·cos(dir)`. There is no wind-power file:
farm output is modelled from `weather.csv` ([`ders.py`](src/corridor_sim/ders.py)).
How the shipped data was built: [data/README.md](data/README.md).

## Output

Each run writes to `runs/<label>/`:

| File | Contents |
|---|---|
| `<label>_timeseries.csv` | every quantity per step: flows, voltages, ratings and what bound them, conductor temperature, curtailment and its cause, tap and reactor operations |
| `<label>_violations.csv` | every step still violating a limit after control |
| `<label>_seasonal.csv` | energy, curtailment and rating by season |
| `<label>_metrics.json` | headline numbers: available, delivered and curtailed energy, ratings, hours over each limit |
| `<label>_summary.txt` | the same numbers as a readable report (also printed) |
| `figures/` | rating vs current, voltage profile, rating drivers (mode 2), battery operation |

The matrix runner adds `matrix_summary.csv` and comparison figures.

## Quick start

```bash
git clone https://github.com/tubnguyen/HOSTING-CAPACITY-DLR
cd HOSTING-CAPACITY-DLR
pip install -e ".[dev]"

corridor-sim --preset dlr2_der4_bess --days 3      # full-weather DLR, all plants, battery
corridor-sim --preset static_der4 --days 3         # same window, static rating
python scripts/run_matrix.py --days 30 --jobs 8    # all 21 presets in parallel
```

**Every command, option, preset and Python setting: [docs/commands.md](docs/commands.md).**

## Documentation

| File | What is in it |
|---|---|
| [docs/commands.md](docs/commands.md) | all commands and options, presets, Python API |
| [docs/methodology.md](docs/methodology.md) | the method in brief |
| [docs/network.md](docs/network.md) | the example network and its parameters |
| [docs/modules.md](docs/modules.md) | what each Python file does |
| [data/README.md](data/README.md) | how the synthetic dataset is built |

## Limitations

Planning study only: quasi-static, intact network (no N-1), no protection or
stability, no sag calculation, one weather point per corridor. The shipped site is
cold and windy, so it favours DLR more than a calmer site would.

## License

MIT — see [LICENSE](LICENSE).
