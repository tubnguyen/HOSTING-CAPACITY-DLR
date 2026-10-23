"""Curtailment search."""
from __future__ import annotations

import pytest

from corridor_sim.constraints import scan
from corridor_sim.controls import solve
from corridor_sim.curtailment import CURTAILABLE, RESOLUTION_MW, curtail, uncurtailed_record


def _entry_dispatch(net, idx):
    return {u: float(net.sgen.at[idx.sgens[u], "p_mw"]) for u in CURTAILABLE}


def test_no_curtailment_when_nothing_binds(solved, cfg):
    net, idx = solved
    before = _entry_dispatch(net, idx)
    record = curtail(net, cfg, idx, {"Z1": 5000.0, "Z2": 5000.0})
    assert record["curtailed_total_mw"] == 0.0
    assert record["curtail_exit"] == "not_needed"
    assert record["feasible"]
    assert _entry_dispatch(net, idx) == pytest.approx(before)


def test_curtailment_clears_a_thermal_violation(solved, cfg):
    net, idx = solved
    limits = {"Z1": 300.0, "Z2": 300.0}
    assert scan(net, idx, limits, cfg)[0] == 3
    record = curtail(net, cfg, idx, limits)
    assert record["curtailed_total_mw"] > 0
    assert record["feasible"], record["binding_after"]
    assert record["binding_after"] == "none"
    assert scan(net, idx, limits, cfg)[0] == 0


def test_applied_cut_is_close_to_the_minimum_that_clears(solved, cfg):
    """Anything meaningfully smaller than the applied cut must still violate."""
    net, idx = solved
    limits = {"Z1": 300.0, "Z2": 300.0}
    entry = _entry_dispatch(net, idx)
    record = curtail(net, cfg, idx, limits)
    applied = record["curtailed_total_mw"]
    assert record["feasible"]
    assert record["curtail_residual_mw"] <= RESOLUTION_MW + 1e-9

    total = sum(entry.values())
    smaller = applied - 2.0 * RESOLUTION_MW
    if smaller > 0:
        fraction = smaller / total
        for unit in CURTAILABLE:
            net.sgen.at[idx.sgens[unit], "p_mw"] = entry[unit] * (1.0 - fraction)
        assert solve(net, "results", deep=True)
        assert scan(net, idx, limits, cfg)[0] != 0, "a smaller cut should not have cleared"


def test_cut_is_shared_pro_rata(solved, cfg):
    net, idx = solved
    entry = _entry_dispatch(net, idx)
    record = curtail(net, cfg, idx, {"Z1": 300.0, "Z2": 300.0})
    total = sum(entry.values())
    fraction = record["curtailed_total_mw"] / total
    for unit in CURTAILABLE:
        assert record[f"curtailed_{unit}_mw"] == pytest.approx(entry[unit] * fraction, rel=1e-6)


def test_more_severe_limit_needs_more_curtailment(solved, cfg):
    net, idx = solved
    entry = _entry_dispatch(net, idx)
    mild = curtail(net, cfg, idx, {"Z1": 400.0, "Z2": 400.0})["curtailed_total_mw"]
    for unit, value in entry.items():
        net.sgen.at[idx.sgens[unit], "p_mw"] = value
        net.sgen.at[idx.sgens[unit], "q_mvar"] = 0.0
    assert solve(net, "dc", deep=True)
    severe = curtail(net, cfg, idx, {"Z1": 250.0, "Z2": 250.0})["curtailed_total_mw"]
    assert severe > mild


def test_export_cap_curtailment(solved, cfg):
    from dataclasses import replace
    net, idx = solved
    strict = replace(cfg, export_cap_mw=80.0)
    record = curtail(net, cfg=strict, idx=idx, limits_a={"Z1": 5000.0, "Z2": 5000.0})
    assert record["curtail_cause"] == "export_cap"
    assert record["curtailed_total_mw"] > 0
    assert record["feasible"]


def test_disabled_curtailment_still_records_what_binds(solved, cfg):
    net, idx = solved
    record = uncurtailed_record(net, cfg, idx, {"Z1": 300.0, "Z2": 300.0})
    assert record["curtail_exit"] == "disabled"
    assert record["binding_before"] != "none"
    assert record["binding_before"] == record["binding_after"]
    assert record["curtailed_total_mw"] == 0.0
    assert not record["feasible"]


def test_owner_groups_are_reported_after_remediation(solved, cfg):
    net, idx = solved
    record = curtail(net, cfg, idx, {"Z1": 300.0, "Z2": 300.0})
    for key in ("worst_corridor_pct", "worst_dso_trafo_pct", "worst_plant_pct"):
        assert record[key] >= 0.0
    assert record["worst_corridor_elem"] != "none"


