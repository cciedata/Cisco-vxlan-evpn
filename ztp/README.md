# ZTP (Phase 3b): NX-OS POAP from Infrahub

A blank NX-OS switch provisions itself: DHCP (Kea) points it to `poap.py` over HTTP, the script reads the switch serial,
the ZTP server finds the device with that `serial_number` in Infrahub and serves its `startup-config` artifact live,
POAP reloads into that config, and the server verifies the switch and records the result in `DcimDevice.ztp_state` on main.

| State | Written by | When |
|---|---|---|
| `pending` | a person / PC | the device is expected to ZTP (serial recorded) |
| `served` | ZTP server | config sent to that serial |
| `complete` | ZTP server | after the POAP reload, SSH to the device's mgmt0 address answers with hostname = device name |
| `failed` | ZTP server | the POAP script reported an error, or no verified login within 30 min of the callback |

Every write also sets `ztp_updated`. The server writes as the `ztp-callback` account (Script, API token, no expiry):
object permissions `Dcim:Device` view (all branches) and update (main only). Permissions are per object kind, not per
attribute.

## Files
| File | What |
|---|---|
| `poap.py` | POAP script (the server fills its address and md5): serial, `boot nxos`, config by serial, scheduled-config, callback; errors go to `/callback/<serial>/failed/<reason>` |
| `ztp_server.py` | HTTP server: `/poap.py`, `/config/<serial>`, `/callback/<serial>[/failed/<reason>]`; state writes; verification |
| `setup_account.py` | creates the permissions, role/group "ZTP Callback", account `ztp-callback`, its token; permission test |
| `make-secret.sh` | asks for the `vamdadmin` password, writes it and its type-5 hash to `~/.vamd/secrets.env` |
| `kea-dhcp4.json` | lab DHCP: 10.250.99.0/24 on `br-ztp`, options 66/67 to the server |
| `run-ztp.sh` | lab host: prep / start / status / stop (bridge, Kea, server, blank N9Kv under QEMU, console log) |
| `ztp_console.py` | lab host: serial console logger |

## Secrets (never in Git)
Rendered configs and baselines carry `username vamdadmin password 5 __VAMDADMIN_HASH__ role network-admin`. Every delivery
path fills the placeholder from `~/.vamd/secrets.env` (`lab/vamd_secrets.py`): the ZTP server, `labctl fetch` (lab deploy),
the Day-2 push. Without `VAMDADMIN_HASH` the line is dropped with a warning and delivery goes on.

```
bash ztp/make-secret.sh                          # VAMDADMIN_PASSWORD, VAMDADMIN_HASH
.venv/bin/python ztp/setup_account.py            # ZTP_CALLBACK_PASSWORD, ZTP_CALLBACK_TOKEN (+ permission test)
bash ztp/run-ztp.sh prep && bash ztp/run-ztp.sh start DC1-L11
bash ztp/run-ztp.sh status
```
Lab note: the verification needs a route from the server to the switch's mgmt0 address; `run-ztp.sh start` adds the device's
mgmt0 gateway to `br-ztp`, unless that subnet is already in use on the host (then the run ends `failed` after 30 min).
