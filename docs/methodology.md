# Methodology

## 1. Line rating

The corridor is rated with the IEEE 738-2012 steady-state heat balance. At
thermal equilibrium the ohmic heating plus absorbed sunlight equals what
convection and radiation carry away:

$$I^2 R(T_c) + q_s = q_c(T_c) + q_r(T_c)$$

Fixing $T_c$ at the design temperature and solving for $I$ gives the **ampacity**.
Fixing $I$ at the current the network is actually carrying and solving for $T_c$
gives the **conductor temperature** — the check that tells you whether a rating
was safe rather than merely permitted. Both directions are implemented
([`dlr.py`](../src/corridor_sim/dlr.py)); the inverse solve is a bisection on
$[T_{air}, 150\ ^\circ\mathrm{C}]$ to 0.1 °C.

Convection takes the larger of the two forced-convection correlations and the
natural-convection floor, so still air is handled without a discontinuity. The
wind angle of attack is folded onto [0°, 90°]: wind along the line cools far
less than wind across it, and on a real corridor that difference is worth more
than a few degrees of ambient.

### Three rating modes

| Mode | Air temperature | Irradiance | Wind speed | Wind angle |
|---|---|---|---|---|
| 0 static | reference | reference | reference | reference |
| 1 ambient-adjusted | measured | measured | fixed low value | perpendicular |
| 2 full weather | measured | measured | measured at conductor height | per zone |

Mode 0 is not a separate model. It is the *same* heat balance evaluated at the
conditions the static rating is declared for — a hot day, light perpendicular
wind, full sun. `dlr.calibration()` asserts this: the model reproduces the
declared static rating to within 0.5 %. That matters, because otherwise any
apparent DLR uplift could just be two models disagreeing.

Mode 1 is the conservative deployment: it needs only ambient temperature, and
holds wind at a low fixed value. Mode 2 needs a wind measurement or forecast and
is worth substantially more.

Resistance is AC. Skin effect at 50 Hz raises the effective value of a
conductor this size by about 2 %, and because ampacity goes as 1/√R, using the
DC value would overstate every rating in the study by roughly 1 %.

### What the conductor rating is not

The heat balance rates the conductor. It says nothing about anything else the
current has to pass through, and on a real corridor two limits sit above it.

**An administrative cap.** A dynamic rating is granted against protection
settings, sag and clearance margins and a permit that were all established for
the static rating. An operator does not follow the heat balance wherever the
weather takes it; the uplift is capped at a fixed multiple, typically 1.3 to
1.5. The default here is 1.5.

**The series equipment.** Current transformers, disconnectors, terminations and
jumper loops in the substation are not cooled by the wind. They carry nameplates
from the IEC 62271 standard rating series, so they step rather than tracking the
conductor, and a line is normally built with the next size up from its own
rating — 1250 A plant on a 780 A line here. This is the most common reason a
published DLR uplift is not realisable in practice, and it is the first thing a
network operator will ask about.

The operative rating is therefore the lowest of the three:

$$I_{op} = \min\bigl(I_{IEEE738}(\text{weather}),\ k_{cap} \cdot I_{static},\ I_{equip}\bigr)$$

Which one bound is written to every row (`rating_{zone}_binding`), and the
uncapped heat balance is written beside it (`rating_{zone}_weather_a`). Without
both, "the weather did not allow more" and "the weather allowed more and we were
not permitted to use it" collapse into the same number, and they call for
completely different responses — one for a bigger conductor, the other for a
protection review or a switchgear replacement.

Both ceilings are configurable and can be removed entirely
(`--dlr-cap-ratio`, `--no-rating-cap`, `--no-equipment-limit`), which is how the
study reports what the conductor alone would have supported. `calibration()`
deliberately ignores them: it checks the conductor model against the declared
static rating, and a ceiling above it would mask a model that had drifted.

### Wind at conductor height

The reference wind field sits at 100 m; the conductor sits at 15 m. Speed is
brought down by a neutral-stability log law over a roughness length, and the
geometry is validated — an effective height at or below the roughness length
raises rather than silently returning nonsense.

The corridor is split into two rating zones with different mean bearings, so the
same wind gives each a different angle of attack. Each zone is rated on its own
weather; the governing limit for the export path is the lower of the two,
because it is one series thermal path.

## 2. Control

Three actuators share the corridor, deliberately separated by role rather than
all reacting to the same voltage at the same speed:

| Actuator | Role | Deadband | Move budget |
|---|---|---|---|
| MV shunt reactor | fine, primary | ±0.18 kV | 4 steps/interval |
| On-load tap changer | coarse, backup | ±0.50 kV | 3 taps/interval |
| Plant reactive droop | continuous | ±0.010 pu | — |

