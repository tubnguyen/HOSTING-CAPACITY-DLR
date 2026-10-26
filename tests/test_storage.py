"""Storage dispatch, energy accounting and reserve."""
from __future__ import annotations

import pytest

from corridor_sim import constants as C
from corridor_sim import storage
from corridor_sim.config import build_config


@pytest.fixture
def cfg_storage():
    return build_config("dlr2_der4_bess", days=1)


def test_sign_convention_is_symmetric():
    assert storage.to_grid_frame(10.0) == -10.0      # charging draws from the grid
    assert storage.to_grid_frame(-10.0) == 10.0      # discharging feeds it


def test_round_trip_loses_exactly_the_stated_efficiency(cfg_storage):
    cfg = cfg_storage
    start = 0.5 * cfg.storage_e_mwh
    charged, _, _ = storage.update_soc(cfg, start, 10.0, apply=True)
    energy_in = 10.0 * C.DT_H
    energy_stored = charged - start
    back, _, _ = storage.update_soc(cfg, charged, -(energy_stored / C.DT_H) * C.BESS_ETA_DIS,
                                    apply=True)
    energy_out = (charged - back) / C.BESS_ETA_DIS * C.BESS_ETA_DIS
    delivered = (charged - back) * C.BESS_ETA_DIS
    assert energy_stored == pytest.approx(energy_in * C.BESS_ETA_CH)
    assert delivered / energy_in == pytest.approx(C.BESS_ETA_RT, rel=1e-6)
    assert energy_out > 0


def test_state_of_charge_is_frozen_on_a_failed_step(cfg_storage):
    cfg = cfg_storage
    start = 0.5 * cfg.storage_e_mwh
    after, clamped, realised = storage.update_soc(cfg, start, 25.0, apply=False)
    assert after == start and clamped == 0 and realised == 25.0


def test_clamping_keeps_power_and_energy_consistent(cfg_storage):
    """When a limit binds, realised power must match the actual energy change."""
    cfg = cfg_storage
    near_full = cfg.soc_max_mwh - 0.5
    after, clamped, realised = storage.update_soc(cfg, near_full, cfg.storage_p_mw, apply=True)
    assert clamped == 1
    assert after == pytest.approx(cfg.soc_max_mwh)
    assert realised * C.BESS_ETA_CH * C.DT_H == pytest.approx(after - near_full)
    assert realised < cfg.storage_p_mw

    near_empty = cfg.soc_min_mwh + 0.5
    after, clamped, realised = storage.update_soc(cfg, near_empty, -cfg.storage_p_mw, apply=True)
    assert clamped == 1
    assert after == pytest.approx(cfg.soc_min_mwh)
    assert realised / C.BESS_ETA_DIS * C.DT_H == pytest.approx(after - near_empty)


def test_power_limits_respect_the_energy_left(cfg_storage):
    cfg = cfg_storage
    assert storage.dischargeable_mw(cfg, cfg.soc_min_mwh) == 0.0
    assert storage.chargeable_mw(cfg, cfg.soc_max_mwh) == 0.0
    assert storage.dischargeable_mw(cfg, cfg.soc_max_mwh) == pytest.approx(cfg.storage_p_mw)
    assert storage.chargeable_mw(cfg, cfg.soc_min_mwh) == pytest.approx(cfg.storage_p_mw)


def test_reserve_shortfall_is_reported(cfg_storage):
    cfg = cfg_storage
    full, short = storage.reserve_availability(cfg, cfg.soc_max_mwh, 0.0)
    assert full == pytest.approx(cfg.storage_reserve_mw) and short == 0

    # Delivering energy consumes the headroom the reserve was booked against.
    partial, short = storage.reserve_availability(cfg, cfg.soc_max_mwh, -cfg.storage_p_mw)
    assert partial == 0.0 and short == 1

    empty, short = storage.reserve_availability(cfg, cfg.soc_min_mwh, 0.0)
    assert empty == 0.0 and short == 1


def test_disabled_storage_holds_no_reserve():
    cfg = build_config("dlr2_der4")
    assert storage.reserve_availability(cfg, 30.0, 0.0) == (0.0, 0)


# ── Where the battery connects, and what it can relieve there ───────────────
def _battery_at(point, discharge_mw=0.0, soc_frac=0.5, **overrides):
    """A solved network with the battery at `point`, plants producing 195 MW."""
    from corridor_sim import network as nw
    from corridor_sim.controls import NetIndex, solve
    cfg = build_config("dlr2_der4_bess", days=1, storage_at=point, **overrides)
    net, buses = nw.build(cfg)
    idx = NetIndex.build(net, buses)
    net.load.at[idx.loads["LOAD_SUB_A"], "p_mw"] = 15.0
    net.load.at[idx.loads["LOAD_AGG"], "p_mw"] = 25.0
    for unit, p in (("WF_1", 55.0), ("WF_2", 66.0), ("WF_3", 44.0), ("PV_1", 30.0)):
        net.sgen.at[idx.sgens[unit], "p_mw"] = p
    net.storage.at[idx.storage, "p_mw"] = -discharge_mw
    assert solve(net, "dc", deep=True)
    phase = {"mode": "discharge" if discharge_mw else "idle", "p_plant_mw": -discharge_mw,
             "headroom_clamped": 0, "shortfall_mw": 0.0, "charge_surplus_mw": 0.0,
             "charge_grid_mw": 0.0}
    return cfg, net, idx, phase, soc_frac * cfg.storage_e_mwh


