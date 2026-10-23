# Commands and options

Everything you can type to run the simulator, with every option and its default.

## Install

```bash
git clone https://github.com/tubnguyen/HOSTING-CAPACITY-DLR
cd HOSTING-CAPACITY-DLR
# add numba for a several-fold faster power flow
pip install -e ".[dev]"       # editable mode and install dependencies
```

## One scenario: `corridor-sim`

```bash
corridor-sim [options]
# OR
python -m corridor_sim.cli [options]
```

Default run is 30 days from 2024-01-01: single
conductor, ambient-adjusted rating (mode 1), all four plants, droop control, no
battery, both zones at a 45° bearing. A `--preset` sets a named scenario, and any
flag given with it overrides the preset.

### Answering questions instead: interactive mode

```bash
corridor-sim                                  # no options, in a terminal
corridor-sim -i                               # the same, anywhere
corridor-sim -i --preset dlr2_der4_bess       # the preset's settings are the defaults offered
```

With no options in a terminal, or with `-i` / `--interactive`, the scenario is asked
for one question at a time:

| Step | Question | Options |
|---|---|---|
| 1 | Plants already on the corridor | `all`, `none`, or names such as `WF_1,PV_1` |
| 2 | Added plants | how many (0 to 10); for each: `wind` or `solar`, rating in MW, connection point |
| 3 | Large loads | how many (0 to 10); for each: demand in MW, connection point |
| 4 | Battery | `yes` or `no` |
| 5 | Corridor conductor | `single` or `twin` |
| 6 | Line rating | `0` static, `1` ambient-adjusted, `2` full weather (or `static`, `ambient`, `full`) |
| 7 | Time window | first day and number of days, inside the input data |
| 8 | Outputs | compare with the static rating (DLR only), figures, name of the run |

Each question shows its default in brackets; Enter takes it. A connection point can
be typed by name or by its number in the list. An answer that matches no option is
not guessed at: the question is asked again, with what it accepts. At the end the
run is summarised and printed as the equivalent command, for example

```text
corridor-sim --dlr 2 --add-der wind:50@SUB_C --add-load 80@MID_A_C --storage --days 7
```

and nothing runs until you confirm. Ctrl+C stops at any point. Without a terminal,
as in a script or a pipe, `corridor-sim` with no options runs the default scenario
as it always has.

### Scenario

