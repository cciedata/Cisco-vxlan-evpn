"""Fill delivery-time secrets into rendered configs (Phase 3b, 8 Oct).

Rendered configs and baselines carry placeholders only (the repo is public). Every delivery path (labctl fetch for a lab deploy,
the Day-2 push, the ZTP server) calls fill() just before the config leaves. Values come from ~/.vamd/secrets.env (mode 600),
written by ztp/make-secret.sh. Decision (decided 8 Oct): when a secret is missing, the line that carries its placeholder is
dropped and a warning is returned; delivery goes on.
"""
import os
import re

SECRETS_FILE = os.path.expanduser(os.environ.get("VAMD_SECRETS", "~/.vamd/secrets.env"))
PLACEHOLDERS = {"__VAMDADMIN_HASH__": "VAMDADMIN_HASH"}       # placeholder in the config -> key in secrets.env


def load(path=SECRETS_FILE):
    vals = {}
    if os.path.exists(path):
        for line in open(path):
            m = re.match(r"\s*([A-Z0-9_]+)=(.*)$", line.rstrip("\n"))
            if m:
                vals[m.group(1)] = m.group(2).strip().strip("'\"")
    for k in list(PLACEHOLDERS.values()) + ["VAMDADMIN_PASSWORD", "ZTP_CALLBACK_TOKEN"]:
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def fill(text, vals=None):
    """Return (text, warnings). Each placeholder is replaced by its secret; with no secret, its whole line is dropped."""
    vals = load() if vals is None else vals
    warnings = []
    for ph, key in PLACEHOLDERS.items():
        if ph not in text:
            continue
        if vals.get(key):
            text = text.replace(ph, vals[key])
        else:
            n = sum(1 for l in text.splitlines() if ph in l)
            text = "".join(l for l in text.splitlines(keepends=True) if ph not in l)
            warnings.append(f"{key} not set ({SECRETS_FILE}): dropped {n} line(s) carrying {ph}")
    return text, warnings