def _just_below(net, idx, zone_lines, share=0.97):
    """A rating a few per cent under the zone's current, so it overloads slightly."""
    amps = max(float(net.res_line.at[idx.lines[n], "i_ka"]) * 1000.0
               for n in zone_lines if n in idx.lines)
    return share * amps


def _limits(net, idx, z1=True, z2=True):
    from corridor_sim.network import CORRIDOR_ZONES
    return {"Z1": _just_below(net, idx, CORRIDOR_ZONES["Z1"]) if z1 else 5000.0,
            "Z2": _just_below(net, idx, CORRIDOR_ZONES["Z2"]) if z2 else 5000.0}


def test_a_battery_upstream_charges_with_what_would_be_curtailed():
    from corridor_sim.constraints import scan
    cfg, net, idx, phase, soc = _battery_at("SUB_A")
    limits = _limits(net, idx)
    assert scan(net, idx, limits, cfg)[0] == 3
    relief = storage.relieve_congestion(net, cfg, idx, limits, soc, phase)
    assert 0.0 < relief["captured_mw"] <= cfg.storage_p_mw
    assert scan(net, idx, limits, cfg)[0] == 0, "charging upstream cleared the corridor"
    assert phase["p_plant_mw"] == pytest.approx(relief["captured_mw"])
    assert phase["mode"] == "charge_surplus"


def test_a_battery_at_the_pcc_cannot_relieve_the_corridor():
    cfg, net, idx, phase, soc = _battery_at("PCC")
    relief = storage.relieve_congestion(net, cfg, idx, _limits(net, idx), soc, phase)
    assert relief["captured_mw"] == 0.0 and relief["trials"] == 0
    assert float(net.storage.at[idx.storage, "p_mw"]) == 0.0


def test_a_battery_reaches_only_the_segments_between_it_and_the_pcc():
    """At SUB_C it relieves zone Z2, which its power runs through, not zone Z1."""
    cfg, net, idx, phase, soc = _battery_at("SUB_C")
    upstream = storage.relieve_congestion(net, cfg, idx, _limits(net, idx, z2=False),
                                          soc, dict(phase))
    assert upstream["captured_mw"] == 0.0
    downstream = storage.relieve_congestion(net, cfg, idx, _limits(net, idx, z1=False),
                                            soc, phase)
    assert downstream["captured_mw"] > 0.0


def test_a_discharge_is_held_back_before_generation_is_cut():
    """Generation has priority, so a discharge into congestion gives way first."""
    cfg, net, idx, phase, soc = _battery_at("SUB_A", discharge_mw=20.0)
    relief = storage.relieve_congestion(net, cfg, idx, _limits(net, idx), soc, phase)
    assert 0.0 < relief["withheld_mw"] <= 20.0 and relief["captured_mw"] == 0.0
    assert -20.0 < phase["p_plant_mw"] <= 0.0, "held back, never turned into a charge"
    assert phase["shortfall_mw"] == pytest.approx(relief["withheld_mw"])
    assert phase["headroom_clamped"] == 1


def test_the_battery_keeps_its_discharge_when_it_may_displace_generation():
    cfg, net, idx, phase, soc = _battery_at("SUB_A", discharge_mw=20.0,
                                            storage_may_curtail_der=True)
    relief = storage.relieve_congestion(net, cfg, idx, _limits(net, idx), soc, phase)
    assert relief["withheld_mw"] == 0.0


def test_a_full_battery_has_nothing_to_capture_with():
    cfg, net, idx, phase, _ = _battery_at("SUB_A")
    relief = storage.relieve_congestion(net, cfg, idx, _limits(net, idx), cfg.soc_max_mwh,
                                        phase)
    assert relief["captured_mw"] == 0.0


def test_relief_can_be_switched_off(tmp_path):
    from corridor_sim import dataio, network, simulate
    cfg = build_config("dlr2_der4_bess", days=None, start="2024-01-10",
                       end="2024-01-10T01:00:00", storage_at="SUB_A",
                       storage_relieves_congestion=False, out_dir=tmp_path)
    net, buses = network.build(cfg)
    result = simulate.run(cfg, net, buses, dataio.load_inputs(cfg), progress=False)
    assert (result["storage_captured_mw"] == 0.0).all()
    assert (result["storage_withheld_mw"] == 0.0).all()