def _with_added(added_der=(), added_loads=(), export_cap_mw=250.0):
    """The solved fixture network with plants added and every plant producing."""
    from corridor_sim import network as nw
    from corridor_sim.config import build_config
    from corridor_sim.controls import NetIndex
    cfg = build_config("dlr2_der4", days=1, added_der=list(added_der),
                       added_loads=list(added_loads), export_cap_mw=export_cap_mw)
    net, buses = nw.build(cfg)
    idx = NetIndex.build(net, buses)
    net.load.at[idx.loads["LOAD_SUB_A"], "p_mw"] = 15.0
    net.load.at[idx.loads["LOAD_AGG"], "p_mw"] = 25.0
    for unit, p in (("WF_1", 55.0), ("WF_2", 66.0), ("WF_3", 44.0), ("PV_1", 30.0)):
        net.sgen.at[idx.sgens[unit], "p_mw"] = p
    for plant in cfg.added_der:
        net.sgen.at[idx.sgens[plant.name], "p_mw"] = 0.8 * plant.mw
    assert solve(net, "dc", deep=True)
    return cfg, net, idx


def test_a_plant_downstream_of_the_overload_is_not_cut_for_it():
    cfg, net, idx = _with_added(added_der=["wind:60@PCC", "wind:60@SUB_C"])
    limits = {"Z1": 400.0, "Z2": 2000.0}             # only zone Z1 is tight
    record = curtail(net, cfg, idx, limits)
    assert record["feasible"] and record["curtail_cause"] == "corridor"
    assert record["curtailed_WF_4_mw"] == 0.0, "the PCC plant puts no current through Z1"
    assert record["curtailed_WF_5_mw"] == 0.0, "nor does a plant joining at SUB_C"
    assert record["curtailed_WF_1_mw"] > 0.0 and record["curtailed_WF_3_mw"] > 0.0
    assert net.sgen.at[idx.sgens["WF_4"], "p_mw"] == pytest.approx(48.0)


def _shares(record, net, idx, units):
    """Share of its entry output each unit gave up."""
    return {u: record[f"curtailed_{u}_mw"]
            / (record[f"curtailed_{u}_mw"] + float(net.sgen.at[idx.sgens[u], "p_mw"]))
            for u in units}


def test_every_plant_cut_gives_up_the_same_share_of_its_output():
    """Curtailment is proportional to each plant's instantaneous output."""
    cfg, net, idx = _with_added(added_der=["wind:60@SUB_C"])
    record = curtail(net, cfg, idx, {"Z1": 300.0, "Z2": 300.0})
    assert record["feasible"] and record["curtail_fraction"] > 0.0
    shares = _shares(record, net, idx, cfg.units)
    assert list(shares.values()) == pytest.approx([record["curtail_fraction"]] * len(shares))
    assert record["curtailed_WF_2_mw"] == pytest.approx(66.0 * record["curtail_fraction"])


def test_when_a_shared_limit_binds_too_every_plant_gives_up_the_same_share():
    """Corridor and export cap together: the PCC plant is behind the cap."""
    cfg, net, idx = _with_added(added_der=["wind:60@PCC"], export_cap_mw=60.0)
    limits = {"Z1": 400.0, "Z2": 400.0}
    level, _, _, detail = scan(net, idx, limits, cfg)
    assert detail["levels"][3] is not None and detail["levels"][4] is not None
    record = curtail(net, cfg, idx, limits)
    assert record["feasible"], record["binding_after"]
    shares = _shares(record, net, idx, cfg.units)
    assert list(shares.values()) == pytest.approx([record["curtail_fraction"]] * len(shares))
    total = sum(record[f"curtailed_{u}_mw"] for u in cfg.units)
    assert record["curtailed_total_mw"] == pytest.approx(total)


def test_a_large_load_takes_generation_before_anything_is_cut():
    """Power a load draws between the plants and the bottleneck never reaches it."""
    limits = {"Z1": 500.0, "Z2": 500.0}
    cut = {}
    for name, loads in (("without", []), ("with", ["60@MID_A_C"])):
        cfg, net, idx = _with_added(added_loads=loads)
        cut[name] = curtail(net, cfg, idx, limits)["curtailed_total_mw"]
        assert scan(net, idx, limits, cfg)[0] == 0
    assert cut["without"] > 0.0
    # The load sits upstream of TAP_W -> SUB_C, the segment carrying every
    # plant, so it takes about its own demand off what has to be cut.
    assert cut["without"] - cut["with"] == pytest.approx(60.0, abs=8.0)


def test_a_load_that_absorbs_the_excess_leaves_nothing_to_cut():
    cfg, net, idx = _with_added(added_loads=["120@MID_A_C"])
    record = curtail(net, cfg, idx, {"Z1": 500.0, "Z2": 500.0})
    assert record["curtailed_total_mw"] == 0.0 and record["curtail_exit"] == "not_needed"


def test_an_overload_drawn_by_load_is_not_curtailed():
    cfg, net, idx = _with_added(added_loads=["150@SUB_A"])
    for unit in cfg.units:
        net.sgen.at[idx.sgens[unit], "p_mw"] = 10.0
    assert solve(net, "dc", deep=True)
    before = _entry_dispatch(net, idx)
    record = curtail(net, cfg, idx, {"Z1": 300.0, "Z2": 300.0})
    assert record["curtailed_total_mw"] == 0.0
    assert record["curtail_exit"] == "load_driven"
    assert record["residual_class"] == "drawn_by_load"
    assert _entry_dispatch(net, idx) == pytest.approx(before)
