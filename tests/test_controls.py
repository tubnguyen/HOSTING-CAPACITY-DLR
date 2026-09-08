"""Voltage and reactive power control."""
from __future__ import annotations

import pytest

from corridor_sim import constants as C
from corridor_sim.constraints import measure_pcc_export
from corridor_sim.controls import (
    MoveBudget,
    droop_reference,
    measurement_bus,
    reactive_import_mvar,
    reactor_step,
    regulate,
    release_reactors,
    solve,
)

Q_LIMIT = 30.0
P_RATED = 90.0


def test_droop_absorbs_on_high_voltage_and_injects_on_low():
    high = droop_reference(1.03, P_RATED, Q_LIMIT, P_RATED)
    low = droop_reference(0.97, P_RATED, Q_LIMIT, P_RATED)
    assert high < 0, "over-voltage must absorb reactive power"
    assert low > 0, "under-voltage must inject reactive power"
    assert high == pytest.approx(-low)


def test_droop_deadband_is_quiet():
    for v in (0.995, 1.000, 1.005, 1.010):
        assert droop_reference(v, P_RATED, Q_LIMIT, P_RATED) == pytest.approx(0.0, abs=1e-9)


def test_droop_saturates_at_the_capability_limit():
    assert droop_reference(1.40, P_RATED, Q_LIMIT, P_RATED) == pytest.approx(-Q_LIMIT)
    assert droop_reference(0.60, P_RATED, Q_LIMIT, P_RATED) == pytest.approx(Q_LIMIT)


def test_droop_slope_matches_the_specification():
    """One slope-width of error beyond the deadband uses the full range."""
    v = C.V_REF_PU + C.DROOP_DEADBAND_PU + C.DROOP_SLOPE_PCT / 100.0
    assert droop_reference(v, P_RATED, Q_LIMIT, P_RATED) == pytest.approx(-Q_LIMIT)


def test_droop_is_gated_at_low_output():
    assert droop_reference(1.03, 0.01 * P_RATED, Q_LIMIT, P_RATED, 0.05) == 0.0
    assert droop_reference(1.03, 0.50 * P_RATED, Q_LIMIT, P_RATED, 0.05) != 0.0


def test_move_budget_limits_operations():
    budget = MoveBudget(2)
    assert budget.allowed("a")
    budget.record("a", 1)
    budget.record("a", 1)
    assert not budget.allowed("a"), "budget must cap operations per step"


def test_move_budget_freezes_on_reversal():
    """An actuator that reverses within a step has bracketed its setpoint."""
    budget = MoveBudget(10)
    budget.record("a", 1)
    budget.record("a", -1)
    assert not budget.allowed("a")
    assert budget.n_frozen() == 1


def test_measurement_bus_policies(cfg):
    from dataclasses import replace
    assert measurement_bus(cfg, "WF_3") == "SUB_A"
    assert measurement_bus(cfg, "PV_1") == "TAP_PV"
    pilot = replace(cfg, droop_measurement="pilot_tap_w")
    assert all(measurement_bus(pilot, u) == "TAP_W"
               for u in ("WF_1", "WF_2", "WF_3", "PV_1"))


def test_regulation_delivers_the_reactive_power_it_reports(solved, cfg, reactor_state):
    """The recorded reactive power must be a solved value, not a bare command."""
    net, idx = solved
    dispatch = {u: float(net.sgen.at[idx.sgens[u], "p_mw"])
                for u in ("WF_1", "WF_2", "WF_3", "PV_1")}
    result = regulate(net, cfg, idx, dispatch, reactor_state, warm_start=False)
    assert result["ok"]
    assert result["q_tracking_error_mvar"] <= C.DROOP_Q_ERR_TOL_MVAR + 1e-6
    for unit, q_ref in result["q_ref"].items():
        if unit == C.BESS_NAME:
            continue
        delivered = float(net.res_sgen.at[idx.sgens[unit], "q_mvar"])
        assert delivered == pytest.approx(q_ref, abs=C.DROOP_Q_ERR_TOL_MVAR + 1e-6)


