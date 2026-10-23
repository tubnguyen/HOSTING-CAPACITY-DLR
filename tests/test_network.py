"""Network construction."""
from __future__ import annotations

import pytest

from corridor_sim import constants as C
from corridor_sim import network as nw
from corridor_sim.config import build_config
from corridor_sim.controls import NetIndex

# The halves a segment is built as when a midpoint connection splits it.
HALVES = {f"{whole}_{k}" for whole in nw.SPLIT_SEGMENT.values() for k in (1, 2)}
WHOLE_LINES = [n for n in nw.CORRIDOR_LINES if n not in HALVES]


def test_builds_and_solves(solved):
    net, _ = solved
    assert net.converged
    assert net.res_bus["vm_pu"].notna().all()


def test_every_element_exists_regardless_of_scenario():
    """Topology invariance: disabling a plant must not change the network."""
    full = build_config("dlr2_der4_bess")
    minimal = build_config("static_der1")
    a, _ = nw.build(full)
    b, _ = nw.build(minimal)
    assert len(a.sgen) == len(b.sgen) == 4
    assert len(a.storage) == len(b.storage) == 1
    assert len(a.bus) == len(b.bus)


def test_corridor_zones_partition_the_corridor():
    listed = [n for names in nw.CORRIDOR_ZONES.values() for n in names]
    assert len(listed) == len(set(listed))
    assert set(nw.EXPORT_PATH_LINES) <= set(listed)


def test_transformer_rating_choice_preserves_impedance():
    """Changing the enforced rating must change loading, not the network."""
    onan, _ = nw.build(build_config(wf_trafo_rating="onan"))
    onaf, _ = nw.build(build_config(wf_trafo_rating="onaf"))
    row_a = onan.trafo[onan.trafo.name == "T_WF1_1"].iloc[0]
    row_b = onaf.trafo[onaf.trafo.name == "T_WF1_1"].iloc[0]
    z_a = row_a.vk_percent / 100 * C.V_HV_KV ** 2 / row_a.sn_mva
    z_b = row_b.vk_percent / 100 * C.V_HV_KV ** 2 / row_b.sn_mva
    assert z_a == pytest.approx(z_b)
    assert row_b.sn_mva > row_a.sn_mva


def test_single_transformer_unit_is_the_outage_case():
    net, _ = nw.build(build_config(wf_trafo_units=1))
    assert sum(net.trafo.name.str.startswith("T_WF")) == 3


def test_storage_connection_modes():
    tie, buses_tie = nw.build(build_config(storage_connection="tie"))
    direct, buses_direct = nw.build(build_config(storage_connection="direct"))
    assert "LAT_BESS" in set(tie.line.name)
    assert "LAT_BESS" not in set(direct.line.name)
    assert buses_direct["STORAGE_PCC"] == buses_direct["TAP_B"]


def test_index_finds_every_named_element(cfg):
    net, buses = nw.build(cfg)
    idx = NetIndex.build(net, buses)
    for name in WHOLE_LINES + nw.PLANT_LINES:
        assert name in idx.lines
    assert not HALVES & set(idx.lines), "no midpoint is used, so no segment is split"
    for name in nw.DSO_TRAFO_NAMES + nw.PLANT_TRAFOS:
        assert name in idx.trafos
    assert idx.storage >= 0


def test_distances_are_the_generic_round_values(cfg):
    """10 km between substations and taps, 20 km from every wind farm to its tap.

    The battery tap is the exception: it sits at the interface.
    """
    net, _ = nw.build(cfg)
    length = net.line.set_index("name")["length_km"]
    for name in WHOLE_LINES:
        expected = 1.0 if name == "CORR_B_PCC" else 10.0
        assert length[name] == pytest.approx(expected)
    for name in ("LAT_WF1_TAP", "LAT_WF2_TAP", "LAT_WF3_A"):
        assert length[name] == pytest.approx(20.0)
    assert C.CORRIDOR_LENGTH_KM == pytest.approx(41.0)


def test_every_wind_farm_has_its_own_lateral(cfg):
    net, buses = nw.build(cfg)
    ends = {row["name"]: {row["from_bus"], row["to_bus"]} for _, row in net.line.iterrows()}
    assert ends["LAT_WF1_TAP"] == {buses["WF_1_HV"], buses["TAP_W"]}
    assert ends["LAT_WF2_TAP"] == {buses["WF_2_HV"], buses["TAP_W"]}
    assert ends["LAT_WF3_A"] == {buses["WF_3_HV"], buses["SUB_A"]}


def test_a_midpoint_splits_its_segment_only_when_used():
    plain, plain_buses = nw.build(build_config())
    assert not set(C.MIDPOINT_BUSES) & set(plain_buses)
    cfg = build_config(added_der=["wind:40@MID_A_C"], added_loads=["30@MID_C_PCC"])
    net, buses = nw.build(cfg)
    length = net.line.set_index("name")["length_km"]
    assert "CORR_PV_W" not in length and "CORR_C_B" not in length
    assert length["CORR_PV_W_1"] + length["CORR_PV_W_2"] == pytest.approx(10.0)
    assert length["CORR_C_B_1"] + length["CORR_C_B_2"] == pytest.approx(10.0)
    # Each midpoint sits halfway along its rating zone.
    assert length["CORR_A_PV"] + length["CORR_PV_W_1"] == pytest.approx(15.0)
    assert length["CORR_C_B_1"] == pytest.approx(5.5)
    ends = {row["name"]: (row["from_bus"], row["to_bus"]) for _, row in net.line.iterrows()}
    assert ends["CORR_PV_W_1"] == (buses["TAP_PV"], buses["MID_A_C"])
    assert ends["CORR_C_B_2"] == (buses["MID_C_PCC"], buses["TAP_B"])
    assert sum(net.line.length_km[net.line.name.str.startswith("CORR")]) == pytest.approx(
        C.CORRIDOR_LENGTH_KM)
    idx = NetIndex.build(net, buses)
    assert idx.hv_buses[-2:] == ["MID_A_C", "MID_C_PCC"]


def test_an_added_plant_joins_through_its_own_step_up_transformer():
    cfg = build_config(added_der=["wind:50@SUB_C", "solar:20@PCC"])
    net, buses = nw.build(cfg)
    idx = NetIndex.build(net, buses)
    assert idx.added_trafos == ["T_WF_4", "T_PV_2"]
    trafo = net.trafo.set_index("name").loc["T_WF_4"]
    assert trafo.hv_bus == buses["SUB_C"] and trafo.lv_bus == buses["WF_4_MV"]
    assert trafo.sn_mva == pytest.approx(C.ADDED_TRAFO_SN_RATIO * 50.0)
    sgen = net.sgen.set_index("name").loc["PV_2"]
    assert sgen.bus == buses["PV_2_MV"] and sgen.sn_mva == pytest.approx(20.0 / C.COS_PHI)
    assert len(net.sgen) == 6


def test_an_added_load_draws_its_demand_from_the_110_kv_busbar():
    cfg = build_config(added_loads=["80@SUB_C", "20@PCC:0.95"])
    net, buses = nw.build(cfg)
    loads = net.load.set_index("name")
    assert loads.loc["LOAD_1", "bus"] == buses["SUB_C"]
    assert loads.loc["LOAD_1", "p_mw"] == 80.0 and loads.loc["LOAD_1", "q_mvar"] == 0.0
    assert loads.loc["LOAD_2", "q_mvar"] == pytest.approx(20.0 * C.TAN_PHI)
    assert "added load 100 MW" in nw.summary(cfg, net)
