#!/usr/bin/env python3
"""Create the ztp-callback account on the Infrahub instance and its API token (idempotent).

  setup_account.py [--new-token]        run with the repo venv from the repo root

Admin login: username/password. INFRAHUB_USERNAME / INFRAHUB_PASSWORD when set; otherwise user admin and the
password the repo's .env generated (INFRAHUB_INITIAL_ADMIN_PASSWORD; another .env with VAMD_INFRAHUB_ENV). There is no
built-in default password: on a fresh instance the old admin/infrahub default gave 401 at /api/auth/login.

Choices: dedicated account ztp-callback, account type Script, token with no expiry, writes directly on main.
Creates, when missing: object permissions Dcim:Device view (allow_all) + update (allow_default = main only), role and group
"ZTP Callback", account ztp-callback (random password, never printed), member of the group. Then logs in as ztp-callback, creates
the token (only if ~/.vamd/secrets.env has none, or --new-token) and stores it there as ZTP_CALLBACK_TOKEN (mode 600).
Last, a permission test: as the token, write DC1-L11's current ztp_state back unchanged (main), and the same on a branch must be refused.
Infrahub permissions are per object kind, not per attribute: the account can update any DcimDevice attribute on main.
"""
import os
import re
import secrets
import sys

import httpx

ADDR = os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.expanduser(os.environ.get("VAMD_INFRAHUB_ENV", os.path.join(REPO, ".env")))
ENV_ADMIN_PASSWORD = "INFRAHUB_INITIAL_ADMIN_PASSWORD"   # docker-compose.yml: the initial admin password, generated into .env
SECRETS = os.path.expanduser(os.environ.get("VAMD_SECRETS", "~/.vamd/secrets.env"))
NAME, ROLE, GROUP, TOKEN_NAME, TEST_DEV = "ztp-callback", "ZTP Callback", "ZTP Callback", "ztp-server", "DC1-L11"


def admin_login():
    """(user, password): INFRAHUB_USERNAME / INFRAHUB_PASSWORD, else admin + the .env password."""
    user = os.environ.get("INFRAHUB_USERNAME") or "admin"
    pw = os.environ.get("INFRAHUB_PASSWORD")
    if pw:
        return user, pw, "INFRAHUB_PASSWORD"
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            m = re.match(r"\s*(?:export\s+)?%s=(.*)$" % ENV_ADMIN_PASSWORD, line.strip())
            if m and m.group(1).strip("'\""):
                return user, m.group(1).strip("'\""), f"{ENV_ADMIN_PASSWORD} in {ENV_FILE}"
    sys.exit(f"STOP: no admin password: set INFRAHUB_PASSWORD, or run from the repo whose .env has {ENV_ADMIN_PASSWORD} "
             f"(looked in {ENV_FILE}; another file: VAMD_INFRAHUB_ENV)")


def login(user, pw):
    r = httpx.post(f"{ADDR}/api/auth/login", json={"username": user, "password": pw}, timeout=30)
    r.raise_for_status(); return {"Authorization": f"Bearer {r.json()['access_token']}"}


def gql(h, q, branch="main"):
    r = httpx.post(f"{ADDR}/graphql/{branch}", json={"query": q}, headers=h, timeout=60).json()
    if r.get("errors"):
        raise RuntimeError(str(r["errors"])[:500])
    return r["data"]


def find(h, kind, field, value):
    e = gql(h, '{ %s(%s__value: "%s") { edges { node { id } } } }' % (kind, field, value))[kind]["edges"]
    return e[0]["node"]["id"] if e else None


