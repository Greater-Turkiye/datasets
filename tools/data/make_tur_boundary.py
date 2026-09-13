#!/usr/bin/env python3
"""Regenerate tools/data/tur_boundary.json from a world-atlas TopoJSON (Natural Earth 1:50m, public domain).

  python tools/data/make_tur_boundary.py path/to/countries-50m.json

Input: world-atlas `countries-50m.json`, e.g. the copy vendored at
Greater-Turkiye/platform apps/web/assets/data/countries-50m.json. Türkiye is the feature with id "792".

TopoJSON shares arcs between neighbouring countries, so an arc of Türkiye that another country also uses is
a land border; every other arc is coastline. Arcs are simplified once (Douglas-Peucker in an equirectangular
km projection, endpoints kept) and reused by every ring, so Türkiye and its neighbours stay consistent.
Tolerances are written to the output; gt.py widens its buffers by them so the simplified outline never
under-flags compared with the Natural Earth line.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

TUR_ID = "792"
KM_PER_DEG = 111.195  # mean Earth radius 6371.0088 km * pi / 180
BUFFER_KM = 12 * 1.852
OUT = Path(__file__).resolve().parent / "tur_boundary.json"
# Sea of Marmara + the Straits: Turkish internal waters. Its centre is > 12 nm from both coasts, so the coast
# buffer alone leaves a hole. Hand-drawn box; everything inside it is Turkish land or Turkish water.
MARMARA = [[26.6, 40.25], [29.95, 40.25], [29.95, 41.05], [26.6, 41.05], [26.6, 40.25]]


def decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x, y = x + dx, y + dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def rings_of(geom: dict):
    parts = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]] if geom["type"] == "Polygon" else []
    for polygon in parts:
        yield from polygon


def simplify(pts: list, tol_km: float) -> list:
    k = math.cos(math.radians(sum(p[1] for p in pts) / len(pts)))
    xy = [(lon * k * KM_PER_DEG, lat * KM_PER_DEG) for lon, lat in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            d = seg_dist(xy[i], xy[a], xy[b])
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > tol_km:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, kept in zip(pts, keep) if kept]


def seg_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def dist_km(p, line) -> float:
    k = math.cos(math.radians(p[1]))
    xy = [((q[0] - p[0]) * k * KM_PER_DEG, (q[1] - p[1]) * KM_PER_DEG) for q in line]
    return min(seg_dist((0, 0), a, b) for a, b in zip(xy, xy[1:]))


def clip(ring: list, box: tuple) -> list:
    """Sutherland-Hodgman clip of a closed ring to (lon0, lat0, lon1, lat1)."""
    x0, y0, x1, y1 = box
    edges = [(lambda p: p[0] >= x0, 0, x0), (lambda p: p[0] <= x1, 0, x1),
             (lambda p: p[1] >= y0, 1, y0), (lambda p: p[1] <= y1, 1, y1)]
    pts = ring[:-1]
    for inside, axis, v in edges:
        out = []
        for i, cur in enumerate(pts):
            prev = pts[i - 1]
            if inside(cur) != inside(prev):
                t = (v - prev[axis]) / (cur[axis] - prev[axis])
                out.append([prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])])
            if inside(cur):
                out.append(cur)
        pts = out
        if not pts:
            return []
    return pts + pts[:1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topojson")
    ap.add_argument("--coast-tolerance-km", type=float, default=1.0)
    ap.add_argument("--border-tolerance-km", type=float, default=0.25)
    args = ap.parse_args()
    topo = json.loads(Path(args.topojson).read_text(encoding="utf-8"))
    geoms = topo["objects"]["countries"]["geometries"]
    tur = next(g for g in geoms if g.get("id") == TUR_ID)
    arcs = decode_arcs(topo)
    tur_arcs = {i if i >= 0 else ~i for ring in rings_of(tur) for i in ring}
    shared = set()
    for g in geoms:
        if g is not tur:
            shared |= {i if i >= 0 else ~i for ring in rings_of(g) for i in ring} & tur_arcs

    rnd = lambda p: [round(p[0], 4), round(p[1], 4)]
    cache: dict[int, list] = {}

    def arc(j: int) -> list:
        if j not in cache:
            tol = args.border_tolerance_km if j in shared else args.coast_tolerance_km
            cache[j] = [rnd(p) for p in simplify(arcs[j], tol)]
        return cache[j]

    def ring_points(ring: list) -> list:
        out: list = []
        for i in ring:
            pts = arc(i) if i >= 0 else arc(~i)[::-1]
            out += pts if not out else pts[1:]
        return out

    polygons = [ring_points(r) for r in rings_of(tur)]
    coast = [arc(j) for j in sorted(tur_arcs - shared)]
    border = [arc(j) for j in sorted(shared)]

    # Other states' land near the Turkish coast: excluded from the 12 nm sea buffer (it is not sea).
    reach = BUFFER_KM + args.coast_tolerance_km + 5
    coast_pts = [p for line in coast for p in line]
    deg = reach / KM_PER_DEG
    zone = (min(p[0] for p in coast_pts) - 2 * deg, min(p[1] for p in coast_pts) - deg,
            max(p[0] for p in coast_pts) + 2 * deg, max(p[1] for p in coast_pts) + deg)
    in_zone = lambda p: zone[0] <= p[0] <= zone[2] and zone[1] <= p[1] <= zone[3]
    foreign = []
    for g in geoms:
        if g is tur:
            continue
        for ring in rings_of(g):
            raw = [p for i in ring for p in (arcs[i] if i >= 0 else arcs[~i][::-1])]
            near = [p for p in raw if in_zone(p) and min(dist_km(p, line) for line in coast) <= reach]
            if not near:
                continue
            pad = 30 / KM_PER_DEG
            box = (min(p[0] for p in near) - pad * 1.4, min(p[1] for p in near) - pad,
                   max(p[0] for p in near) + pad * 1.4, max(p[1] for p in near) + pad)
            pts = clip(ring_points(ring), box)
            if len(pts) >= 4:
                foreign.append({"country": g["properties"]["name"], "ring": [rnd(p) for p in pts]})

    doc = {
        "_comment": "Türkiye geofence data. Generated by tools/data/make_tur_boundary.py; "
                    "see tools/data/README.md for source, licence and method.",
        "source": "Natural Earth 1:50m Admin 0 countries via world-atlas countries-50m.json (Türkiye = feature id 792)",
        "license": "Public domain (Natural Earth, https://www.naturalearthdata.com/about/terms-of-use/)",
        "coast_tolerance_km": args.coast_tolerance_km,
        "border_tolerance_km": args.border_tolerance_km,
        "polygons": polygons,
        "internal_waters": [MARMARA],
        "coast": coast,
        "border": border,
        "foreign_land": foreign,
    }
    lines = [f'  "{k}": {json.dumps(v, ensure_ascii=False)}' for k, v in doc.items() if not isinstance(v, list)]
    for key in ("polygons", "internal_waters", "coast", "border", "foreign_land"):
        rows = ",\n".join("    " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in doc[key])
        lines.append(f'  "{key}": [\n{rows}\n  ]')
    OUT.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")
    n = sum(len(r) for r in polygons)
    m = sum(len(f["ring"]) for f in foreign)
    print(f"{OUT.name}: Türkiye {len(polygons)} rings / {n} points ({len(coast)} coast, {len(border)} border lines); "
          f"foreign land {len(foreign)} rings / {m} points: {sorted({f['country'] for f in foreign})}")


if __name__ == "__main__":
    main()
