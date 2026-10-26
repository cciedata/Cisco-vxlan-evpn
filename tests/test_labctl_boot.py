"""labctl staged N9Kv boot and boot watchdog, offline (no containerlab, no Docker)."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lab"))
pytest.importorskip("httpx")
labctl = pytest.importorskip("labctl")


def topo(n9k=10, linux=3):
    nodes = {f"N{i}": {"kind": "cisco_n9kv"} for i in range(1, n9k + 1)}
    nodes.update({f"H{i}": {"kind": "linux"} for i in range(1, linux + 1)})
    return {"name": "t", "topology": {"nodes": nodes, "links": []}}


def waits(t, node):
    return [w["node"] for w in t["topology"]["nodes"][node].get("stages", {}).get("create", {}).get("wait-for", [])]


def test_groups_of_five_wait_for_the_whole_previous_group():
    t, groups = labctl.staged_topology(topo(12), 5)
    assert groups == [["N1", "N2", "N3", "N4", "N5"], ["N6", "N7", "N8", "N9", "N10"], ["N11", "N12"]]
    assert all(waits(t, n) == [] for n in groups[0])
    assert all(waits(t, n) == groups[0] for n in groups[1])
    assert all(waits(t, n) == groups[1] for n in groups[2])
    assert all(waits(t, f"H{i}") == [] for i in range(1, 4))            # only N9Kv are staged
    assert all(w["stage"] == "healthy" for n in groups[1] for w in t["topology"]["nodes"][n]["stages"]["create"]["wait-for"])


def test_group_size_zero_or_larger_than_the_lab_is_one_group():
    for size in (0, 10, 50):
        t, groups = labctl.staged_topology(topo(10), size)
        assert len(groups) == 1 and not any("stages" in s for s in t["topology"]["nodes"].values())


def test_node_filter_limits_the_groups():
    _, groups = labctl.staged_topology(topo(10), 2, only={"N2", "N4", "N9", "H1"})
    assert groups == [["N2", "N4"], ["N9"]]


def test_source_topology_is_not_changed():
    src = topo(10)
    labctl.staged_topology(src, 5)
    assert not any("stages" in s for s in src["topology"]["nodes"].values())


def fake(health, last, began):
    return dict(health=lambda c: health[c.split("-")[-1]], log_time=lambda c: last.get(c.split("-")[-1]),
                start_time=lambda c: began.get(c.split("-")[-1]), stuck=600)


def test_watchdog_flags_only_a_quiet_unhealthy_node():
    now = 10_000
    health = {"N1": "healthy", "N2": "starting", "N3": "starting", "N4": "missing", "N5": "starting"}
    last = {"N1": now - 5000, "N2": now - 700, "N3": now - 30, "N5": now - 900}
    began = {"N1": now - 6000, "N2": now - 2000, "N3": now - 2000, "N5": now - 300}
    hung = labctl.hung_nodes(["N1", "N2", "N3", "N4", "N5"], "t", now, **fake(health, last, began))
    # N1 healthy, N3 log moving, N4 not created yet, N5 started under 10 min ago: only N2 is hung
    assert [h.split("(")[0] for h in hung] == ["N2"]


def test_watchdog_negative_control_moving_log_is_not_hung():
    now = 10_000
    hung = labctl.hung_nodes(["N2"], "t", now, **fake({"N2": "starting"}, {"N2": now - 599}, {"N2": now - 5000}))
    assert hung == []
