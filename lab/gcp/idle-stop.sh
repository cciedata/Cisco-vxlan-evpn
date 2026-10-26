#!/bin/bash
# Startup script of the lab VM (runs on every boot): idle stop.
# Stops the VM (guest shutdown = GCP STOP, disk kept) after IDLE_LIMIT seconds with no established SSH session.
LIMIT=$(curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/attributes/idle-limit-sec || true)
[[ "$LIMIT" =~ ^[0-9]+$ ]] || LIMIT=1800
cat > /usr/local/sbin/vamd-idle-check <<X
#!/bin/bash
F=/run/vamd-idle-since
now=\$(date +%s)
n=\$(ss -Htn state established '( sport = :22 )' | wc -l)
if [ "\$n" -gt 0 ]; then echo \$now > \$F; exit 0; fi
[ -f \$F ] || echo \$now > \$F
idle=\$(( now - \$(cat \$F) ))
if [ \$idle -ge $LIMIT ]; then logger -t vamd-idle "no SSH session for \$idle s (limit $LIMIT), stopping"; shutdown -h now; fi
X
chmod 755 /usr/local/sbin/vamd-idle-check
cat > /etc/systemd/system/vamd-idle.service <<X
[Unit]
Description=vamd idle stop check
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vamd-idle-check
X
cat > /etc/systemd/system/vamd-idle.timer <<X
[Unit]
Description=vamd idle stop check every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
X
systemctl daemon-reload
systemctl enable --now vamd-idle.timer