def main():
    user, pw, source = admin_login()
    print(f"admin login: {user}, password from {source}")
    A = login(user, pw)
    # decision encoding, read from the instance's own permissions (never assumed)
    perms = gql(A, "{ CoreObjectPermission { edges { node { id identifier { value } decision { value } } } } }")["CoreObjectPermission"]["edges"]
    enc = {}
    for p in perms:
        m = re.search(r":(deny|allow_default|allow_other|allow_all)$", p["node"]["identifier"]["value"] or "")
        if m: enc[m.group(1)] = p["node"]["decision"]["value"]
    print("decision encoding on this instance:", enc)
    known = {"deny": 1, "allow_default": 2, "allow_other": 4, "allow_all": 6}
    if any(known[k] != v for k, v in enc.items()) or "allow_all" not in enc:
        sys.exit(f"STOP: decision values differ from the expected encoding {known}")
    ids = []
    for action, decision in (("view", "allow_all"), ("update", "allow_default")):
        ident = f"object:Dcim:Device:{action}:{decision}"
        e = gql(A, '{ CoreObjectPermission(namespace__value: "Dcim", name__value: "Device", action__value: "%s", decision__value: %d) '
                   '{ edges { node { id } } } }' % (action, known[decision]))["CoreObjectPermission"]["edges"]
        pid = e[0]["node"]["id"] if e else None    # 8 Oct: look up by namespace/name/action/decision (the identifier filter did not match)
        if not pid:
            pid = gql(A, 'mutation { CoreObjectPermissionCreate(data: {namespace: {value: "Dcim"}, name: {value: "Device"}, '
                         'action: {value: "%s"}, decision: {value: %d}, description: {value: "ZTP callback"}}) '
                         '{ ok object { id identifier { value } } } }' % (action, known[decision]))["CoreObjectPermissionCreate"]["object"]["id"]
            print("permission created:", ident)
        else:
            print("permission exists:", ident)
        ids.append(pid)
    # writing on main also needs the global permission edit_default_branch (✅ 8 Oct: refused without it); the object permissions above
    # still limit the account to updating DcimDevice
    g = gql(A, '{ CoreGlobalPermission(action__value: "edit_default_branch") { edges { node { id decision { value } } } } }')["CoreGlobalPermission"]["edges"]
    g = [x["node"]["id"] for x in g if x["node"]["decision"]["value"] == known["allow_all"]]
    if not g:
        g = [gql(A, 'mutation { CoreGlobalPermissionCreate(data: {action: {value: "edit_default_branch"}, decision: {value: %d}, '
                    'description: {value: "ZTP callback"}}) { ok object { id } } }' % known["allow_all"])["CoreGlobalPermissionCreate"]["object"]["id"]]
        print("global permission created: edit_default_branch")
    else:
        print("global permission exists: edit_default_branch")
    ids.append(g[0])
    plist = ", ".join('{id: "%s"}' % i for i in ids)
    rid = find(A, "CoreAccountRole", "name", ROLE)
    if not rid:
        rid = gql(A, 'mutation { CoreAccountRoleCreate(data: {name: {value: "%s"}, permissions: [%s]}) { ok object { id } } }'
                  % (ROLE, plist))["CoreAccountRoleCreate"]["object"]["id"]; print("role created:", ROLE)
    else:
        gql(A, 'mutation { CoreAccountRoleUpdate(data: {id: "%s", permissions: [%s]}) { ok } }' % (rid, plist)); print("role exists:", ROLE)
    gid = find(A, "CoreAccountGroup", "name", GROUP)
    if not gid:
        gid = gql(A, 'mutation { CoreAccountGroupCreate(data: {name: {value: "%s"}, roles: [{id: "%s"}]}) { ok object { id } } }'
                  % (GROUP, rid))["CoreAccountGroupCreate"]["object"]["id"]; print("group created:", GROUP)
    else:
        print("group exists:", GROUP)
    vals = {}
    if os.path.exists(SECRETS):
        for line in open(SECRETS):
            m = re.match(r"([A-Z0-9_]+)=(.*)$", line.strip())
            if m: vals[m.group(1)] = m.group(2).strip("'\"")
    aid = find(A, "CoreAccount", "name", NAME)
    pw = vals.get("ZTP_CALLBACK_PASSWORD")
    if not aid:
        pw = secrets.token_urlsafe(24)
        aid = gql(A, 'mutation { CoreAccountCreate(data: {name: {value: "%s"}, password: {value: "%s"}, account_type: {value: "Script"}, '
                     'description: {value: "ZTP server callback: writes ztp_state on main"}, member_of_groups: [{id: "%s"}]}) '
                     '{ ok object { id } } }' % (NAME, pw, gid))["CoreAccountCreate"]["object"]["id"]
        print("account created:", NAME, "(Script)")
    else:
        print("account exists:", NAME)
        if not pw:
            pw = secrets.token_urlsafe(24)
            gql(A, 'mutation { CoreAccountUpdate(data: {id: "%s", password: {value: "%s"}}) { ok } }' % (aid, pw)); print("password reset (not stored before)")
    tok = vals.get("ZTP_CALLBACK_TOKEN")
    if not tok or "--new-token" in sys.argv:
        U = login(NAME, pw)
        r = gql(U, 'mutation { InfrahubAccountTokenCreate(data: {name: "%s"}) { ok object { token { value } } } }' % TOKEN_NAME)
        tok = r["InfrahubAccountTokenCreate"]["object"]["token"]["value"]; print("token created:", TOKEN_NAME, "(no expiry)")
    vals.update({"ZTP_CALLBACK_PASSWORD": pw, "ZTP_CALLBACK_TOKEN": tok})
    os.makedirs(os.path.dirname(SECRETS), mode=0o700, exist_ok=True)
    keep = [l for l in (open(SECRETS).read().splitlines() if os.path.exists(SECRETS) else []) if not re.match(r"(ZTP_CALLBACK_PASSWORD|ZTP_CALLBACK_TOKEN)=", l)]
    with open(SECRETS + ".tmp", "w") as f:
        f.write("\n".join(keep + [f"ZTP_CALLBACK_PASSWORD='{pw}'", f"ZTP_CALLBACK_TOKEN='{tok}'"]) + "\n")
    os.chmod(SECRETS + ".tmp", 0o600); os.replace(SECRETS + ".tmp", SECRETS); print("secrets file updated:", SECRETS)
    # permission test with the token
    T = {"X-INFRAHUB-KEY": tok}
    d = gql(A, '{ DcimDevice(name__value: "%s") { edges { node { id ztp_state { value } } } } }' % TEST_DEV)["DcimDevice"]["edges"][0]["node"]
    cur = d["ztp_state"]["value"]
    val = '"%s"' % cur if cur else "null"
    try:
        gql(T, 'mutation { DcimDeviceUpdate(data: {id: "%s", ztp_state: {value: %s}}) { ok } }' % (d["id"], val)); print(f"TEST main: ztp-callback may update {TEST_DEV} ztp_state ({cur}, unchanged): OK")
    except RuntimeError as e:
        print(f"TEST main: REFUSED {e}"); sys.exit(4)
    br = gql(A, "{ Branch { name } }")["Branch"]
    other = next((b["name"] for b in br if b["name"] != "main"), None)
    if other:
        try:
            gql(T, 'mutation { DcimDeviceUpdate(data: {id: "%s", ztp_state: {value: %s}}) { ok } }' % (d["id"], val), branch=other)
            print(f"TEST branch {other}: allowed (NOT expected)")
        except RuntimeError as e:
            print(f"TEST branch {other}: refused as expected ({str(e)[:120]})")
    print("SETUP DONE")


if __name__ == "__main__":
    main()
