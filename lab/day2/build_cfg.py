#!/usr/bin/env python3
"""Day-2 config file for one NX-OS device: the Infrahub startup-config artifact (current, Ready) + the lines the device
must keep that Infrahub does not model (decided 7 Oct, FU-13): local accounts, password policy, boot variables, the lab
management features, and (9 Oct) the TCAM carve lines (set on the device, applied at reload; config replace cannot remove them). The kept lines are read from the device's own running-config (passed in), never typed per device.

  build_cfg.py <device> --running <file with 'show running-config'> --out <file>     (Ansible: one call per host)
  from day2.build_cfg import build                                                     (Nornir)
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import labctl  # noqa: E402  (Infrahub client, artifact read)
import vamd_secrets  # noqa: E402  (Phase 3b: delivery-time secrets)

KEEP = [r"^no password strength-check$", r"^username \S+ password ", r"^username \S+ role ", r"^username \S+ sshkey ",
        r"^boot nxos ", r"^hardware access-list tcam region ", r"^feature (nxapi|scp-server)$", r"^ssh key ", r"^snmp-server user admin "]   # FU-13; snmp admin: FU-20
KEEP_RE = re.compile("|".join(KEEP))
# FU-20 (7 Oct, job 233): NX-OS 'configure replace' pre-check refused the file: "Version command not found in User config file",
# "does not contain mandatory [snmp-server user admin ] command", "does not contain mandatory [vlan 1] command".
REPLACE_MANDATORY_VLAN = "vlan 1"
VERSION_RE = re.compile(r"^version \S+")


def kept_lines(running):
    return [l for l in running.splitlines() if KEEP_RE.match(l)]


def artifact(device, branch):
    cache = os.environ.get("DAY2_CACHE")                 # FU-17 snapshot (infrahub_inventory.py --export)
    if cache:
        import json
        return open(os.path.join(cache, "configs", f"{device}.cfg")).read(), json.load(open(os.path.join(cache, "checksums.json")))[device]
    text, art = labctl.Infrahub(branch).artifact("startup-config", device)
    return text, art["checksum"]


def build(device, running, branch="main"):
    """Returns (config text, artifact checksum, kept lines)."""
    text, checksum = artifact(device, branch)
    text, warns = vamd_secrets.fill(text)                                          # Phase 3b: __VAMDADMIN_HASH__
    for w in warns:
        print(f"WARNING {device}: {w}", file=sys.stderr)
    keep = kept_lines(running)
    lines = text.splitlines()
    have = set(lines)
    extra = [l for l in keep if l not in have]
    version = [l for l in running.splitlines() if VERSION_RE.match(l)][:1]          # FU-20: first line of the file
    head = [] if any(VERSION_RE.match(l) for l in lines) else version
    if not any(l == REPLACE_MANDATORY_VLAN or l.startswith(REPLACE_MANDATORY_VLAN + ",") for l in lines):
        extra.append(REPLACE_MANDATORY_VLAN)                                           # FU-20: mandatory for config replace
    body = "\n".join(head + [text.rstrip("\n")]) + "\n"
    if extra:
        body += "\n".join(["!", "! Day-2: kept from the device or required by config replace (not modeled in Infrahub)"] + extra) + "\n"
    return body, checksum, head + extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("device")
    ap.add_argument("--running", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--branch", default="main")
    a = ap.parse_args()
    body, checksum, extra = build(a.device, open(a.running).read(), a.branch)
    open(a.out, "w").write(body)
    print(f"{a.device}: artifact {checksum[:12]}, {len(body.splitlines())} lines, kept from device {len(extra)}")


if __name__ == "__main__":
    main()