def test_regulation_respects_the_move_budgets(solved, cfg, reactor_state):
    net, idx = solved
    dispatch = {u: float(net.sgen.at[idx.sgens[u], "p_mw"])
                for u in ("WF_1", "WF_2", "WF_3", "PV_1")}
    result = regulate(net, cfg, idx, dispatch, reactor_state, warm_start=False)
    assert result["oltc_moves_a"] <= C.OLTC_MOVE_BUDGET * len(idx.oltc_a)
    assert result["reactor_moves"] <= C.REACTOR_MOVE_BUDGET * len(idx.reactors)


def test_regulation_always_leaves_a_solved_network(solved, cfg, reactor_state):
    """Even a stressed step must end solved, so curtailment can act on it."""
    net, idx = solved
    for unit in ("WF_1", "WF_2", "WF_3", "PV_1"):
        net.sgen.at[idx.sgens[unit], "p_mw"] = C.DER_RATING_MW[unit]
        net.sgen.at[idx.sgens[unit], "q_mvar"] = 0.0
    dispatch = dict(C.DER_RATING_MW)
    result = regulate(net, cfg, idx, dispatch, reactor_state, warm_start=False)
    assert result["ok"]
    assert net.res_bus["vm_pu"].notna().all()


def test_solve_reports_failure_without_the_deep_path(solved):
    net, _ = solved
    assert solve(net, "results")


# ── Reactive exchange at the interface ───────────────────────────────────────
def test_import_is_the_negative_of_export(solved):
    """The guard watches import; the measurement is signed for export.

    Getting this backwards is invisible: the guard simply never fires, every
    row reports a healthy flag, and the reactive exchange it was written to
    catch goes unremarked.
    """
    net, idx = solved
    exported = measure_pcc_export(net, idx.buses)["q_mvar"]
    assert reactive_import_mvar(net, idx.buses) == pytest.approx(-exported)


def test_a_loaded_corridor_imports_reactive_power(solved):
    """A long inductive overhead line absorbs megavars under load.

    This is the sign the guard actually has to handle - not a hypothetical.
    """
    net, idx = solved
    for unit in ("WF_1", "WF_2", "WF_3", "PV_1"):
        net.sgen.at[idx.sgens[unit], "p_mw"] = 0.75 * C.DER_RATING_MW[unit]
        net.sgen.at[idx.sgens[unit], "q_mvar"] = 0.0
    assert solve(net, "dc", deep=True)
    assert reactive_import_mvar(net, idx.buses) > 0.0


def test_the_guard_fires_on_the_sign_that_actually_occurs(solved, cfg, reactor_state):
    """Heavy import must reach the release path and step the reactors down."""
    net, idx = solved
    for unit in ("WF_1", "WF_2", "WF_3", "PV_1"):
        net.sgen.at[idx.sgens[unit], "p_mw"] = 0.75 * C.DER_RATING_MW[unit]
        net.sgen.at[idx.sgens[unit], "q_mvar"] = 0.0
    assert solve(net, "dc", deep=True)
    assert reactive_import_mvar(net, idx.buses) > C.Q_GUARD_MVAR, "expected heavy import"

    dispatch = {u: float(net.sgen.at[idx.sgens[u], "p_mw"])
                for u in ("WF_1", "WF_2", "WF_3", "PV_1")}
    result = regulate(net, cfg, idx, dispatch, reactor_state, warm_start=False)
    assert result["ok"]
    assert result["reactor_flag"] == "released"


def test_releasing_reactors_reduces_absorption(solved):
    """Every megavar a reactor stops absorbing is one less to import."""
    net, idx = solved
    state = dict.fromkeys(idx.reactors, C.REACTOR_STEP_INIT)
    for name in idx.reactors:
        net.shunt.at[idx.shunts[name], "q_mvar"] = C.REACTOR_STEPS_MVAR[state[name]]
    assert solve(net, "results")
    before = sum(float(net.shunt.at[idx.shunts[n], "q_mvar"]) for n in idx.reactors)

    new_state, released = release_reactors(net, idx, state, MoveBudget(C.REACTOR_MOVE_BUDGET))
    after = sum(float(net.shunt.at[idx.shunts[n], "q_mvar"]) for n in idx.reactors)
    if released:
        assert after < before
        assert all(new_state[n] <= state[n] for n in idx.reactors)


