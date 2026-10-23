# Methodology in brief

Quasi-static AC power flow (pandapower, Newton-Raphson) at 15-minute steps. Each step:

```
loads → plant output → battery intent → voltage control → line rating
      → battery reconciliation → curtailment → state of charge → record
```

## 1. Line rating

IEEE 738-2012 steady-state heat balance per conductor:

$$I^2 R(T_c) + q_{solar} = q_{convection} + q_{radiation}$$

* **Forward:** fix $T_c$ at 80 °C, solve for $I$ → the ampacity.
* **Inverse:** fix $I$ at the carried current, solve for $T_c$ → the conductor temperature.

| Mode | Air temp | Irradiance | Wind |
|---|---|---|---|
| 0 static | reference 32 °C | reference 1000 W/m² | 0.6 m/s, perpendicular |
| 1 ambient-adjusted | measured | measured | 0.6 m/s, perpendicular |
| 2 full weather | measured | measured | measured, angle per zone |

Mode 0 is the same model at reference conditions and reproduces the declared
780 A within 0.5 %, so the three modes are directly comparable.

**Operative rating** = min(heat balance, cap × static, series equipment).
Defaults: cap 2 × static (1560 A, the twin-bundle static rating), equipment 1600 A.
Which one bound is recorded every step, beside the uncapped heat balance.

**Wind:** the 100 m wind is brought down to the 15 m conductor height with a log
law, and 40 % of it is counted as cooling (`--wind-factor`): the rating has to hold
on the least-cooled span, sheltered and with the wind partly along the line. The
corridor has two zones, both at a 45° bearing by default; the lower zone rating
governs.

## 2. Voltage control

| Actuator | Role | Deadband | Max moves per step |
|---|---|---|---|
| MV shunt reactor | fine, acts first | ±0.18 kV | 4 |
| On-load tap changer | coarse backup | ±0.50 kV | 3 |
| Plant Q(V) droop | continuous | ±0.01 pu | — |

* Reactor first, re-solve, then the tap changer.
* Droop re-solves after every update; converges only when voltage has settled
  and the reactive error is closed.
* If reactive import at the PCC exceeds 20 MVAr, the reactors are stepped down.
* Tap operations are counted as net position change per step, not loop iterations.

## 3. Constraints

| Check | Curtailed for? |
|---|---|
| Over-voltage on any 110 kV bus | yes |
| Under-voltage on any 110 kV bus | no (cutting power makes it worse) |
| Thermal: corridor, DSO transformers, plant assets | yes |
| Export cap at the PCC | yes |

All four are checked every step.

## 4. Curtailment

* Proportional to instantaneous output: every plant cut in a step gives up the same
  share of what it is producing at that moment, recorded as `curtail_fraction`. A
  plant producing twice as much is cut twice as many megawatts.
* The cut is the smallest that clears every actionable violation, searched on the
  solved network, so loads are already accounted for: what a large load draws
  between the plants and the bottleneck never reaches it, and only the excess the
  network cannot carry and the load cannot use is curtailed.
* Over-voltage, the export cap and transformer overloads are shared by every plant,
  and so is any step in which one of them binds.
* A corridor overload alone is shared by the plants whose power flows through the
  overloaded segment: those joining at or upstream of its head-side end. A plant
  connected further along, at the PCC for one, is not cut for it.
* A segment overloaded by power drawn back toward the head, for a large load up
  the corridor, is recorded but never curtailed for: cutting generation deepens it.
* Every curtailed MWh is assigned to one cause.

## 5. Battery

* Two-phase dispatch: intent before the solve, then clamped to the export headroom.
* Discharges on the reserve signal; charges from surplus, then the grid.
* State of charge integrates realised power only; reserve shortfall is counted.
* Its tap is near the PCC, so it cannot relieve overloads upstream of it.

## 6. Inputs

Inputs of any resolution are interpolated onto the 15-minute grid.

Added plants have no input file of their own. An added wind farm is modelled from
`weather.csv` with the same turbine, hub height and losses as the built-in farms;
an added solar plant follows `pv_generation.csv`, scaled from PV_1's 60 MW to its
own rating. An added load is a constant demand. Each joins at one of five
connection points; see [network.md](network.md#connection-points).

## 7. Reported currents and cost

* **Current from all plants:** the highest corridor segment current with every
  connected plant at full available output, read before curtailment. **Current
  after curtailment:** the same once curtailment has brought it onto the operative
  rating. The difference between them is the curtailment, so the exceedance below
  is enough to size it.
* **Time above a limit:** how long the current from all plants exceeds 780 A (a
  statically rated single conductor would be overloaded), the operative rating (this
  run curtails for the corridor) or 1560 A (even the twin bundle would be). Each is
  given as a share of the window and in hours.
* **Thermal loading:** the same current as a percentage of a rating. Against the
  operative rating it says what has to be curtailed; against the 780 A static rating
  it says how far past the as-built conductor the generation reaches.
* **Technical cost:** curtailed energy, network losses (all lines and
  transformers) and reactive energy exchanged at the PCC outside the ±33 MVAr
  window (10 % of the installed fleet, so wider with added plants, at most 50 MVAr). Priced at an energy price (curtailment, losses) and a reactive energy
  price; both are placeholders. Reported in the summary tables and the metrics,
  not as a figure.
* **Static comparison (`--compare`):** the same run at the static rating, reused if
  one with the same fingerprint (settings, window, input data, model code) is in the
  output folder; headline and cost are then given for both, with the change in its
  own unit and in %. The figures describe one run each.

Column names are decoded in [outputs.md](outputs.md).

## 8. Not modelled

* N-1 contingencies, protection, stability, dynamics.
* Sag and clearance (the 80 °C design temperature stands in).
* Per-span ratings: one weather point and one bearing per zone.
* Series equipment as one lumped rating; the cap as a fixed multiple.
* Ramp limits, sensor validation, fallback ratings, real-time latency.
