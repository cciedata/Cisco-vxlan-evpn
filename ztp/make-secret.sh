#!/bin/bash
# Write the ciscoadmin password and its NX-OS type-5 hash (SHA-256 crypt) to ~/.vamd/secrets.env (mode 600).
# Prompts twice; keeps every other line already in the file (for example ZTP_CALLBACK_TOKEN). Never prints the password.
set -eu
F=${VAMD_SECRETS:-$HOME/.vamd/secrets.env}
mkdir -p "$(dirname "$F")"; chmod 700 "$(dirname "$F")"; touch "$F"; chmod 600 "$F"
read -rsp "ciscoadmin password: " p1; echo; read -rsp "again: " p2; echo
[ -n "$p1" ] && [ "$p1" = "$p2" ] || { echo "passwords empty or different"; exit 1; }
h=$(printf '%s' "$p1" | openssl passwd -5 -stdin)
grep -vE '^(CISCOADMIN_PASSWORD|CISCOADMIN_HASH)=' "$F" > "$F.tmp" || true
printf "CISCOADMIN_PASSWORD='%s'\nCISCOADMIN_HASH='%s'\n" "$p1" "$h" >> "$F.tmp"; mv "$F.tmp" "$F"; chmod 600 "$F"
echo "written: $F (CISCOADMIN_PASSWORD, CISCOADMIN_HASH ${h:0:3}...)"