def test_release_respects_the_shared_move_budget(solved):
    """An actuator cannot exceed its operation rate by being asked twice."""
    net, idx = solved
    state = dict.fromkeys(idx.reactors, C.REACTOR_STEP_INIT)
    spent = MoveBudget(C.REACTOR_MOVE_BUDGET)
    for name in idx.reactors:                       # budget already exhausted
        for _ in range(C.REACTOR_MOVE_BUDGET):
            spent.record(name, 1)
    new_state, released = release_reactors(net, idx, state, spent)
    assert not released and new_state == state


# ── Actuator duty ────────────────────────────────────────────────────────────
def test_committed_operations_never_exceed_control_loop_writes(solved, cfg, reactor_state):
    """A tap stepped and stepped back within an interval is not an operation.

    The loop may write a position several times while it searches; only the
    net change between the state it started from and the state it committed is
    a switch operation, and that is what a maintenance schedule counts.
    """
    net, idx = solved
    dispatch = {u: float(net.sgen.at[idx.sgens[u], "p_mw"])
                for u in ("WF_1", "WF_2", "WF_3", "PV_1")}
    result = regulate(net, cfg, idx, dispatch, reactor_state, warm_start=False)
    assert result["oltc_net_moves"] <= result["oltc_moves_a"] + result["oltc_moves_b"]
    assert result["reactor_net_moves"] <= result["reactor_moves"]
    assert result["oltc_net_moves"] >= 0 and result["reactor_net_moves"] >= 0


def test_the_guard_holds_the_reactors_down_instead_of_fighting_the_voltage_loop(solved):
    """Two loops, one actuator, opposite directions.

    The voltage loop raises absorption to trim the MV busbar; the guard drops it
    to cut import at the interface. The reactor bank is a few megavars against
    an import of tens, so the guard's threshold is not reachable by reactor
    action and it fires every interval. Without a hold-down the reactor walks up
    and down forever and reports an operation count no stepped reactor could
    survive.
    """
    net, idx = solved
    state = dict.fromkeys(idx.reactors, 2)
    for name in idx.reactors:                      # MV voltage high enough to want a raise
        net.shunt.at[idx.shunts[name], "q_mvar"] = C.REACTOR_STEPS_MVAR[state[name]]
    assert solve(net, "results")

    raised, _ = reactor_step(net, idx, state, MoveBudget(C.REACTOR_MOVE_BUDGET),
                             allow_raise=True)
    held, after = reactor_step(net, idx, state, MoveBudget(C.REACTOR_MOVE_BUDGET),
                               allow_raise=False)
    assert all(after[n] <= state[n] for n in idx.reactors), "hold-down must never raise"
    if raised:
        assert not held or any(after[n] < state[n] for n in idx.reactors)


def test_hold_down_still_permits_a_release(solved):
    """Falling is the whole point; only rising is blocked."""
    net, idx = solved
    for unit in ("WF_1", "WF_2", "WF_3", "PV_1"):
        net.sgen.at[idx.sgens[unit], "p_mw"] = 0.75 * C.DER_RATING_MW[unit]
        net.sgen.at[idx.sgens[unit], "q_mvar"] = 0.0
    assert solve(net, "dc", deep=True)
    state = dict.fromkeys(idx.reactors, len(C.REACTOR_STEPS_MVAR) - 1)
    for name in idx.reactors:
        net.shunt.at[idx.shunts[name], "q_mvar"] = C.REACTOR_STEPS_MVAR[state[name]]
    assert solve(net, "results")
    _, after = reactor_step(net, idx, state, MoveBudget(C.REACTOR_MOVE_BUDGET),
                            allow_raise=False)
    assert all(after[n] <= state[n] for n in idx.reactors)