| Option | Values | Default | What it does |
|---|---|---|---|
| `--preset NAME` | see [Presets](#presets) | none | start from a named scenario |
| `--conductor` | `single`, `twin` | `single` | as-built conductor or twin-bundle reconductoring |
| `--der LIST` | comma list of `WF_1`, `WF_2`, `WF_3`, `PV_1`, or `all`, `none` | all four | built-in plants connected, e.g. `--der WF_1,WF_2` |
| `--add-der KIND:MW@POINT` | `wind` or `solar`, MW, connection point | none | add a plant, e.g. `--add-der wind:50@SUB_C`; repeat for more |
| `--add-load MW@POINT[:PF]` | MW, connection point, optional lagging power factor | none | add a constant large load, e.g. `--add-load 80@MID_A_C`; repeat for more |
| `-i`, `--interactive` | flag | off | ask for the scenario question by question (above) |
| `--control` | `droop`, `cosphi` | `droop` | plant reactive power: Q(V) droop or fixed cos φ 0.95 |
| `--storage` | flag | off | connect the 30 MW / 60 MWh battery |
| `--no-curtailment` | flag | on | record violations but never curtail |
| `--export-cap MW` | number | `250` | contracted export limit at the PCC |

### Line rating

| Option | Values | Default | What it does |
|---|---|---|---|
| `--dlr` | `0`, `1`, `2` | `1` | 0 static, 1 ambient-adjusted, 2 full weather |
| `--dlr-cap-ratio X` | number ≥ 1.0 | `2.0` | administrative cap on the dynamic rating, as a multiple of static |
| `--no-rating-cap` | flag | off | remove the administrative cap (cannot be combined with `--dlr-cap-ratio`) |
| `--no-equipment-limit` | flag | off | ignore the series substation equipment rating |
| `--azimuth DEG` | `0`, `30`, `45`, `60`, `90` | — | bearing of both zones (0 north–south, 90 east–west) |
| `--azimuth-z1 DEG` | same set | `45` | bearing of zone Z1 only; wins over `--azimuth` |
| `--azimuth-z2 DEG` | same set | `45` | bearing of zone Z2 only; wins over `--azimuth` |
| `--wind-factor X` | 0 < X ≤ 1 | `0.4` | share of the conductor-height wind counted as cooling |

`POINT` is one of `SUB_A` (0 km), `MID_A_C` (15 km, midway to SUB_C), `SUB_C`
(30 km), `MID_C_PCC` (35.5 km, midway to the PCC) or `PCC` (41 km), in any case.
Added plants are named `WF_4`, `WF_5`, … and `PV_2`, `PV_3`, … in the order given,
added loads `LOAD_1`, `LOAD_2`, …. How they are modelled:
[network.md](network.md#added-plants-and-loads).

The bearing and the wind factor only change the result in mode 2. The wind factor
stands for the least-cooled span: sheltered by trees and terrain, with the wind
parallel.

### Time window and files

| Option | Values | Default | What it does |
|---|---|---|---|
| `--start DATE` | `YYYY-MM-DD` | `2024-01-01` | first day simulated (UTC) |
| `--days N` | integer > 0 | `30` | window length |
| `--end DATE` | `YYYY-MM-DD` | — | explicit end; overrides `--days` |
| `--data-dir PATH` | folder | `data/` | folder holding the four input CSVs |
| `--out PATH` | folder | `runs/` | results go to `<out>/<label>/` |
| `--label NAME` | text | auto | output name; auto is `<conductor>_der<n>_<control>_dlr<mode>_bess<0/1>`, where `n` counts every plant connected, then one tag per added plant and load, e.g. `_wf50-SUB_C_ld80-PCC` |
| `--no-plots` | flag | plots on | skip the figures |
| `--compare` | flag | off | also run the static case and compare (slow the first time) |

What each output file holds, and what every column name means:
[outputs.md](outputs.md).

### Technical cost prices

| Option | Values | Default | What it does |
|---|---|---|---|
| `--energy-price EUR` | number ≥ 0 | `50` | €/MWh for curtailed energy and network losses |
| `--reactive-price EUR` | number ≥ 0 | `5` | €/MVArh for reactive energy outside the PCC window (±33 MVAr, wider with added plants) |

Both are round placeholders for the cost columns of the summary; they change no
simulated result, and there is no cost figure to redraw.

A bad flag or a dataset that does not cover the window exits with code 2 and a
one-line message.

The power flow tries several starting points when a step is hard to solve. A start
that diverges is expected and is not reported on the console; a step that no start
solves is counted under "Steps that did not converge" in the summary's `CHECKS`
table.

### Presets

21 presets, named `<rating>_der<n>[_bess]`:

| Part | Options | Meaning |
|---|---|---|
| rating | `static`, `dlr1`, `dlr2`, `twin` | single conductor at mode 0, 1 or 2; twin bundle at mode 0 |
| `der<n>` | `der1` … `der4` | `WF_2`, then `+WF_1`, then `+WF_3`, then `+PV_1` |
| `_bess` | only with `der4` | battery connected |

Plus `baseline`: static rating, no generation. Every preset uses droop control.

### Examples

```bash
# a quick look: full-weather DLR, all plants, battery, three days
corridor-sim --preset dlr2_der4_bess --days 3

# Different DLR mode
corridor-sim --dlr 0 --der WF_1,WF_2,WF_3 --days 365
corridor-sim --dlr 1 --der WF_1,WF_2,WF_3 --days 365
corridor-sim --dlr 2 --der WF_1,WF_2,WF_3 --days 365

# Affect of angle
corridor-sim --dlr 2 --azimuth 0 --days 365
corridor-sim --dlr 2 --azimuth-z1 0 --azimuth-z2 90 --days 365

# Reinforcement mode
corridor-sim --dlr 0 --conductor twin --der WF_1,WF_2,WF_3 --days 365

# your own data, a named output (its static reference is my_case_static)
corridor-sim --data-dir /path/to/my_data --dlr 2 --start 2024-01-01 --days 365 --label my_case

# Set your own electricity price
corridor-sim --preset dlr2_der4 --days 3 --compare --energy-price 80

# Affect of wind speed
corridor-sim --dlr 2 --wind-factor 0.6 --days 30

# Where a new 50 MW wind farm connects: head of the corridor, or the grid interface
corridor-sim --dlr 2 --add-der wind:50@SUB_A --days 30
corridor-sim --dlr 2 --add-der wind:50@PCC --days 30

# Two added plants and a large load midway along zone Z1
corridor-sim --dlr 2 --add-der wind:50@SUB_C --add-der solar:30@MID_C_PCC --add-load 80@MID_A_C --days 30

# Only the added plants: none of the built-in four
corridor-sim --der none --add-der wind:120@MID_A_C --days 30
```

## The whole matrix: `scripts/run_matrix.py`

Runs presets in parallel, then writes `matrix_summary.csv` and the comparison figures.
With `--compare`, once every run has finished each DLR run's summary is rewritten
against the static preset with the same plants and storage (`dlr2_der4` against
`static_der4`), so no static run is simulated twice. The figures describe one run
each and are unaffected.

```bash
python scripts/run_matrix.py --days 30 --jobs 8
python scripts/run_matrix.py --only static_der4 dlr2_der4 --days 7
python scripts/run_matrix.py --collect-only      # rebuild table and figures from saved runs
```

| Option | Default | Purposes |
|---|---|---|
| `--days N` | `30` | window length for every scenario |
| `--start DATE` | `2024-01-01` | first day |
| `--jobs N` | CPU count − 1 | scenarios run at once |
| `--only P [P ...]` | all 21 | run only these presets |
| `--out PATH` | `runs/` | where scenario folders and `matrix_summary.csv` go |
| `--data-dir PATH` | `data/` | input folder |
| `--figures PATH` | `<out>/figures/` | where the comparison figures go |
| `--no-plots` | plots on | skip the per-scenario figures |
| `--collect-only` | off | skip running; rebuild the table and figures from `--out` |
| `--compare` | off | rewrite each DLR run's summary against its static preset |

## Regenerate the dataset: `data/generate.py`

```bash
python data/generate.py --year 2024 --seed 20240101
```

| Option | Default | What it does |
|---|---|---|
| `--year` | `2024` | calendar year to generate |
| `--seed` | `20240101` | random seed; the same seed gives byte-identical files |
| `--pv-mw` | `60` | solar plant rating used for `pv_generation.csv` |
| `--out` | `data/` | output folder |

## From Python

```python
from corridor_sim.cli import run_scenario
from corridor_sim.config import build_config

if __name__ == "__main__":
    cfg = build_config("dlr2_der4", days=365, data_dir="/path/to/my_data",
                       azimuth_z1_deg=45.0, export_cap_mw=200.0, label="my_case",
                       added_der=["wind:50@SUB_C", {"kind": "solar", "mw": 30, "at": "PCC"}],
                       added_loads=[{"mw": 80, "at": "MID_A_C", "pf": 0.95}])
    metrics, paths = run_scenario(cfg, make_plots=True, progress=True, compare=True)
```

From Python the static comparison is off unless `compare=True` is given. It starts
a second process, so keep the call under `if __name__ == "__main__":` in a script.

`build_config(preset, **fields)` applies the field defaults, then the preset, then
your overrides, and validates the result. Every CLI flag maps to a field
(`--dlr` → `dlr_mode`, `--der` → `der_enabled`, `--add-der` → `added_der`,
`--add-load` → `added_loads`, and so on). An added plant or load is given as the
option's text, as a dict, or as an `AddedPlant` / `AddedLoad` from `corridor_sim.config`;
a dict may name it (`"name": "WF_7"`), otherwise it is numbered on. `to_argv(cfg)`
gives the options that rebuild a configuration. These fields are only reachable
from Python:

| Field | Default | Options / meaning |
|---|---|---|
| `wf_trafo_units` | `2` | step-up units per wind farm; `1` is the single-unit outage case |
| `wf_trafo_rating` | `"onaf"` | `"onaf"` 63 MVA forced cooling, `"onan"` 50 MVA natural |
| `pv_control_mode` | `None` | `"droop"`, `"cosphi"`; `None` follows `control_mode` |
| `cosphi_sign` | `"absorb"` | `"absorb"`, `"inject"` for cos φ control |
| `droop_measurement` | `"local"` | `"local"`, `"pilot_tap_w"`, `"pilot_sub_a"`: bus the droop watches |
| `droop_p_min_frac` | `0.05` | droop is switched off below this share of rated output |
| `conductor_height_m` | `15.0` | conductor height for the wind profile |
| `roughness_m` | `0.30` | surface roughness for the wind profile |
| `displacement_m` | `0.0` | zero-plane displacement for the wind profile |
| `low_wind_ms` | `0.6` | fixed wind speed used by mode 1 |
| `site_elevation_m` | `100.0` | site elevation for air density |
| `t_cond_max_c` | `80.0` | conductor design temperature |
| `export_cap_basis` | `"net"` | `"net"` measured at the PCC, `"gross"` generation less corridor losses |
| `storage_p_mw`, `storage_e_mwh` | `30`, `60` | battery power and energy |
| `storage_soc_init` | `0.5` | starting state of charge, 0 to 1 |
| `storage_connection` | `"tie"` | `"tie"` via its own transformer, `"direct"` on the 110 kV tap |
| `storage_charge_source` | `"surplus_then_grid"` | `"surplus_only"`, `"grid_only"` |
| `storage_reserve_mw`, `storage_contract_mw` | `30`, `30` | reserve held and contracted delivery |
| `storage_q_mode` | `"droop"` | `"cosphi"`, `"fixed"`, `"unity"` |
| `storage_may_curtail_der` | `False` | let battery delivery displace generation at the export cap |

Network and physical parameters (line lengths, conductor data, plant sizes,
transformers, control deadbands) are constants in
[`constants.py`](../src/corridor_sim/constants.py); edit that file for your own network.

## Tests and lint

```bash
python -m pytest -q           # about six minutes
ruff check src tests scripts data
```
