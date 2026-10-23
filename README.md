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
* **Minimal curtailment** wherever DLR cannot help: every plant cut gives up the same
  share of its instantaneous output, and only plants feeding the constraint are cut.
  A large load takes the generation it can use before anything is curtailed.
* **Battery** (optional) with state of charge and reserve accounting.
* **Added plants and large loads** of any size, wind or solar, at five connection
  points along the corridor: SUB_A, midway to SUB_C, SUB_C, midway to the PCC, or the PCC.
* **Interactive setup**: run `corridor-sim` with no options and it asks for the
  scenario question by question, then prints the equivalent command.

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

Added plants and loads connect at one of five points, measured from SUB_A:

```
  SUB_A ──────── MID_A_C ──────── SUB_C ──── MID_C_PCC ──── PCC
   0 km           15 km           30 km       35.5 km       41 km
```

Parameters: [docs/network.md](docs/network.md).

## Quick start

```bash
git clone https://github.com/tubnguyen/HOSTING-CAPACITY-DLR
cd HOSTING-CAPACITY-DLR
pip install -e ".[dev]"

corridor-sim                                             # asks for the scenario, step by step
corridor-sim --preset dlr2_der4_bess --days 3            # full-weather DLR, all plants, battery
corridor-sim --preset dlr2_der4_bess --days 3 --compare  # the same, beside the static rating
corridor-sim --dlr 2 --add-der wind:50@SUB_C --add-load 80@MID_A_C --days 7   # add a plant and a load
python scripts/run_matrix.py --days 30 --jobs 8    # all 21 presets in parallel
```

Run with no options in a terminal, `corridor-sim` asks which plants to connect,
what to add and where, battery or not, the conductor, the rating mode and the
window. Each question lists its options and a default (Enter takes it); an answer
that matches no option is asked again. Before anything runs it prints the same run
as a command, to repeat it without the questions. `corridor-sim -i --preset ...`
asks too, offering the preset as the defaults.

If ```corridor-sim``` does not work, try:
```bash
python -m corridor_sim.cli --preset dlr2_der4_bess --days 3
```

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
| `<label>_summary.csv` | the summary as titled tables: headline, corridor current, power flow by segment, generation by plant, grid interface, added loads, voltage, transformer loading, technical cost, monthly (or period) means, current duration, checks, storage |
| `<label>_timeseries.csv` | every quantity per step: flows, voltages, ratings and what bound them, the current before and after curtailment, conductor temperature, curtailment and its cause, tap and reactor operations |
| `<label>_violations.csv` | every step still violating a limit after control |
| `<label>_metrics.json` | every headline number, plus the run's full settings |
| `figures/` | corridor current (15-minute series and monthly means), current duration, power flow, current along the corridor, voltage, rating drivers (mode 2), battery operation |

**Every column name, table and figure explained: [docs/outputs.md](docs/outputs.md).**

The **current from all plants** is current all DERs would produce at full available output,
before curtailment.
The **current after curtailment** is current conductors able to carry.
Compared with: 780 A static rating single conductor, DLR uplift conductor,
and the 1560 A bundle conductor reinforcement.

The technical cost - curtailed energy, network losses and reactive energy outside
the PCC window, in MWh and MVArh and, at indicative prices (`--energy-price`,
`--reactive-price`), in k€ - is in the summary tables and the metrics. 

A run reports itself. `--compare` also runs the same case at the static rating
and sets the two side by side in the headline and cost tables; the static case is
slow, so it is reused if a matching run is already in the output folder. The figures
describe one run each.

The matrix runner adds `matrix_summary.csv` and comparison figures.


**Every command, option, preset and Python setting: [docs/commands.md](docs/commands.md).**

## Documentation

| File | Content |
|---|---|
| [docs/commands.md](docs/commands.md) | all commands and options, presets, Python API |
| [docs/outputs.md](docs/outputs.md) | output files, what every column name means, tables and figures |
| [docs/methodology.md](docs/methodology.md) | methodology |
| [docs/network.md](docs/network.md) | example network and parameters |
| [docs/modules.md](docs/modules.md) | purpose of each Python file |
| [data/README.md](data/README.md) | synthetic dataset |

## Limitations

Planning study only: intact network (no N-1), no protection or
stability, no sag calculation, one weather point per corridor. Added loads are
constant demand, with no flexibility to relieve the corridor. The shipped site is
cold and windy, so it favours DLR more than a calmer site would.

## License

MIT — see [LICENSE](LICENSE).
