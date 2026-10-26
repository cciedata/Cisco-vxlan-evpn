#!/usr/bin/env python3
"""Day-2 push with Nornir + netmiko (decided 7 Oct: inventory live from Infrahub, NX-OS config replace, SSH transport).

  nornir_push.py --site DC1 [--devices DC1-L11,...] [--branch main] --check     preview only: the replace patch, nothing applied
  nornir_push.py --site DC1 [--devices ...]                                      apply: checkpoint, config replace, verify, save

Per device: read the running-config over SSH -> build the file (Infrahub startup-config artifact + kept lines, build_cfg.py)
-> copy it to bootflash: over SCP (netmiko file_transfer) -> 'configure replace bootflash:<file> show-patch' (--check) or
checkpoint + 'configure replace bootflash:<file> verbose' -> 'show config-replace status' -> copy run start.
Report: lab/build/day2/nornir-<time>.txt. Exit 0 only when every device succeeds.
"""
import argparse
import os
import re
import sys
import tempfile
import time

from netmiko import file_transfer
from nornir.core import Nornir
from nornir.core.inventory import ConnectionOptions, Defaults, Groups, Host, Hosts, Inventory
from nornir.core.plugins.connections import ConnectionPluginRegister
from nornir.plugins.runners import ThreadedRunner

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from day2 import build_cfg, infrahub_inventory  # noqa: E402

NXOS_USER = os.environ.get("DAY2_USER", "admin")                 # lab: vrnetlab login; production: AAA (not designed)
NXOS_PASS = os.environ.get("DAY2_PASSWORD", "admin")
REMOTE_FILE = "day2-{device}.cfg"                                 # FU-14: one file per device on bootflash:
CHECKPOINT = "day2-before-{stamp}"                                # FU-14: rollback point before every replace
WORKERS = 10
READ_TIMEOUT = 600                                                # config replace on a full config can take minutes
REPORT_DIR = os.path.join(os.path.dirname(HERE), "build", "day2")


def push(task, check, branch, stamp):
    dev = task.host.name
    conn = task.host.get_connection("netmiko", task.nornir.config)
    lines = []
    running = conn.send_command("show running-config", read_timeout=READ_TIMEOUT)
    body, checksum, kept = build_cfg.build(dev, running, branch)
    lines.append(f"artifact {checksum[:12]}, {len(body.splitlines())} lines, kept from device {len(kept)}")
    remote = REMOTE_FILE.format(device=dev)
    with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as f:
        f.write(body)
        local = f.name
    try:
        t = file_transfer(conn, source_file=local, dest_file=remote, file_system="bootflash:", direction="put", overwrite_file=True)
    finally:
        os.unlink(local)
    lines.append(f"scp bootflash:{remote}: transferred={t.get('file_transferred')} verified={t.get('file_verified')}")
    if not t.get("file_verified"):
        raise RuntimeError("file transfer not verified")
    if check:
        patch = conn.send_command(f"configure replace bootflash:{remote} show-patch", read_timeout=READ_TIMEOUT)
        lines.append("patch preview:\n" + patch)
        return "\n".join(lines)
    cp = CHECKPOINT.format(stamp=stamp)
    lines.append(conn.send_command(f"checkpoint {cp}", read_timeout=120).strip() or f"checkpoint {cp}")
    out = conn.send_command(f"configure replace bootflash:{remote} verbose", read_timeout=READ_TIMEOUT,
                            expect_string=r"#\s*$")
    lines.append("replace:\n" + out[-3000:])
    status = conn.send_command("show config-replace status", read_timeout=120)
    lines.append("status:\n" + status)
    if not re.search(r"(?i)(success|completed)", status) or re.search(r"(?i)fail", status):
        log = conn.send_command("show config-replace log exec | last 40", read_timeout=120)     # 9 Oct: keep the switch's evidence
        raise RuntimeError(f"config replace not successful; rollback point {cp}\n" + "\n".join(lines[-2:]) + "\nlog:\n" + log)
    lines.append(conn.send_command("copy running-config startup-config", read_timeout=300, expect_string=r"#\s*$").strip()[-200:])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True)
    ap.add_argument("--devices")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--check", action="store_true", help="preview the replace patch only")
    a = ap.parse_args()
    devs = infrahub_inventory.devices(a.site, a.devices.split(",") if a.devices else None, a.branch)
    if not devs:
        sys.exit("STOP: no NX-OS devices selected")
    hosts = Hosts()
    for d in devs:
        hosts[d["name"]] = Host(name=d["name"], hostname=d["host"], username=NXOS_USER, password=NXOS_PASS,
                                platform=infrahub_inventory.NETMIKO[d["platform"]], data={"site": d["site"], "role": d["role"]},
                                connection_options={"netmiko": ConnectionOptions(extras={"fast_cli": False, "conn_timeout": 30})})
    ConnectionPluginRegister.auto_register()
    nr = Nornir(inventory=Inventory(hosts=hosts, groups=Groups(), defaults=Defaults()), runner=ThreadedRunner(num_workers=WORKERS))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    agg = nr.run(task=push, name="day2", check=a.check, branch=a.branch, stamp=stamp)
    nr.close_connections()
    os.makedirs(REPORT_DIR, exist_ok=True)
    rep, bad = [], []
    for host, multi in sorted(agg.items()):
        if multi.failed:
            bad.append(host)
            rep.append(f"== {host}: FAILED {type(multi[0].exception).__name__}: {multi[0].exception}")
        else:
            rep.append(f"== {host}: OK\n{multi[0].result}")
    head = f"Day-2 Nornir {'check' if a.check else 'push'} {a.site} {stamp}: {len(devs) - len(bad)} of {len(devs)} OK"
    txt = head + "\n" + "\n".join(rep) + "\n"
    open(os.path.join(REPORT_DIR, f"nornir-{stamp}.txt"), "w").write(txt)
    print(txt, end="")
    sys.exit(0 if not bad else 7)


if __name__ == "__main__":
    main()
