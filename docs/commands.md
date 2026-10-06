# Commands and options

Everything you can type to run the simulator, with every option and its default.

## Install

```bash
git clone https://github.com/tubnguyen/HOSTING-CAPACITY-DLR
cd HOSTING-CAPACITY-DLR
pip install -e ".[dev]"       # add numba for a several-fold faster power flow
```

## One scenario: `corridor-sim`

```bash
corridor-sim [options]        # same as: python -m corridor_sim.cli [options]
```

With no options it runs 30 days from 2024-01-01 on the shipped data: single
conductor, ambient-adjusted rating (mode 1), all four plants, droop control, no
battery. A `--preset` sets a named scenario, and any flag given with it overrides
the preset.

### Scenario

| Option | Values | Default | What it does |
|---|---|---|---|
| `--preset NAME` | see [Presets](#presets) | none | start from a named scenario |
| `--conductor` | `single`, `twin` | `single` | as-built conductor or twin-bundle reconductoring |
| `--der LIST` | comma list of `WF_1`, `WF_2`, `WF_3`, `PV_1` | all four | plants connected, e.g. `--der WF_1,WF_2` |
| `--control` | `droop`, `cosphi` | `droop` | plant reactive power: Q(V) droop or fixed cos φ 0.95 |
| `--storage` | flag | off | connect the 30 MW / 60 MWh battery |
| `--no-curtailment` | flag | on | record violations but never curtail |
| `--export-cap MW` | number | `250` | contracted export limit at the PCC |

### Line rating

| Option | Values | Default | What it does |
|---|---|---|---|
| `--dlr` | `0`, `1`, `2` | `1` | 0 static, 1 ambient-adjusted, 2 full weather |
| `--dlr-cap-ratio X` | number ≥ 1.0 | `1.5` | administrative cap on the dynamic rating, as a multiple of static |
| `--no-rating-cap` | flag | off | remove the administrative cap (cannot be combined with `--dlr-cap-ratio`) |
| `--no-equipment-limit` | flag | off | ignore the series substation equipment rating |
| `--azimuth DEG` | `0`, `30`, `45`, `60`, `90` | — | bearing of both zones (0 north–south, 90 east–west) |
| `--azimuth-z1 DEG` | same set | `90` | bearing of zone Z1 only; wins over `--azimuth` |
| `--azimuth-z2 DEG` | same set | `60` | bearing of zone Z2 only; wins over `--azimuth` |

The bearing only changes the result in mode 2.

### Time window and files

| Option | Values | Default | What it does |
|---|---|---|---|
| `--start DATE` | `YYYY-MM-DD` | `2024-01-01` | first day simulated (UTC) |
| `--days N` | integer > 0 | `30` | window length |
| `--end DATE` | `YYYY-MM-DD` | — | explicit end; overrides `--days` |
| `--data-dir PATH` | folder | `data/` | folder holding the four input CSVs |
| `--out PATH` | folder | `runs/` | results go to `<out>/<label>/` |
| `--label NAME` | text | auto | output name; auto is `<conductor>_der<n>_<control>_dlr<mode>_bess<0/1>` |
| `--no-plots` | flag | plots on | skip the figures |

A bad flag or a dataset that does not cover the window exits with code 2 and a
one-line message.

### Presets

21 presets, named `<rating>_der<n>[_bess]`:

| Part | Options | Meaning |
|---|---|---|
| rating | `static`, `dlr1`, `dlr2`, `twin` | single conductor at mode 0, 1 or 2; twin bundle at mode 0 |
| `der<n>` | `der1` … `der4` | `WF_2`, then `+WF_1`, then `+WF_3`, then `+PV_1` |
| `_bess` | only with `der4` | battery connected |

Plus `baseline`: static rating, no generation, cos φ control. Every preset uses droop
control except `baseline`.

### Examples

```bash
# a quick look: full-weather DLR, all plants, battery, three days
corridor-sim --preset dlr2_der4_bess --days 3

# which rating scheme is worth buying
corridor-sim --dlr 0 --der WF_1,WF_2,WF_3 --days 365
corridor-sim --dlr 1 --der WF_1,WF_2,WF_3 --days 365
corridor-sim --dlr 2 --der WF_1,WF_2,WF_3 --days 365

# how much the line's direction matters
corridor-sim --dlr 2 --azimuth 0 --days 365
corridor-sim --dlr 2 --azimuth-z1 0 --azimuth-z2 90 --days 365

# is it the permit or the switchgear that limits the uplift?
corridor-sim --dlr 2 --no-rating-cap --days 365
corridor-sim --dlr 2 --no-equipment-limit --days 365

# what reconductoring would buy
corridor-sim --dlr 0 --conductor twin --der WF_1,WF_2,WF_3 --days 365

# your own data, a named output
corridor-sim --data-dir /path/to/my_data --dlr 2 --start 2024-01-01 --days 365 --label my_case
```

Runs are slow: roughly 20 s per simulated day when nothing binds and 2–3 minutes
per day when the corridor curtails heavily, on one core.

## The whole matrix: `scripts/run_matrix.py`

Runs presets in parallel, then writes `matrix_summary.csv` and the comparison figures.

```bash
python scripts/run_matrix.py --days 30 --jobs 8
python scripts/run_matrix.py --only static_der4 dlr2_der4 --days 7
python scripts/run_matrix.py --collect-only      # rebuild table and figures from saved runs
```

| Option | Default | What it does |
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

cfg = build_config("dlr2_der4", days=365, data_dir="/path/to/my_data",
                   azimuth_z1_deg=45.0, export_cap_mw=200.0, label="my_case")
metrics, paths = run_scenario(cfg, make_plots=True, progress=True)
```

`build_config(preset, **fields)` applies the field defaults, then the preset, then
your overrides, and validates the result. Every CLI flag maps to a field
(`--dlr` → `dlr_mode`, `--der` → `der_enabled`, and so on). These fields are only
reachable from Python:

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
python -m pytest -q           # about four minutes
ruff check src tests scripts data
```
