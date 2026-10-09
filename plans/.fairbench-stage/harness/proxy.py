#!/usr/bin/env python3
"""Minimal CONNECT proxy with a host allowlist. Everything not listed is refused.

This is an allowlist, not a denylist: the worker container has no route to the
outside except through here, so a host that is not in ALLOW is unreachable by
any means the agent can invent.
"""
import socket, select, sys, threading

ALLOW = {"api.fireworks.ai:443"}
LOG = open("/var/log/proxy-attempts.log", "a", buffering=1)

def pipe(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r: break
            for s in r:
                d = s.recv(65536)
                if not d: return
                (b if s is a else a).sendall(d)
    except OSError:
        pass

def handle(c):
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            d = c.recv(4096)
            if not d: return
            req += d
            if len(req) > 16384: return
        line = req.split(b"\r\n")[0].decode("latin1")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            LOG.write(f"REFUSED non-connect: {line[:120]}\n")
            c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n"); return
        target = parts[1]
        if target not in ALLOW:
            LOG.write(f"REFUSED {target}\n")
            c.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n"); return
        LOG.write(f"ALLOWED {target}\n")
        host, port = target.rsplit(":", 1)
        u = socket.create_connection((host, int(port)), timeout=30)
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        pipe(c, u); u.close()
    except Exception as e:
        LOG.write(f"ERROR {type(e).__name__}\n")
    finally:
        c.close()

s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 8888)); s.listen(64)
LOG.write("proxy up, allowlist=%s\n" % sorted(ALLOW))
while True:
    conn, _ = s.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
