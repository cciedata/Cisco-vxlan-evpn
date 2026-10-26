"""The rule-set mgmt addresses and vPC backup /31s reproduce the baselines in any plan order (offline).

A build from empty and a phased build used to give the firewalls, ISP routers and cloud routers different mgmt addresses
and the vPC pairs different backup /31s, because the Infrahub pools hand out addresses in creation order. Those are now
computed by rule from FabricDesign (mgmt0_rule_first_host, vpc_backup_rule_first_host). These tests rebuild them from
design/fabric-design.yml and the pool blocks in scripts/create_pools.py, compare them with the addresses in the
baseline configs, and check that shuffling the device order changes nothing.
"""
import ipaddress
import pathlib
import random
import re
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
fs = pytest.importorskip("generators.fabric_site")

SITES = ("DC1", "DC2")
# role -> device short-name prefix (generators/fabric_site.py FIXED_PAIRS)
PREFIX = {role: prefix for prefix, role, _ in fs.FIXED_PAIRS}


def design():
    doc = next(yaml.safe_load_all((ROOT / "design" / "fabric-design.yml").read_text()))
    return doc["spec"]["data"][0]


def pool_block(name):
    text = (ROOT / "scripts" / "create_pools.py").read_text()
    m = re.search(r'"name": "%s",\s*"prefix": "([^"]+)"' % re.escape(name), text)
    assert m, f"pool {name} not in create_pools.py"
    return ipaddress.ip_network(m.group(1))


def configure_rules(d):
    fs.MGMT0_RULE_FIRST_HOST = {k: int(v) for k, v in d["mgmt0_rule_first_host"].items()}
    fs.VPC_BACKUP_RULE_FIRST_HOST = {k: int(v) for k, v in d["vpc_backup_rule_first_host"].items()}
    fs.P2P_PLEN = d["p2p_link_prefix_length"]


def baseline_mgmt(device):
    cfg = (ROOT / "tests" / "expected" / f"{device}.cfg").read_text().splitlines()
    for i, line in enumerate(cfg):
        if re.match(r'^(interface (mgmt0|MgmtEth\S*)|\s*edit "mgmt1")$', line):
            for nxt in cfg[i + 1:i + 8]:
                m = re.search(r"(?:ip address|ipv4 address|set ip) (\d+\.\d+\.\d+\.\d+)", nxt)
                if m:
                    return m.group(1)
    raise AssertionError(f"{device}: no mgmt address in the baseline")


def baseline_backup(device, svi):
    cfg = (ROOT / "tests" / "expected" / f"{device}.cfg").read_text().splitlines()
    i = cfg.index(f"interface {svi}")
    for nxt in cfg[i + 1:i + 10]:
        m = re.search(r"ip address (\d+\.\d+\.\d+\.\d+)/(\d+)", nxt)
        if m:
            return m.group(1)
    raise AssertionError(f"{device}: no {svi} address in the baseline")


def plan(site, d):
    rows = []
    for role in d["mgmt0_rule_first_host"]:
        for n in range(1, 3):
            rows.append({"name": f"{site}-{PREFIX[role]}{n}", "role": role})
    return rows


def domains(site):
    # vPC domains with a backup SVI (VTEP pairs): server leaf pair 1, border leaves, service leaves; EDGE / DMZ have none
    return [{"name": f"{site}-VPC-L1-L2", "role": "leaf", "backup": True, "members": (f"{site}-L1", f"{site}-L2")},
            {"name": f"{site}-VPC-BL1-BL2", "role": "border_leaf", "backup": True, "members": (f"{site}-BL1", f"{site}-BL2")},
            {"name": f"{site}-VPC-SL1-SL2", "role": "service_leaf", "backup": True, "members": (f"{site}-SL1", f"{site}-SL2")},
            {"name": f"{site}-VPC-EDGE1-EDGE2", "role": "edge", "backup": False, "members": (f"{site}-EDGE1", f"{site}-EDGE2")}]


@pytest.mark.parametrize("site", SITES)
def test_mgmt_rule_matches_baselines_in_any_order(site):
    d = design()
    configure_rules(d)
    block = pool_block(f"{site}-MGMT0")
    rows = plan(site, d)
    want = {r["name"]: baseline_mgmt(r["name"]) for r in rows}
    assert fs.rule_mgmt_addresses(rows, block) == want
    for seed in range(5):                            # creation order must not matter
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert fs.rule_mgmt_addresses(shuffled, block) == want


@pytest.mark.parametrize("site", SITES)
def test_backup_rule_matches_baselines(site):
    d = design()
    configure_rules(d)
    block = pool_block(f"{site}-VPC-BACKUP")
    hosts = {int(k): v for k, v in d["vpc_backup_member_hosts"].items()}
    svi = f"Vlan{d['vpc_backup_vlan']}"
    nets = fs.rule_backup_networks(domains(site), block)
    assert set(nets) == {x["name"] for x in domains(site) if x["backup"]}
    for x in domains(site):
        if x["backup"]:
            for k, member in enumerate(x["members"], start=1):
                assert str(nets[x["name"]][hosts[k]]) == baseline_backup(member, svi)


def test_rules_refuse_collisions():
    d = design()
    configure_rules(d)
    block = pool_block("DC1-MGMT0")
    fs.MGMT0_RULE_FIRST_HOST = dict(fs.MGMT0_RULE_FIRST_HOST, internet_fw=fs.MGMT0_RULE_FIRST_HOST["isp_router"] + 1)
    with pytest.raises(ValueError, match="already given"):
        fs.rule_mgmt_addresses(plan("DC1", d), block)
    configure_rules(d)
    fs.VPC_BACKUP_RULE_FIRST_HOST = dict(fs.VPC_BACKUP_RULE_FIRST_HOST, border_leaf=fs.VPC_BACKUP_RULE_FIRST_HOST["leaf"] + 1)
    with pytest.raises(ValueError):                 # misaligned /31
        fs.rule_backup_networks(domains("DC1"), pool_block("DC1-VPC-BACKUP"))
    configure_rules(d)
    del fs.VPC_BACKUP_RULE_FIRST_HOST["service_leaf"]
    with pytest.raises(ValueError, match="no entry"):
        fs.rule_backup_networks(domains("DC1"), pool_block("DC1-VPC-BACKUP"))
