#!/usr/bin/env python3
"""Build tools/data/places.json: the city gazetteer the automatic records use to place an event.

    python tools/data/make_places.py path/to/ne_10m_populated_places_simple.geojson [place_names_tr.csv]

Natural Earth 1:10m populated places (public domain), cut to the watch area and to towns large
enough that a name in a headline most likely means them. Places in Türkiye are left out: an event
located there is a question for a person (the Turkish forces gate), not for a gazetteer. The
optional CSV is platform's tools/geo/place_names_tr.csv, which supplies Turkish exonyms.

The output is sorted and rounded, so the same inputs rebuild it byte for byte.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "places.json"
BBOX = (5.0, 5.0, 80.0, 62.0)  # lon0, lat0, lon1, lat1: Balkans and Libya to Central Asia, Horn to Russia
MIN_POP = 30_000
# Names that are also ordinary words, or too short to be safe in a headline.
AMBIGUOUS = {"Mary", "Bar", "Split", "Nice", "Mobile", "Most", "Kos", "Page", "Van", "Hit", "Of", "Ur",
             "Sale", "Kut", "Mina", "Bor", "Tur", "Ras", "Suez Canal", "Salt", "Tours", "Gori",
             # also given names or surnames of people who appear in headlines
             "Serdar", "Kerch Strait", "Abbas", "Hamid", "Rustavi", "Marina", "Alexandria Bay", "Victoria"}
# Transliterations a headline uses that Natural Earth does not carry as `namealt`.
ALIASES = {"Kyiv": ["Kiev"], "Odessa": ["Odesa"], "Zaporizhzhya": ["Zaporizhzhia", "Zaporozhye"],
           "Mykolayiv": ["Mykolaiv", "Nikolaev"], "Dnipro": ["Dnipropetrovsk"], "Kharkiv": ["Kharkov"],
           "Luhansk": ["Lugansk"], "Al Hudaydah": ["Hodeidah", "Hudaydah"], "Sanaa": ["Sana'a", "Sana’a"],
           "Lviv": ["Lvov"], "Chernihiv": ["Chernigov"], "Kramatorsk": [], "Pristina": ["Prishtina", "Priştine"]}


def main() -> int:
    src = Path(sys.argv[1])
    tr = {}
    if len(sys.argv) > 2:
        with open(sys.argv[2], encoding="utf-8") as fh:
            tr = {row["natural_earth"]: row["turkish"] for row in csv.DictReader(fh) if row.get("turkish")}
    feats = json.loads(src.read_text(encoding="utf-8"))["features"]
    rows = []
    for f in feats:
        p = f["properties"]
        lon, lat = round(float(p["longitude"]), 3), round(float(p["latitude"]), 3)
        if not (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
            continue
        if p.get("adm0_a3") == "TUR" or p.get("sov_a3") == "TUR":
            continue
        if (p.get("pop_max") or 0) < MIN_POP and not p.get("adm0cap"):
            continue
        name = p["name"]
        if name in AMBIGUOUS or len(name) < 4:
            continue
        alt = [a.strip() for a in (p.get("namealt") or "").split("|") if a.strip()]
        names = sorted({name, p.get("nameascii") or name, *alt, *ALIASES.get(name, [])} - AMBIGUOUS)
        names = [n for n in names if len(n) >= 4]
        rows.append({"n": name, "names": names, "tr": tr.get(name, name), "a": p["adm0_a3"],
                     "p": int(p.get("pop_max") or 0), "at": [lon, lat]})
    rows.sort(key=lambda r: (-r["p"], r["n"]))
    doc = {"source": "Natural Earth 1:10m populated places v5.1.2 (public domain), https://www.naturalearthdata.com/",
           "bbox": list(BBOX), "min_pop": MIN_POP, "places": rows}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(rows)} places -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
