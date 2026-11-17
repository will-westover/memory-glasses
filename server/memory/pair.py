"""Show a QR code the iPhone app scans to connect to this server."""

import argparse
import ipaddress
import os
import re
import subprocess
import sys
from urllib.parse import urlencode

import segno

from memory.config import SERVER_DIR, load_env_file

TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
PORT = 8000


def local_addresses() -> list[tuple[str, ipaddress.IPv4Address]]:
    """Return each network interface with its IPv4 address."""
    output = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    found, interface = [], None

    for line in output.splitlines():
        if match := re.match(r"^(\S+):", line):
            interface = match.group(1)
        elif match := re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line):
            found.append((interface, ipaddress.ip_address(match.group(1))))

    return found


def best_address(addresses: list[tuple[str, ipaddress.IPv4Address]]) -> str | None:
    """Pick the Tailscale address if there is one, otherwise the Wi-Fi address."""
    tailscale = [ip for _, ip in addresses if ip in TAILSCALE]
    wifi = [ip for name, ip in addresses if name == "en0"]

    chosen = (tailscale or wifi or [None])[0]

    return str(chosen) if chosen else None


def pairing_link(server: str, token: str) -> str:
    """Return the link the app opens to save the server and token."""
    return "memoryglasses://pair?" + urlencode({"server": server, "token": token})


def main() -> int:
    parser = argparse.ArgumentParser(description="Show a QR code that connects the iPhone app to this server.")
    parser.add_argument("--address", help="the address the phone should use (default: Tailscale, else Wi-Fi)")
    args = parser.parse_args()

    load_env_file(SERVER_DIR / ".env")
    host = args.address or best_address(local_addresses())

    if not host:
        print("No network address found. Connect to Wi-Fi or Tailscale, or pass --address.", file=sys.stderr)
        return 1

    server = host if host.startswith("http") else f"http://{host}:{PORT}"
    segno.make(pairing_link(server, os.environ.get("API_TOKEN", "")), error="m").terminal(compact=True)

    print(f"\nScan with the iPhone camera to connect the app to {server}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