Giving the reactor and the tap changer the same deadband on the same bus
produces a limit cycle: both see the same error, both act, and they fight. Here
the reactor acts first, the network is re-solved, and only then is the tap
changer tested against the voltage that remains.

Both switched actuators freeze after reversing direction within one interval. An
actuator that has reversed has bracketed its setpoint, and further movement is
hunting, not control. A move-rate budget additionally caps how many operations a
15-minute interval can physically contain, and it is shared: an actuator cannot
exceed its physical operation rate by being asked twice in one interval for two
different reasons.

That distinction carries into what is reported. The control loop may write an
actuator position several times while it searches, and freeze-on-reversal exists
precisely so that it can. Those writes are iterations of a solver, not
operations of a switch. `oltc_operations` counts the net change between the
position a step started from and the position it committed — the number a
maintenance schedule is written against — and `oltc_loop_moves` keeps the inner
count beside it as a diagnostic. On this corridor the two differ by more than an
order of magnitude, and reporting the inner count as duty makes a control scheme
that behaves well look unusable.

### Reactive exchange at the interface

A long inductive overhead corridor *absorbs* reactive power under load — tens of
megavars at high output. The guard that stages the shunt reactors down therefore
watches reactive **import**, and every megavar a reactor stops absorbing is a
megavar the corridor no longer has to draw from the grid.

The measurement at the interface is signed for export, so import is its
negative. A guard written directly against the exported sign watches a condition
this network never reaches: it never fires, the release path is never taken, and
every row reports a healthy flag while the exchange sits well outside its
window. `reactive_import_mvar()` names the quantity explicitly for that reason.
The window flag itself is on the magnitude of the exchange in either direction,
matching the contract it is written against.

### Reactive droop, and why the loop is built the way it is

The droop characteristic absorbs above the deadband and injects below it, with
the full reactive range spanning a 4 % voltage error and saturating at the
plant's capability.

Two properties of the iteration are load-bearing:

1. **Re-solve after every reactive update.** If the network is only re-solved
   when a tap moves, then once the taps settle the measured voltage is
   byte-identical to the previous iteration, the stability test passes on that
   identity, and the loop exits reporting a reactive power the network never
   saw. Every downstream consumer — recorded flows, the constraint scan, the
   curtailment search — then reads a state that was never solved.

2. **A settled iteration is not a tracked setpoint.** Under-relaxation can drive
   the increment below tolerance while a unit is still far from its reference.
   Convergence therefore requires *both* a settled voltage *and* a closed
   residual, and the residual is written to every row
   (`q_tracking_error_mvar`) so the claim is checkable rather than trusted.

Each unit carries its own under-relaxation factor that halves whenever its step
reverses. The droop is steep relative to this corridor's dV/dQ, so a fixed
damping factor sits near the stability boundary and the margin moves with
loading; halving on reversal squeezes an oscillating unit onto its fixed point.

At extreme loading the droop has no fixed point at all — the gain far exceeds
the network's sensitivity, which is a genuine property of a weak corridor, not a
numerical artefact. The loop therefore **backtracks**: a step the power flow
cannot follow is undone, the damping is reduced, and the iteration resumes from
the last solved state. A timestep always ends on a solved network, so
curtailment can still act on it, and `reg_converged` records honestly that the
droop did not settle.

### Solving

Newton-Raphson, warm-started from the previous interval. A cold start at high
output can diverge even though a solution exists, because the corridor's
reactive absorption is not yet being met locally; the entry solve therefore
seeds the droop units at their capability in the supporting direction, and falls
back to a continuation that ramps injection in from a level that does converge.
The final solve is always at full injection, so the physics is unchanged. The
deep path is deliberately disabled inside the control and curtailment loops,
where a cheap failure is informative.

## 3. Inputs and coverage

Inputs arrive at whatever resolution they come in and are interpolated onto the
15-minute grid. Interpolation fills *between* observations. Anything outside the
span of a file is a gap in the study's inputs and stops the run.

The order matters more than it looks. Filling first and testing for NaN
afterwards reads like a coverage check and is not one: a forward or backward
fill leaves no NaN behind, so the test always passes, and the run proceeds on
the last observed value held flat across the uncovered window. That failure is
worse than substituting zeros, because zeros are visible and a held edge value
plots as an entirely plausible calm spell. The window is therefore checked
against each file's own first and last timestamp before any filling happens.

A gap *inside* a file's span is interpolated rather than rejected, deliberately:
a missing hour in an hourly series is not distinguishable from a series that was
six-hourly to begin with, and rejecting it would rule out every legitimately
coarse input.

