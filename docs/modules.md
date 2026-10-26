# What each Python file does

## The simulator: `src/corridor_sim/`

| File | Purpose |
|---|---|
| [`__init__.py`](../src/corridor_sim/__init__.py) | Package marker and version number. |
| [`cli.py`](../src/corridor_sim/cli.py) | The `corridor-sim` command. `run_scenario()` builds, simulates, reports and plots one scenario, and on request its static reference alongside. |
| [`interactive.py`](../src/corridor_sim/interactive.py) | Interactive mode: asks for the scenario question by question, checks every answer against its options, and prints the equivalent command. |
| [`config.py`](../src/corridor_sim/config.py) | The immutable `Config` holding every run setting, added plants and loads, the 21 presets, validation, the command-line parser and its inverse `to_argv`. |
| [`constants.py`](../src/corridor_sim/constants.py) | Every network and physical parameter: voltages, conductor data, line lengths, plant sizes, transformers, control settings, limits. Edit this for your own network. |
| [`network.py`](../src/corridor_sim/network.py) | Builds the pandapower model of the corridor, with any added plants, loads and midpoint connections; topology is one readable function. |
| [`dataio.py`](../src/corridor_sim/dataio.py) | Reads the four input CSVs, checks they cover the window, and interpolates them onto the 15-minute grid. |
| [`ders.py`](../src/corridor_sim/ders.py) | Turns the weather into wind farm output, for the built-in farms and added ones: hub-height wind, air density, a 6 MW power curve, wake losses. |
| [`dlr.py`](../src/corridor_sim/dlr.py) | IEEE 738 line rating, forward (ampacity) and inverse (conductor temperature), plus the cap and equipment ceilings. |
| [`controls.py`](../src/corridor_sim/controls.py) | Per-step voltage control: shunt reactor, tap changer and plant Q(V) droop, plus the robust power-flow solve. |
| [`constraints.py`](../src/corridor_sim/constraints.py) | Scans the four constraint levels (over-voltage, under-voltage, thermal, export cap) and measures PCC export. |
| [`curtailment.py`](../src/corridor_sim/curtailment.py) | Finds the smallest pro-rata generation cut that clears every actionable violation, shared by the plants that relieve it. |
| [`storage.py`](../src/corridor_sim/storage.py) | Battery dispatch, state of charge and reserve accounting. |
| [`simulate.py`](../src/corridor_sim/simulate.py) | The 15-minute time loop that ties everything together and records one row per step. |
| [`reference.py`](../src/corridor_sim/reference.py) | The static-rating run a DLR run is compared against: its settings, fingerprint and lookup for reuse. |
| [`report.py`](../src/corridor_sim/report.py) | Turns the time series into metrics, the summary CSV of network, cost and check tables, the console summary and the violation table. |
| [`plots.py`](../src/corridor_sim/plots.py) | Figures for one run (network topology, corridor current, current duration, power flow, current along the corridor, voltage, rating drivers, storage) and for the scenario matrix. |

## Scripts and data

| File | Purpose |
|---|---|
| [`scripts/run_matrix.py`](../scripts/run_matrix.py) | Runs many presets in parallel, pairs each DLR run with its static run, and builds the comparison table and figures. |
| [`data/generate.py`](../data/generate.py) | Generates the synthetic one-year dataset from a fixed seed. |

## Tests: `tests/`

| File | Purpose |
|---|---|
| [`conftest.py`](../tests/conftest.py) | Shared fixtures: a default config and a solved, moderately loaded network. |
| [`synthetic.py`](../tests/synthetic.py) | A hand-built per-step result for the report and matrix tests. |
| [`test_config.py`](../tests/test_config.py) | Presets, validation and command-line parsing. |
| [`test_network.py`](../tests/test_network.py) | The network builds, solves, and has the stated distances and elements. |
| [`test_dataio.py`](../tests/test_dataio.py) | Input coverage checks, interpolation and duplicate timestamps. |
| [`test_dlr.py`](../tests/test_dlr.py) | The IEEE 738 model: static calibration, forward/inverse agreement, ceilings. |
| [`test_controls.py`](../tests/test_controls.py) | Droop, move budgets, the reactor guard and the reactive power sign conventions. |
| [`test_constraints.py`](../tests/test_constraints.py) | Each constraint level is detected and under-voltage never hides another violation. |
| [`test_curtailment.py`](../tests/test_curtailment.py) | Curtailment clears violations, is close to minimal, takes the same share of every plant's output, and leaves alone what a large load absorbs. |
| [`test_storage.py`](../tests/test_storage.py) | Battery efficiency, state-of-charge limits and reserve shortfall. |
| [`test_report.py`](../tests/test_report.py) | Metrics, technical cost, summary tables and text are computed correctly. |
| [`test_reference.py`](../tests/test_reference.py) | The static reference is found only when settings, window, data and model all match. |
| [`test_matrix.py`](../tests/test_matrix.py) | The matrix runner collects and tabulates results, and pairs DLR runs with static ones. |
| [`test_topology.py`](../tests/test_topology.py) | The topology figure shows what the run connects, leaves switched-off plants out, and never lets two markers overlap. |
| [`test_interactive.py`](../tests/test_interactive.py) | Interactive mode: answers become the right run, unmatched answers are asked again, defaults and cancelling. |
| [`test_smoke.py`](../tests/test_smoke.py) | Short end-to-end runs: convergence, energy balance, ratings respected, files written, static reference made then reused, added plants and loads. |
