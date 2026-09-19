#!/usr/bin/env python3
"""What OpenStreetMap has mapped as military on a given island — a lead list, not a finding.

The register (handbook ADR 0019) records what stands on the islands that the 1923 Treaty of
Lausanne and the 1947 Treaty of Paris place under a demilitarised regime. Before anything can be
verified it has to be enumerated, and OpenStreetMap is the only openly licensed inventory of that
kind: `landuse=military` areas, `military=*` features (bases, ranges, bunkers, checkpoints), tagged
by volunteers over two decades and redistributable under the ODbL with attribution.

What this tool prints is therefore a **counting of what somebody has mapped**, not a statement that
those facilities exist as tagged. It is the worklist for verification against primary sources, and
records that quote it say so.

**Greek territory only.** The query is restricted to Greece, because a box around an island reaches
the Turkish coast in several of these straits and a Turkish military feature must never be counted
here, whoever mapped it.

**No coordinates.** The output is counts and names only. A coordinate belongs in a record when it
has been verified against a primary source, not before (ADR 0019 §3), and an unverified one would
discredit a record whose other fields are sound.

    python tools/osm_military.py                  # every island in ISLANDS
    python tools/osm_military.py rhodes kos       # only these
    python tools/osm_military.py --json           # machine-readable summary

Overpass asks callers to identify themselves; USER_AGENT does that honestly. We do not send a
browser user agent to get past a block anywhere — a service that refuses this one stays unused.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "GreaterTurkiye-OSINT/0.1 (+https://github.com/Greater-Turkiye)"
PAUSE_S = 12  # Overpass is a shared free service; a dozen seconds between queries keeps us welcome

# south, west, north, east — generous enough to include an island's offshore islets
ISLANDS: dict[str, tuple[str, tuple[float, float, float, float]]] = {
    "lesbos": ("Midilli / Lesbos", (38.90, 25.80, 39.45, 26.65)),
    "chios": ("Sakız / Chios", (38.10, 25.80, 38.65, 26.25)),
    "samos": ("Sisam / Samos", (37.60, 26.50, 37.90, 27.15)),
    "ikaria": ("Ahikerya / Ikaria", (37.50, 25.95, 37.80, 26.40)),
    "lemnos": ("Limni / Lemnos", (39.75, 24.95, 40.10, 25.70)),
    "samothrace": ("Semadirek / Samothrace", (40.35, 25.40, 40.55, 25.80)),
    "rhodes": ("Rodos / Rhodes", (35.80, 27.60, 36.50, 28.35)),
    "kos": ("İstanköy / Kos", (36.65, 26.85, 36.98, 27.40)),
    "leros": ("Leros", (37.05, 26.75, 37.25, 26.98)),
    "kalymnos": ("Kalimnos / Kalymnos", (36.88, 26.85, 37.08, 27.10)),
}

# Everything is restricted to Greek territory. A bounding box around an island reaches the Turkish
# coast in several of these straits — Ayvalık is 9 km from Lesbos, Bodrum 4 km from Kos — and a
# Turkish feature must never be counted here, whoever mapped it (ADR 0013, ADR 0019 §4.6).
QUERY = """[out:json][timeout:180];
area["ISO3166-1"="GR"][admin_level=2]->.gr;
(
  way["landuse"="military"](area.gr)({bbox});
  relation["landuse"="military"](area.gr)({bbox});
  way["military"](area.gr)({bbox});
  relation["military"](area.gr)({bbox});
  node["military"](area.gr)({bbox});
);
out tags 500;
"""


def fetch(bbox: tuple[float, float, float, float]) -> list[dict]:
    body = QUERY.format(bbox=",".join(str(v) for v in bbox)).encode("utf-8")
    req = urllib.request.Request(OVERPASS, data=body, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as r:  # noqa: S310 - fixed https endpoint
        return json.loads(r.read().decode("utf-8"))["elements"]


def summarise(elements: list[dict]) -> dict:
    kinds: Counter[str] = Counter()
    named: list[dict] = []
    for e in elements:
        tags = e.get("tags", {})
        kind = tags.get("military") or tags.get("landuse") or "unknown"
        kinds[kind] += 1
        name = tags.get("name") or tags.get("name:en") or tags.get("name:el")
        if name:
            named.append({"name": name, "kind": kind, "operator": tags.get("operator")})
    named.sort(key=lambda n: (n["kind"], n["name"]))
    return {"total": len(elements), "by_kind": dict(kinds.most_common()), "named": named}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("islands", nargs="*", help=f"one or more of: {', '.join(ISLANDS)}")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)

    wanted = args.islands or list(ISLANDS)
    unknown = [i for i in wanted if i not in ISLANDS]
    if unknown:
        print(f"unknown island(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    out: dict[str, dict] = {}
    for i, key in enumerate(wanted):
        label, bbox = ISLANDS[key]
        if i:
            time.sleep(PAUSE_S)
        try:
            elements = fetch(bbox)
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            print(f"{label}: query failed ({err})", file=sys.stderr)
            continue
        s = summarise(elements)
        out[key] = {"label": label, **s}
        if not args.json:
            kinds = ", ".join(f"{k} {v}" for k, v in s["by_kind"].items()) or "nothing tagged"
            print(f"{label}: {s['total']} features — {kinds}")
            for n in s["named"]:
                op = f" · {n['operator']}" if n["operator"] else ""
                print(f"    {n['kind']:12} {n['name']}{op}")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
