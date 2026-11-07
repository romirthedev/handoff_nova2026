"""Run Handoff on this laptop so a phone on the same Wi-Fi can be the pass camera.

    .venv/bin/python run_local.py

Laptop: http://localhost:8820. Phones: https://<this laptop's Wi-Fi address>:8443, which the dashboard's QR code
points at. Phones only open the camera over https, so that link uses a self-signed certificate made here; the
phone warns about it once (Safari: Show Details > visit this website; Chrome: Advanced > Proceed).
"""

import asyncio
import os
import socket
import subprocess
from pathlib import Path

import uvicorn

HTTP_PORT, HTTPS_PORT = 8820, 8443
CERTS = Path(__file__).parent / "data" / "certs"


def wifi_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outward interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def certificate(ip: str) -> tuple[Path, Path]:
    cert, key, stamp = CERTS / "cert.pem", CERTS / "key.pem", CERTS / "ip.txt"
    if not (cert.exists() and stamp.exists() and stamp.read_text() == ip):
        CERTS.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-subj", "/CN=Handoff",
                        "-addext", f"subjectAltName=IP:{ip},DNS:localhost", "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True)
        stamp.write_text(ip)
    return cert, key


def load_env():
    """Read KEY=value lines from handoff/.env (e.g. ANTHROPIC_API_KEY) without overriding the shell."""
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main():
    load_env()
    ip = wifi_ip()
    servers = []
    if ip:
        cert, key = certificate(ip)
        os.environ["HANDOFF_LAN_URL"] = f"https://{ip}:{HTTPS_PORT}"
    from app.server import app  # reads HANDOFF_LAN_URL and the API key
    from app.vision import engine_label
    print(f"\n  Plate checks: {engine_label()} (falls back to the codes printed on the cards)")

    servers.append(uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=HTTP_PORT)))
    if ip:  # same app and same tickets, second listener; lifespan runs once on the first
        servers.append(uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=HTTPS_PORT, lifespan="off",
                                                     ssl_certfile=str(cert), ssl_keyfile=str(key))))
        print(f"\n  Laptop:  http://localhost:{HTTP_PORT}\n  Phones:  https://{ip}:{HTTPS_PORT}  (same Wi-Fi)\n")
    else:
        print("\n  No Wi-Fi address found, so phones can't reach this laptop. Use the deployed site for the phone.\n")
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