## 4. Constraint hierarchy

| Level | Constraint | Actionable |
|---|---|---|
| L1 | Over-voltage on any 110 kV bus | yes |
| L2 | Under-voltage on any 110 kV bus | **no** |
| L3 | Thermal, split by asset owner | yes |
| L4 | Export cap at the interface | yes |

L2 is recorded but never curtailed for: reducing active power *deepens* an
under-voltage across an inductive corridor, so curtailment is the wrong
instrument. The important consequence is that **all four levels are evaluated on
every scan**. Returning the first violated level would let a non-actionable
under-voltage hide a coincident thermal overload — the scan would report
"nothing to do" during exactly the hours that most need attention.

L3 is scanned in three ownership groups — corridor lines, distribution
transformers, plant assets — each with its own worst element and margin. Pooling
them into one number is how a plant-owned step-up transformer ends up setting a
published network hosting capacity while the corridor itself has headroom.

Every curtailed megawatt-hour is attributed to exactly one cause
(`curtail_cause`), so the energy adds up.

## 5. Curtailment

Curtailment is parametrised by one scalar: total megawatts removed, allocated
pro rata over the entry dispatch. Every actionable level is relieved
monotonically by injecting less, so the problem is one-dimensional and monotone.

The search brackets the feasibility boundary, then interpolates on a signed
normalised margin with regula falsi and an Illinois guard, and **applies the
smallest cut that clears** — not the last cut tried. The residual bracket width
is reported (`curtail_residual_mw`), so the remaining discretisation error is a
number in the output rather than an unknown.

Marching down in fixed steps and banking whatever was taken answers "the first
grid point past the boundary", which systematically overstates the curtailment a
constraint requires — and the overshoot is indistinguishable from a real
requirement once it is written to a results file.

Two properties the search depends on:

* **Trials evaluate what will be applied.** The droop is re-settled inside every
  trial. Freezing reactive power during the search and re-settling once
  afterwards means searching over one function and reporting another; the
  re-settle can push the result back over the limit the search just cleared.
* **Trials are path-independent.** Each restarts from the entry reactive power,
  so a trial is a pure function of the cut and the bracket stays meaningful.

A first guess is taken from the linear structure of the violation — corridor
current and interface export both scale with injection — which keeps the search
to a handful of trials rather than a doubling ladder from zero.

## 6. Storage

The battery is a market participant, not a corridor congestion device, and its
reach is asymmetric in both directions. It connects near the receiving end, so
it cannot relieve a thermal overload on the sections upstream of its tap — only
curtailment can. But it shares the final section into the interface with
generation, so discharging there competes with export for that segment; because
the battery is not curtailable, the plants are cut instead. Storage sited this
way can therefore *increase* curtailment rather than relieve it, and grid
charging additionally depresses the interface voltage. Both are consequences of
siting and charging strategy, and both are configurable
(`storage_connection`, `storage_charge_source`) rather than assumed.

Dispatch runs in two phases: an intent set before the network is solved, and a
reconciliation against the export headroom the solved network actually leaves.
Generation has priority, so a delivery that would breach the cap is clamped and
the shortfall is recorded rather than absorbed.

State of charge integrates on realised power only. On a step that did not
converge nothing is integrated — there is no energy bookkeeping for power that
was never delivered. When a limit clamps the result, realised power is backed
out of the actual state-of-charge change so power and energy stay consistent.

Holding a reserve and delivering energy compete for the same asset. Every
interval records the reserve still available and whether it fell short, so the
conflict is a counted result rather than a hidden assumption.

## 7. What is deliberately not modelled

* No contingency (N-1) analysis; the corridor is studied intact.
* No protection, stability or electromagnetic transient behaviour — the study is
  quasi-static, and 15-minute steps say nothing about dynamics.
* A single weather point represents the whole corridor, and each rating zone
  carries one mean bearing. Real span-by-span bearings spread around that mean,
  so a per-span rating would be lower than a zone-mean rating.
* Sag and clearance are not computed. The design conductor temperature stands in
  for the clearance limit that governs a real line.
* The series equipment is one lumped current rating, not a modelled set of
  assets. A real study would rate each item and would usually find one of them,
  rather than the conductor, setting the scheme.
* The administrative cap is a fixed multiple. Real schemes often use a
  time-varying or forecast-confidence-dependent limit, and some require the
  rating to be held for a minimum period before it can be used.
* No sub-hourly ramping constraints on the plants.
* Nothing here is a real-time system. A deployed DLR scheme needs sensor
  validation, a fallback rating when measurements are lost, and a latency budget
  between measurement and dispatch. This is a planning study and assumes the
  weather it is given.
