#!/usr/bin/env python3
"""Lab only: log the ZTP N9Kv serial console (telnet 127.0.0.1:5099) to /var/lib/vamd-ztp/console.log; never types anything."""
import socket
import time

while True:
    try:
        s = socket.create_connection(("127.0.0.1", 5099), timeout=10); s.settimeout(None)
        with open("/var/lib/vamd-ztp/console.log", "ab") as f:
            while True:
                b = s.recv(4096)
                if not b:
                    break
                f.write(b.replace(b"\xff", b"")); f.flush()
    except Exception:
        time.sleep(3)
