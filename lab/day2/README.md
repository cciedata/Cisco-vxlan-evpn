# Day-2 push (lab/day2)

Pushes the Infrahub `startup-config` artifact to NX-OS devices with **config replace** over **SSH**, from an inventory read
**live from Infrahub**. Two tools, same steps:

```
.venv-lab/bin/python lab/day2/nornir_push.py --site DC1 --devices DC1-L11 --check     # preview the replace patch
.venv-lab/bin/python lab/day2/nornir_push.py --site DC1 --devices DC1-L11             # apply
cd lab/day2/ansible && ../../../.venv-lab/bin/ansible-playbook day2_push.yml -e site=DC1 -e devices=DC1-L11 -e check=true
cd lab/day2/ansible && ../../../.venv-lab/bin/ansible-playbook day2_push.yml -e site=DC1 -e devices=DC1-L11
```

Steps per device: read the running-config -> build the file (`build_cfg.py`: artifact + the lines Infrahub does not model,
kept from the device; `build_cfg.py` defines which lines those are) -> SCP to `bootflash:` ->
`configure replace ... show-patch` (check) or checkpoint + `configure replace` -> `show config-replace status` -> save.

Needs on the device: SSH and `feature scp-server`, rendered on every production NX-OS config from FabricDesign.nxos_scp_server.
Setup: `uv venv .venv-lab && uv pip install -p .venv-lab -r lab/requirements-lab.txt` (Nornir, netmiko, httpx, PyYAML, pinned);
Ansible runs from `.venv-lab` (ansible-core 2.20.1 + ansible-pylibssh, FU-21); collections: `.venv-lab/bin/ansible-galaxy collection install -r lab/day2/ansible/requirements.yml`.

Config replace pre-check (NX-OS, found 7 Oct on the first device run): the file needs a `version` line, the default
`snmp-server user admin` and `vlan 1`; `build_cfg.py` takes the first two from the device and adds `vlan 1` (FU-20).

Inventory: one small in-repo query (`infrahub_inventory.py`) feeds both tools, live from
Infrahub. The OpsMill Ansible inventory plugin (opsmill.infrahub 1.9.0) and nornir-infrahub (1.2.0) exist; the plugin needs
infrahub-sdk in the Ansible Python (laptop 1's system Python has no pip), so the shared script is used; same data, one query.
Reports: `lab/build/day2/`.
