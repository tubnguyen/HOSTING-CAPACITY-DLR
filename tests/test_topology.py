"""The network topology figure: what it shows and how it lays it out."""
from __future__ import annotations

import pytest

from corridor_sim import plots
from corridor_sim.config import build_config


def test_it_shows_what_the_run_connects_and_nothing_switched_off():
    cfg = build_config("dlr2_der2", storage_enabled=True, added_der=["solar:30@PCC"],
                       added_loads=["80@MID_A_C"])
    items = {e["name"]: e for e in plots.topology_elements(cfg)}
    assert set(items) == {"WF_1", "WF_2", "PV_2", "BESS", "LOAD_1"}, "WF_3 and PV_1 are off"
    assert items["WF_2"]["text"] == "108 MW" and not items["WF_2"]["added"]
    assert items["PV_2"]["bus"] == "PCC" and items["PV_2"]["added"]
    assert items["BESS"]["text"] == "30 MW / 60 MWh" and items["BESS"]["bus"] == "TAP_B"
    assert items["LOAD_1"]["kind"] == "load" and items["LOAD_1"]["bus"] == "MID_A_C"
    assert plots.topology_elements(build_config("baseline")) == []


@pytest.mark.parametrize("wanted", [[0.0, 0.0, 0.0], [0.0, 0.1, 5.0, 5.0], [3.0, 1.0, 2.0],
                                    [0.0, 1.3, 1.3, 1.3, 2.6], []])
def test_markers_are_spread_no_closer_than_the_gap(wanted):
    placed = plots.spread(wanted, 0.66)
    order = sorted(range(len(wanted)), key=lambda i: wanted[i])
    gaps = [placed[b] - placed[a] for a, b in zip(order, order[1:], strict=False)]
    assert all(g >= 0.66 - 1e-9 for g in gaps), "no two markers overlap, order is kept"
    if wanted:
        # A crowd is centred on where it wanted to be, not pushed one way.
        assert sum(placed) / len(placed) == pytest.approx(sum(wanted) / len(wanted))


def test_items_that_have_room_stay_on_their_bus():
    assert plots.spread([0.0, 1.3, 2.6], 0.66) == [0.0, 1.3, 2.6]


@pytest.mark.parametrize("cfg", [
    build_config("baseline"),
    build_config(),
    build_config("static_der2", storage_enabled=True,
                 added_der=["wind:50@SUB_C", "wind:70@SUB_C", "solar:20@SUB_C",
                            "solar:25.5@MID_C_PCC", "wind:40@MID_A_C"],
                 added_loads=["80@SUB_C", "30@PCC:0.95", "15@MID_C_PCC"]),
    build_config(conductor="twin", der_enabled=[], added_der=["wind:120@MID_A_C"]),
], ids=["nothing connected", "default", "crowded", "only added"])
def test_the_figure_is_drawn_for_any_network(cfg, tmp_path):
    path = tmp_path / "topology.png"
    plots.network_topology(cfg, path)
    assert path.stat().st_size > 5000
