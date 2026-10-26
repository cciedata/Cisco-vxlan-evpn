# ZTP: NX-OS POAP from Infrahub

A blank NX-OS switch provisions itself: DHCP (Kea) points it to `poap.py` over HTTP, the script reads the switch serial,
the ZTP server finds the device with that `serial_number` in Infrahub and serves its `startup-config` artifact live,
POAP reloads into that config, and the server verifies the switch and records the result in `DcimDevice.ztp_state` on main.

| State | Written by | When |
|---|---|---|
| `pending` | a person / PC | the device is expected to ZTP (serial recorded) |
| `served` | ZTP server | config sent to that serial |
| `complete` | ZTP server | after the POAP reload, SSH to the address of the device's management interface (FabricDesign `<role>_mgmt_interface`) answers with hostname = device name, and its release (`show version`) equals `DcimPlatform.os_version` |
| `failed` | ZTP server | the POAP script reported an error, no verified login within `FabricDesign.ztp_verify_window_s` of the callback, or the running release differs from `DcimPlatform.os_version` |

ZTP does not install images: a switch on another release ends `failed`; the server logs a `version-mismatch` event
(running release, `os_version`, the `show version` line) in `ztp/ztp-events.jsonl`, the state reason names both releases, and
`run-ztp.sh status` shows the release check. Design values come from Infrahub (branch main, or `--branch <name>`) through `ztp_design.py`; a missing one ends the
device `failed` with the field named.

Every write also sets `ztp_updated`. The server writes as the `ztp-callback` account (Script, API token, no expiry):
object permissions `Dcim:Device` view (all branches) and update (main only). Permissions are per object kind, not per
attribute.

## Files
| File | What |
|---|---|
| `poap.py` | POAP script (the server fills its address and md5): serial, `boot nxos`, config by serial, scheduled-config, callback; errors go to `/callback/<serial>/failed/<reason>` |
| `ztp_server.py` | HTTP server: `/poap.py`, `/config/<serial>`, `/callback/<serial>[/failed/<reason>]`; state writes; verification |
| `ztp_design.py` | reads the ZTP design values (FabricDesign `ztp_*`, `<role>_mgmt_interface`, `local_admin_username`; `DcimPlatform.os_version`) for the server and `run-ztp.sh` |
| `setup_account.py` | creates the permissions, role/group "ZTP Callback", account `ztp-callback`, its token; permission test |
| `make-secret.sh` | asks for the password of the local admin account (`FabricDesign.local_admin_username`), writes it and its type-5 hash to `~/.vamd/secrets.env` |
| `kea-dhcp4.json` | lab DHCP template on `br-ztp`, options 66/67 to the server; `run-ztp.sh start` fills the subnet, pool and server address from FabricDesign (`ztp_dhcp_subnet`, `ztp_pool_start_host`/`ztp_pool_end_host`, `ztp_server_host`) into `/var/lib/vamd-ztp/kea-dhcp4.json` |
| `run-ztp.sh` | lab host: prep / start / status / stop (bridge, Kea, server, blank N9Kv under QEMU, console log) |
| `ztp_console.py` | lab host: serial console logger |

## Secrets (never in Git)
Rendered configs and baselines carry the local admin account's username line (`FabricDesign.local_admin_username`) with
`password 5 __CISCOADMIN_HASH__`. Every delivery
path fills the placeholder from `~/.vamd/secrets.env` (`lab/vamd_secrets.py`): the ZTP server, `labctl fetch` (lab deploy),
the Day-2 push. Without `CISCOADMIN_HASH` the line is dropped with a warning and delivery goes on.

```
bash ztp/make-secret.sh                          # CISCOADMIN_PASSWORD, CISCOADMIN_HASH
.venv/bin/python ztp/setup_account.py            # ZTP_CALLBACK_PASSWORD, ZTP_CALLBACK_TOKEN (+ permission test); admin password from .env
bash ztp/run-ztp.sh prep && bash ztp/run-ztp.sh start DC1-L11
bash ztp/run-ztp.sh status
```
`setup_account.py` logs in as `admin` with the password the repo's `.env` generated (`INFRAHUB_INITIAL_ADMIN_PASSWORD`);
`INFRAHUB_USERNAME` / `INFRAHUB_PASSWORD` override it, `VAMD_INFRAHUB_ENV` points at another `.env`.

Lab note: the verification needs a route from the server to the switch's management address; `run-ztp.sh start` adds the
device's management gateway to `br-ztp`, unless that subnet is already in use on the host (then the run ends `failed` after
`FabricDesign.ztp_verify_window_s`).
