#!/bin/env python3
#md5sum="__MD5__"
# vamd POAP script (HTTP, serial number as identity, config live from Infrahub through the ZTP server).
# Served by ztp/ztp_server.py, which fills __SRV__ and the md5 line. Steps: read the serial, set the boot variable (8 Oct finding:
# without it POAP's reload stops at 'loader >'), download the config by serial, make it the scheduled-config, call back.
# Any error is reported to /callback/<serial>/failed (ztp_state = failed) and POAP is aborted.
import re
import sys
import time
from cli import cli

SRV = "__SRV__"


def log(msg):
    with open("/bootflash/vamd_poap.log", "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def report_failed(sn, step, err):
    log("FAILED at %s: %s" % (step, err))
    reason = re.sub(r"[^A-Za-z0-9_.-]", "_", "%s:%s" % (step, err))[:120]
    try:
        cli("copy http://%s/callback/%s/failed/%s bootflash:vamd_callback.txt vrf management" % (SRV, sn, reason))
    except Exception:
        pass
    sys.exit(1)


sn = "UNKNOWN"
step = "serial"
try:
    m = re.search(r"SN:\s*(\S+)", cli("show inventory"))
    sn = m.group(1) if m else "UNKNOWN"
    log("serial " + sn)
    step = "boot-variable"
    mi = re.search(r"NXOS image file is:\s*bootflash:/+(\S+)", cli("show version"))
    if not mi:
        raise RuntimeError("no NXOS image in show version")
    cli("configure terminal ; boot nxos bootflash:%s ; end" % mi.group(1))
    cli("copy running-config startup-config")
    log("boot nxos bootflash:" + mi.group(1))
    step = "config-download"
    cli("copy http://%s/config/%s bootflash:vamd_poap.cfg vrf management" % (SRV, sn))
    log("config downloaded")
    step = "scheduled-config"
    cli("copy bootflash:vamd_poap.cfg scheduled-config")
    log("scheduled-config set")
    step = "callback"
    cli("copy http://%s/callback/%s bootflash:vamd_callback.txt vrf management" % (SRV, sn))
    log("callback sent; POAP reloads and applies the scheduled config")
except SystemExit:
    raise
except Exception as e:
    report_failed(sn, step, e)
