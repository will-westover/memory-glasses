"""Named places: turn coordinates into a name such as "Home".

    uv run python -m memory.places add "Home" 40.2518 -111.6493 --radius 75
    uv run python -m memory.places list
"""

import argparse
import math
import re
import sys

from memory import db

EARTH_RADIUS_M = 6_371_000


def parse_location(tag: str | None) -> tuple[float, float] | None:
    """Read (lat, lon) from the location tag phones write into videos."""
    match = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", tag or "")

    if not match:
        return None

    return float(match.group(1)), float(match.group(2))


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the distance in metres between two points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    half_dphi = math.radians(lat2 - lat1) / 2
    half_dlambda = math.radians(lon2 - lon1) / 2

    a = math.sin(half_dphi) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_dlambda) ** 2

    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def place_for(lat: float | None, lon: float | None, places: list[tuple]) -> str | None:
    """Return the name of the nearest place whose radius contains the point."""
    if lat is None or lon is None:
        return None

    inside = [
        (distance_m(lat, lon, place_lat, place_lon), name)
        for name, place_lat, place_lon, radius_m in places
        if distance_m(lat, lon, place_lat, place_lon) <= radius_m
    ]

    return min(inside)[1] if inside else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage named places.")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add")
    add.add_argument("name")
    add.add_argument("lat", type=float)
    add.add_argument("lon", type=float)
    add.add_argument("--radius", type=float, default=75, help="metres (default 75)")

    commands.add_parser("list")
    args = parser.parse_args()

    with db.connect() as conn:
        if args.command == "add":
            db.save_place(conn, args.name, args.lat, args.lon, args.radius)
            print(f"Saved {args.name}. Rebuild a session's facts to use it.")

        for name, lat, lon, radius_m in db.places(conn):
            print(f"{name:<20} {lat:>10.5f} {lon:>11.5f}  {radius_m:.0f} m")

    return 0


if __name__ == "__main__":
    sys.exit(main())
