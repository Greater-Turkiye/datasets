#!/usr/bin/env python3
"""Greater-Turkiye dataset tool.

  python tools/gt.py validate [--base REF]   schema + vocab + reference + policy checks
  python tools/gt.py build                   compile data/ into dist/ (JSONL, CSV, GeoJSON, feeds)
  python tools/gt.py fmt [--check]           canonical key order + "# label" comments on references
  python tools/gt.py new <kind>              create a record skeleton with a fresh ID
  python tools/gt.py id <prefix>             print a fresh ID (evt, act, sit, eqp, src)
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas" / "v1"
VOCAB_DIR = ROOT / "vocab"
DATA_DIRS = ("data", "examples")
MAX_FILE_BYTES = 100_000

KINDS = {  # schema kind -> (id prefix, directory)
    "event": ("evt", "events"),
    "actor": ("act", "actors"),
    "site": ("sit", "sites"),
    "equipment": ("eqp", "equipment"),
    "source": ("src", "sources"),
}
DIR_OF_PREFIX = {prefix: directory for prefix, directory in KINDS.values()}
ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
ID_RE = re.compile(r"^(evt|act|sit|eqp|src)_([0-7][0-9a-hjkmnp-tv-z]{25})$")
TEXT_FIELDS = ("title", "summary", "description", "public_role")
TUR_BOUNDARY = ROOT / "tools" / "data" / "tur_boundary.json"
GEOFENCE_MSG = ("location/geometry is inside Türkiye's land territory or within 12 nm of its coast; "
                "remove the geometry and use a coarse location precision")


# --- YAML: no implicit timestamps, only true/false are booleans (country/lang "no" stays a string)

class Loader(yaml.SafeLoader):
    pass


Loader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in resolvers if tag not in ("tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool")]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))


# --- IDs: TypeID-style, UUIDv7 encoded as 26 Crockford base32 chars

def _uuid7() -> int:
    ms = time.time_ns() // 1_000_000
    rand = secrets.randbits(74)
    return (ms << 80) | (0x7 << 76) | ((rand >> 62) << 64) | (0b10 << 62) | (rand & ((1 << 62) - 1))


def new_id(prefix: str) -> str:
    n, chars = _uuid7(), []
    for _ in range(26):
        chars.append(ALPHABET[n & 31])
        n >>= 5
    return f"{prefix}_{''.join(reversed(chars))}"


def id_time(record_id: str) -> datetime:
    n = 0
    for c in record_id.split("_", 1)[1]:
        n = n * 32 + ALPHABET.index(c)
    return datetime.fromtimestamp((n >> 80) / 1000, tz=timezone.utc)


def expected_path(record_id: str, base: str = "data") -> Path:
    t = id_time(record_id)
    return Path(base) / DIR_OF_PREFIX[record_id.split("_", 1)[0]] / f"{t:%Y}" / f"{t:%m}" / f"{record_id}.yaml"


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- Türkiye geofence: land territory + 12 nm of sea (Natural Earth 1:50m, see tools/data/README.md)

TERRITORIAL_SEA_KM = 12 * 1.852  # 12 nautical miles = 22.224 km
KM_PER_DEG = 111.195  # mean Earth radius 6371.0088 km * pi / 180
DENSIFY_KM = 5.0


class Geofence:
    """Is a point on Türkiye's land, in its internal waters, or in the sea within 12 nm of its coast?

    In order: inside a Turkish land ring or the Marmara box -> in; within `border_tolerance_km` of a land
    border -> in; on another state's land -> out (the 12 nm buffer is sea, it does not reach into Greece,
    Georgia...); within 12 nm + `coast_tolerance_km` of the Turkish coast -> in. The tolerances are the
    outline's maximum simplification error, so the check never under-flags compared with Natural Earth.
    Distances are approximate: a local equirectangular projection centred on the tested point
    (x = dlon * cos(lat) * 111.195 km, y = dlat * 111.195 km), far below 1% off great-circle over ~25 km.
    """

    def __init__(self, path: Path) -> None:
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.coast_km = TERRITORIAL_SEA_KM + float(doc["coast_tolerance_km"])
        self.border_km = float(doc["border_tolerance_km"])
        self.rings = doc["polygons"] + doc["internal_waters"]
        self.coast, self.border = doc["coast"], doc["border"]
        self.foreign = [f["ring"] for f in doc["foreign_land"]]
        pts = [p for ring in self.rings for p in ring]
        lat_pad = self.coast_km / KM_PER_DEG
        lat_min, lat_max = min(p[1] for p in pts) - lat_pad, max(p[1] for p in pts) + lat_pad
        lon_pad = lat_pad / math.cos(math.radians(lat_max))
        self.bbox = (min(p[0] for p in pts) - lon_pad, lat_min, max(p[0] for p in pts) + lon_pad, lat_max)

    def contains(self, lon: float, lat: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        if any(_in_ring(lon, lat, ring) for ring in self.rings):
            return True
        if any(self.distance_km(lon, lat, line) <= self.border_km for line in self.border):
            return True
        if any(_in_ring(lon, lat, ring) for ring in self.foreign):
            return False
        return any(self.distance_km(lon, lat, line) <= self.coast_km for line in self.coast)

    @staticmethod
    def distance_km(lon: float, lat: float, line: list) -> float:
        k = math.cos(math.radians(lat)) * KM_PER_DEG
        xy = [((p[0] - lon) * k, (p[1] - lat) * KM_PER_DEG) for p in line]
        return min(_origin_seg_dist(a, b) for a, b in zip(xy, xy[1:]))

    def intersects(self, geometry: dict) -> bool:
        """Point: the point. Polygon: every vertex, points every 5 km along each edge, and whether it encloses Türkiye."""
        if geometry.get("type") == "Point":
            return self.contains(*geometry["coordinates"][:2])
        if geometry.get("type") != "Polygon":
            return False
        rings = geometry["coordinates"]
        for ring in rings:
            for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
                km = math.hypot((bx - ax) * math.cos(math.radians((ay + by) / 2)), by - ay) * KM_PER_DEG
                steps = max(1, math.ceil(km / DENSIFY_KM))
                if any(self.contains(ax + (bx - ax) * i / steps, ay + (by - ay) * i / steps) for i in range(steps)):
                    return True
        # a polygon drawn around Türkiye (edges entirely outside the buffer) still covers it
        return any(sum(_in_ring(r[0][0], r[0][1], ring) for ring in rings) % 2 for r in self.rings)


def _in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        if (ay > lat) != (by > lat) and lon < ax + (lat - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def _origin_seg_dist(a, b) -> float:
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


# --- reporting

class Report:
    def __init__(self) -> None:
        self.errors = 0
        self.warnings = 0

    def _emit(self, level: str, where: Path | str, msg: str) -> None:
        rel = where.relative_to(ROOT).as_posix() if isinstance(where, Path) and where.is_absolute() else str(where)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::{level} file={rel}::{msg}")
        else:
            print(f"{level.upper():8}{rel}: {msg}")

    def error(self, where: Path | str, msg: str) -> None:
        self.errors += 1
        self._emit("error", where, msg)

    def warn(self, where: Path | str, msg: str) -> None:
        self.warnings += 1
        self._emit("warning", where, msg)


@dataclass
class Record:
    path: Path
    base: str
    data: dict

    @property
    def kind(self) -> str:
        return self.data["schema"].split("/")[0]


# --- loading

def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)


def load_schemas() -> dict[str, dict]:
    return {f.name.removesuffix(".schema.json"): json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(SCHEMA_DIR.glob("*.schema.json"))}


def load_validators(report: Report) -> dict[str, Draft202012Validator]:
    schemas = load_schemas()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    registry = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in schemas.values()])
    return {name: Draft202012Validator(s, registry=registry) for name, s in schemas.items() if not name.startswith("_")}


def load_vocab(report: Report) -> dict[str, dict[str, dict]]:
    vocab = {}
    for f in sorted(VOCAB_DIR.glob("*.yaml")):
        doc = load_yaml(f)
        codes = {}
        for entry in doc.get("codes", []):
            code = entry.get("code")
            if not isinstance(code, str) or not code:
                report.error(f, f"entry without code: {entry}")
                continue
            if code in codes:
                report.error(f, f"duplicate code {code}")
            label = entry.get("label") or {}
            if not label.get("tr") or not label.get("en"):
                report.error(f, f"{code}: label.tr and label.en are required")
            if entry.get("status", "active") not in ("active", "deprecated"):
                report.error(f, f"{code}: status must be active or deprecated")
            codes[code] = entry
        for code, entry in codes.items():
            if entry.get("replaced_by") and entry["replaced_by"] not in codes:
                report.error(f, f"{code}: replaced_by {entry['replaced_by']} does not exist")
        vocab[f.stem] = codes
    return vocab


def load_records(report: Report) -> dict[str, Record]:
    records: dict[str, Record] = {}
    for base in DATA_DIRS:
        root = ROOT / base
        if not root.exists():
            continue
        for f in sorted(root.rglob("*")):
            if f.is_dir() or f.name in (".gitkeep", "README.md"):
                continue
            if f.suffix != ".yaml":
                report.error(f, "only .yaml files are allowed here")
                continue
            if f.stat().st_size > MAX_FILE_BYTES:
                report.error(f, f"file larger than {MAX_FILE_BYTES} bytes")
                continue
            try:
                data = load_yaml(f)
            except yaml.YAMLError as e:
                report.error(f, f"YAML parse error: {e}")
                continue
            if not isinstance(data, dict):
                report.error(f, "record must be a YAML mapping")
                continue
            rid = data.get("id")
            if not isinstance(rid, str) or not ID_RE.match(rid):
                report.error(f, f"invalid id {rid!r}; generate one with `python tools/gt.py new <kind>`")
                continue
            if rid in records:
                report.error(f, f"duplicate id, also in {records[rid].path.relative_to(ROOT).as_posix()}")
                continue
            records[rid] = Record(f, base, data)
    return records


# --- checks

class Checker:
    def __init__(self, report, validators, vocab, policy, records):
        self.report, self.validators, self.vocab, self.policy, self.records = report, validators, vocab, policy, records
        self.pii = {name: re.compile(rx) for name, rx in policy["pii_patterns"].items()}
        self.markers = [re.compile(rf"(?<!\w){re.escape(m)}(?!\w)") for m in policy["classification_markers"]]
        self.now = datetime.now(timezone.utc)
        self.geofence = Geofence(TUR_BOUNDARY)

    def in_geofence(self, rec) -> bool:
        geometry = (rec.data.get("location") or {}).get("geometry")
        return bool(geometry) and self.geofence.intersects(geometry)

    def run(self) -> None:
        for rid, rec in self.records.items():
            self.check(rid, rec)

    def check(self, rid: str, rec: Record) -> None:
        err = lambda msg: self.report.error(rec.path, msg)
        name, _, version = str(rec.data.get("schema", "")).partition("/")
        if version != "1" or name not in self.validators:
            err(f"unknown schema {rec.data.get('schema')!r}")
            return
        prefix = rid.split("_", 1)[0]
        if name != "tombstone" and KINDS[name][0] != prefix:
            err(f"id prefix {prefix}_ does not match schema {name}")
        expected = expected_path(rid, rec.base)
        if rec.path.relative_to(ROOT) != expected:
            err(f"file must be at {expected.as_posix()} (path is derived from the ID)")
        schema_errors = sorted(self.validators[name].iter_errors(rec.data), key=lambda e: list(map(str, e.absolute_path)))
        for e in schema_errors:
            err(f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}")
        self.check_policy_text(rec, rec.data)
        if schema_errors or name == "tombstone":
            return
        getattr(self, f"check_{name}")(rec)
        self.check_citations(rec)
        self.check_i18n(rec)
        self.check_coordinates(rec, rec.data)

    # vocab / references

    def code(self, rec, vocab_name, value, where):
        entry = self.vocab[vocab_name].get(value)
        if entry is None:
            self.report.error(rec.path, f"{where}: unknown code {value!r} (see vocab/{vocab_name}.yaml)")
        elif entry.get("status") == "deprecated":
            self.report.warn(rec.path, f"{where}: {value!r} is deprecated, use {entry.get('replaced_by')!r}")

    def ref(self, rec, value, where):
        target = self.records.get(value)
        if target is None:
            self.report.error(rec.path, f"{where}: {value} does not exist")
        elif target.kind == "tombstone":
            self.report.error(rec.path, f"{where}: {value} is withdrawn")
        elif rec.base == "data" and target.base == "examples":
            self.report.error(rec.path, f"{where}: data/ must not reference examples/")

    def check_event(self, rec):
        d = rec.data
        self.code(rec, "event-types", d["event_type"], "event_type")
        for r in d["regions"]:
            self.code(rec, "regions", r, "regions")
        for c in d.get("countries", []):
            self.code(rec, "countries", c, "countries")
        for i, a in enumerate(d.get("actors", [])):
            self.ref(rec, a["ref"], f"actors/{i}")
        for i, e in enumerate(d.get("equipment", [])):
            self.ref(rec, e["ref"], f"equipment/{i}")
        for i, s in enumerate(d.get("sites", [])):
            self.ref(rec, s, f"sites/{i}")
        for i, c in enumerate(d.get("claims", [])):
            self.ref(rec, c["by"], f"claims/{i}/by")
            self.code(rec, "characterizations", c["characterization"], f"claims/{i}/characterization")
            if c["source"] >= len(d["sources"]):
                self.report.error(rec.path, f"claims/{i}/source: index {c['source']} out of range")
        t = d["time"]
        if "end" in t and parse_dt(t["end"]) < parse_dt(t["start"]):
            self.report.error(rec.path, "time/end is before time/start")
        if parse_dt(t["start"]) > self.now + timedelta(days=1) and not d["event_type"].startswith(("exercise.", "maritime.navtex")):
            self.report.error(rec.path, "time/start is in the future")
        self.check_tur_gate(rec)
        self.check_verified(rec)

    def check_actor(self, rec):
        d = rec.data
        self.code(rec, "actor-kinds", d["kind"], "kind")
        if "country" in d:
            self.code(rec, "countries", d["country"], "country")
        if "parent" in d:
            self.ref(rec, d["parent"], "parent")
        if d["kind"] == "person" and not (d.get("public_role") and d.get("wikidata")):
            self.report.error(rec.path, "persons are allowed only with public_role and wikidata (public officials acting publicly)")
        for i, des in enumerate(d.get("designations", [])):
            if len(des["by"]) == 3:
                self.code(rec, "countries", des["by"], f"designations/{i}/by")

    def check_site(self, rec):
        d = rec.data
        self.code(rec, "site-types", d["site_type"], "site_type")
        if "country" in d:
            self.code(rec, "countries", d["country"], "country")
        if d.get("country") == "TUR":
            self.report.error(rec.path, "sites operated by Türkiye are out of scope (red line)")
        if self.in_geofence(rec):
            self.report.error(rec.path, f"[Türk kuvvetleri kapısı / TUR forces gate] {GEOFENCE_MSG}")
        for i, a in enumerate(d.get("operators", [])):
            self.ref(rec, a, f"operators/{i}")
        for r in d.get("regions", []):
            self.code(rec, "regions", r, "regions")

    def check_equipment(self, rec):
        d = rec.data
        self.code(rec, "equipment-categories", d["category"], "category")
        if "origin_country" in d:
            self.code(rec, "countries", d["origin_country"], "origin_country")
        for f in ("manufacturer", "variant_of"):
            if f in d:
                self.ref(rec, d[f], f)

    def check_source(self, rec):
        d = rec.data
        self.code(rec, "source-types", d["source_type"], "source_type")
        if "country" in d:
            self.code(rec, "countries", d["country"], "country")
        if "operator" in d:
            self.ref(rec, d["operator"], "operator")
        self.check_url(rec, d["url"], "url")

    def check_url(self, rec, url, where):
        host = (urlparse(url).hostname or "").lower()
        for blocked in self.policy["blocked_domains"]:
            if host == blocked or host.endswith("." + blocked):
                self.report.error(rec.path, f"{where}: domain {host} is not allowed (shortener/leak site)")
        if re.search(r"[?&](utm_[a-z]+|fbclid|gclid|igshid|si)=", url):
            self.report.warn(rec.path, f"{where}: remove tracking parameters from URL")

    def check_citations(self, rec):
        cites = rec.data.get("sources", [])
        restricted = 0
        for i, c in enumerate(cites):
            self.check_url(rec, c["url"], f"sources/{i}/url")
            if "ref" in c:
                self.ref(rec, c["ref"], f"sources/{i}/ref")
                src = self.records.get(c["ref"])
                if src and src.kind == "source" and src.data["terms"] in ("restricted", "no-redistribution"):
                    restricted += 1
        if cites and restricted == len(cites):
            self.report.error(rec.path, "restricted/no-redistribution sources can only be leads; add an open source")

    # policy gates

    def check_tur_gate(self, rec):
        d, gate = rec.data, self.policy["tur_forces_gate"]
        gated = False
        for a in d.get("actors", []):
            actor = self.records.get(a["ref"])
            if actor and actor.kind == "actor" and actor.data.get("country") == "TUR" \
                    and actor.data["kind"] in gate["military_actor_kinds"] and a["role"] in gate["gated_roles"]:
                gated = True
        flags = d.get("policy", {})
        fenced = self.in_geofence(rec)
        if not (gated or fenced or flags.get("involves_tur_forces")):
            return
        err = lambda msg: self.report.error(rec.path, f"[Türk kuvvetleri kapısı / TUR forces gate] {msg}")
        if (gated or flags.get("involves_tur_forces")) and \
                (not flags.get("involves_tur_forces") or flags.get("sensitivity") != "elevated"):
            err("set policy.involves_tur_forces: true and policy.sensitivity: elevated")
        loc = d.get("location")
        if loc:
            if fenced:
                err(GEOFENCE_MSG)
            elif "geometry" in loc:
                err("records involving Turkish forces must not contain coordinates")
            if loc["precision"] not in gate["allowed_precisions"]:
                err(f"location precision must be one of {gate['allowed_precisions']}")
        if self.now - parse_dt(d["time"]["start"]) < timedelta(hours=gate["min_delay_hours"]):
            err(f"event must be at least {gate['min_delay_hours']} hours old")
        self.report.warn(rec.path, "needs the `policy:approved` label from a maintainer before merge")

    def check_verified(self, rec):
        d, rules = rec.data, self.policy["verified"]
        a = d["assessment"]
        if a["status"] != "verified":
            return
        err = lambda msg: self.report.error(rec.path, f"[verified] {msg}")
        if a["credibility"] > rules["max_credibility"]:
            err(f"credibility must be <= {rules['max_credibility']}")
        for field in ("title", "summary"):
            if field in d and "en" not in d[field]:
                err(f"{field}.en is required")
        for i, c in enumerate(d["sources"]):
            if not c.get("archives"):
                err(f"sources/{i} needs at least one archive")
        publishers = {c.get("ref") or urlparse(c["url"]).hostname for c in d["sources"]}
        strong = set(a.get("method", [])) & set(rules["strong_methods"])
        if len(publishers) < rules["min_independent_sources"] and not strong:
            err(f"needs {rules['min_independent_sources']} independent sources or one of {rules['strong_methods']}")

    def check_policy_text(self, rec, node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).lower() in self.policy["forbidden_keys"]:
                    self.report.error(rec.path, f"{path}{k}: personal-data field is not allowed")
                self.check_policy_text(rec, v, f"{path}{k}/")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                self.check_policy_text(rec, v, f"{path}{i}/")
        elif isinstance(node, str):
            if not node.startswith(("http://", "https://")):
                for name, rx in self.pii.items():
                    if rx.search(node):
                        self.report.error(rec.path, f"{path.rstrip('/')}: looks like personal data ({name})")
            for rx in self.markers:
                if rx.search(node):
                    self.report.error(rec.path, f"{path.rstrip('/')}: classification marking {rx.pattern!r} — classified material is a red line")

    def check_i18n(self, rec):
        for field in TEXT_FIELDS + ("name",):
            value = rec.data.get(field)
            if isinstance(value, dict) and "en" not in value:
                self.report.warn(rec.path, f"{field}.en missing (required before publication)")

    def check_coordinates(self, rec, node):
        if isinstance(node, dict):
            if node.get("type") in ("Point", "Polygon") and "coordinates" in node:
                for x in _flatten(node["coordinates"]):
                    if abs(round(x, 5) - x) > 1e-12:
                        self.report.error(rec.path, f"coordinate {x} has more than 5 decimals")
                        return
            for v in node.values():
                self.check_coordinates(rec, v)
        elif isinstance(node, list):
            for v in node:
                self.check_coordinates(rec, v)


def _flatten(value):
    if isinstance(value, list):
        for v in value:
            yield from _flatten(v)
    elif isinstance(value, (int, float)):
        yield value


def check_git(base: str, report: Report) -> None:
    try:
        out = subprocess.run(["git", "diff", "--name-status", "-M", f"{base}...HEAD", "--", *DATA_DIRS],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        report.error("git", f"cannot diff against {base}: {e}")
        return
    for line in out.splitlines():
        parts = line.split("\t")
        if parts[0].startswith("D"):
            report.error(parts[1], "records are never deleted; replace the content with a tombstone/1 record")
        elif parts[0].startswith("R"):
            report.error(parts[1], f"records are never moved or renamed (-> {parts[2]})")


def validate_all(report: Report, base: str | None = None) -> dict[str, Record]:
    validators = load_validators(report)
    vocab = load_vocab(report)
    policy = load_yaml(ROOT / "policy.yaml")
    records = load_records(report)
    Checker(report, validators, vocab, policy, records).run()
    if base:
        check_git(base, report)
    return records


# --- fmt: canonical key order (the order of `properties` in the schema) + "# English label" after references

FMT_WIDTH = 120  # a mapping/list goes on one line in flow style only if the line (without comment) fits
BLOCK_ITEM_KEYS = ("sources", "corrections")  # items of these lists are always block mappings
REF_RE = re.compile(r"^(act|sit|eqp|src)_[0-7][0-9a-hjkmnp-tv-z]{25}$")


class Formatter:
    """Emits the constrained YAML of this repo deterministically.

    Top-level mappings (title, time, location...) are block style; deeper mappings and list items are flow
    style when they fit in FMT_WIDTH (except items of sources/corrections); scalar lists are flow when they fit;
    `coordinates` is always flow. Strings are plain when that reads back identically (with gt.py's loader and
    with a standard YAML 1.1 loader, apart from timestamps), otherwise double-quoted. Only the leading comment
    header of a file is kept; reference comments are regenerated, any other comment is dropped.
    """

    def __init__(self, schemas: dict[str, dict], labels: dict[str, str]) -> None:
        self.schemas = schemas
        self.by_id = {s["$id"]: s for s in schemas.values()}
        self.labels = labels

    def format(self, text: str, data: dict, kind: str) -> str:
        header = []
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                break
            header.append(line.rstrip())
        while header and not header[-1]:
            header.pop()
        while header and not header[0]:
            header.pop(0)
        lines: list[tuple[str, list[str]]] = []
        schema = self.schemas[kind]
        self.emit_map(self.order(data, schema, schema), 0, 0, lines)
        body = [t + (f"  # {'; '.join(refs)}" if refs else "") for t, refs in lines]
        out = "\n".join(header + body) + "\n"
        if yaml.load(out, Loader=Loader) != data:
            raise ValueError("formatted output does not read back identically")
        return out

    # key order

    def resolve(self, node: dict, doc: dict) -> tuple[dict, dict]:
        while "$ref" in node:
            base, _, frag = node["$ref"].partition("#")
            doc = self.by_id[base] if base else doc
            node = doc
            for part in filter(None, frag.split("/")):
                node = node[part]
        return node, doc

    def order(self, value, node: dict, doc: dict):
        node, doc = self.resolve(node, doc)
        if isinstance(value, dict):
            props: dict[str, tuple[dict, dict]] = {}
            for branch in [node] + node.get("oneOf", []) + node.get("anyOf", []):
                branch, bdoc = self.resolve(branch, doc)
                for k, sub in branch.get("properties", {}).items():
                    props.setdefault(k, (sub, bdoc))
            keys = [k for k in props if k in value] + [k for k in value if k not in props]
            return {k: self.order(value[k], *props.get(k, ({}, doc))) for k in keys}
        if isinstance(value, list):
            return [self.order(v, node.get("items", {}), doc) for v in value]
        return value

    # emitting

    def emit_map(self, d: dict, indent: int, depth: int, lines: list) -> None:
        for k, v in d.items():
            head = f"{' ' * indent}{self.scalar(k)}:"
            if isinstance(v, dict) and v:
                one = f"{head} {self.flow(v)}"
                if depth > 0 and len(one) <= FMT_WIDTH:
                    lines.append((one, self.refs(v)))
                else:
                    lines.append((head, []))
                    self.emit_map(v, indent + 2, depth + 1, lines)
            elif isinstance(v, list) and v:
                one = f"{head} {self.flow(v)}"
                scalars = not any(isinstance(x, (dict, list)) for x in v)
                if k == "coordinates" or (scalars and len(one) <= FMT_WIDTH):
                    lines.append((one, self.refs(v)))
                else:
                    lines.append((head, []))
                    for item in v:
                        self.emit_item(item, indent + 2, depth + 1, lines, block=k in BLOCK_ITEM_KEYS)
            else:
                value = self.flow(v) if isinstance(v, (dict, list)) else self.scalar(v)
                lines.append((f"{head} {value}", [] if depth == 0 and k == "id" else self.refs(v)))

    def emit_item(self, item, indent: int, depth: int, lines: list, block: bool) -> None:
        pad = " " * indent
        if isinstance(item, dict) and item:
            one = f"{pad}- {self.flow(item)}"
            if not block and len(one) <= FMT_WIDTH:
                lines.append((one, self.refs(item)))
                return
            sub: list = []
            self.emit_map(item, indent + 2, depth + 1, sub)
            sub[0] = (f"{pad}- {sub[0][0][indent + 2:]}", sub[0][1])
            lines.extend(sub)
        else:
            value = self.flow(item) if isinstance(item, (dict, list)) else self.scalar(item)
            lines.append((f"{pad}- {value}", self.refs(item)))

    def flow(self, v) -> str:
        if isinstance(v, dict):
            return "{" + ", ".join(f"{self.scalar(k, True)}: {self.flow(x)}" for k, x in v.items()) + "}"
        if isinstance(v, list):
            return "[" + ", ".join(self.flow(x) for x in v) + "]"
        return self.scalar(v, True)

    def refs(self, v) -> list[str]:
        found: list[str] = []
        if isinstance(v, dict):
            v = list(v.values())
        if isinstance(v, list):
            for x in v:
                found += [r for r in self.refs(x) if r not in found]
        elif isinstance(v, str) and REF_RE.match(v) and self.labels.get(v):
            found.append(self.labels[v])
        return found

    @staticmethod
    def scalar(v, flow: bool = False) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            r = repr(v)
            return r if "e" not in r and "n" not in r else f"{v:.10f}".rstrip("0")
        s = str(v)
        return s if _plain_ok(s, flow) else json.dumps(s, ensure_ascii=False)


@lru_cache(maxsize=None)
def _plain_ok(s: str, flow: bool) -> bool:
    if not s or s != s.strip() or any(c in s for c in "\n\r\t") or (flow and any(c in s for c in ",[]{}")):
        return False
    docs = [(f"k: {s}", {"k": s})] + ([(f"[{s}]", [s]), (f"{{k: {s}}}", {"k": s})] if flow else [])
    try:
        if any(yaml.load(src, Loader=Loader) != want for src, want in docs):
            return False
        std = yaml.safe_load(f"k: {s}")["k"]
    except yaml.YAMLError:
        return False
    return std == s or isinstance(std, (date, datetime))


def record_label(rec: Record) -> str | None:
    name = rec.data.get("name")
    return (name.get("en") or name.get("tr")) if isinstance(name, dict) else None


def cmd_fmt(args) -> int:
    report = Report()
    records = load_records(report)
    formatter = Formatter(load_schemas(), {rid: record_label(rec) for rid, rec in records.items()})
    changed = 0
    for rid, rec in sorted(records.items(), key=lambda item: item[1].path):
        kind = str(rec.data.get("schema", "")).partition("/")[0]
        if kind.startswith("_") or kind not in formatter.schemas:
            report.error(rec.path, f"unknown schema {rec.data.get('schema')!r}; not formatted")
            continue
        text = rec.path.read_text(encoding="utf-8")
        try:
            new = formatter.format(text, rec.data, kind)
        except (ValueError, KeyError) as e:
            report.error(rec.path, f"cannot format: {e}")
            continue
        if new != text:
            changed += 1
            if args.check:
                report.error(rec.path, "not formatted; run `python tools/gt.py fmt`")
            else:
                rec.path.write_text(new, encoding="utf-8", newline="\n")
                print(f"formatted {rec.path.relative_to(ROOT).as_posix()}")
    verb = "would be reformatted" if args.check else "reformatted"
    print(f"{len(records)} records, {changed} {verb}, {report.errors} errors")
    return 1 if report.errors else 0


# --- feed: a public, account-free feed of the records the project stands behind
#
# Only event records under data/ whose assessment.status is `verified` or `partially_verified` are
# published.  examples/ is fictional and never reaches the feed, and neither do unverified, disputed,
# false or withdrawn records.  Every item repeats its verification status, its Admiralty credibility,
# the assessment note and every source with its archive, so no item can be read as an unattributed
# fact.  All times are derived from the record (`reported_at`, else `time.start`; `corrections[].date`
# for the update time) and never from the clock, so rebuilding unchanged data is byte-identical.

FEED_STATUSES = ("verified", "partially_verified")
FEED_SITE = "https://greater-turkiye.github.io/datasets/"
REPO_URL = "https://github.com/Greater-Turkiye/datasets"
FEED_TITLE = "Greater Türkiye — doğrulanmış kayıtlar / verified records"
FEED_DESCRIPTION = (
    "Türkiye'nin çevresindeki askerî ve güvenlik gelişmelerine dair doğrulanmış ve kısmen doğrulanmış "
    "açık kaynaklı kayıtlar. Her madde doğrulama durumunu ve kaynaklarını taşır. / Verified and "
    "partially verified open-source records of military and security developments in Türkiye's "
    "neighbourhood. Every item carries its verification status and its sources."
)
FEED_RIGHTS = ("Veriler CC BY 4.0 ile yayımlanır. / Data is published under CC BY 4.0.")
STATUS_LABEL = {"verified": ("DOĞRULANMIŞ", "VERIFIED"),
                "partially_verified": ("KISMEN DOĞRULANMIŞ", "PARTIALLY VERIFIED")}
CREDIBILITY_NOTE = ("1 teyitli … 6 değerlendirilemez / 1 confirmed … 6 cannot be judged")
ATTRIBUTION_NOTE = (
    "Kayıtlar, listelenen kaynaklara atfedilen bilgilerden derlenmiştir; tartışmalı nitelendirmeler "
    "kaynaklarına aittir ve projenin kendi sesiyle ileri sürülmez. / Records are compiled from "
    "information attributed to the sources listed; contested characterisations belong to their sources "
    "and are never asserted in the project's own voice."
)
DIGEST_DAYS = 7
DIGEST_SUMMARY_CHARS = 400


def _feed_dt(value: str) -> datetime:
    """A record date or timestamp as an aware UTC datetime (`2026-08-09` counts as midnight UTC)."""
    t = parse_dt(value if len(value) > 10 else f"{value}T00:00:00+00:00")
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _esc(value, attr: bool = False) -> str:
    """XML/HTML escaping that leaves apostrophes alone; attribute values are double-quoted."""
    out = html.escape(str(value), quote=False)
    return out.replace('"', "&quot;") if attr else out


def _iso_z(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(field, lang: str) -> str:
    """A bilingual field in the requested language, falling back to the other one."""
    field = field if isinstance(field, dict) else {}
    for key in (lang, "en", "tr"):
        if field.get(key):
            return str(field[key])
    return ""


def _clip(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.—-")
    return f"{cut}…"


def feed_entries(records: dict[str, Record], region_labels: dict[str, dict]) -> list[dict]:
    """The published records, newest first; ties broken by ID so the order never depends on the clock."""
    entries: list[dict] = []
    for rid, rec in records.items():
        if rec.base != "data" or rec.kind != "event":
            continue  # examples/ is fictional, other kinds are the registry behind the events
        e = rec.data
        status = e["assessment"]["status"]
        if status not in FEED_STATUSES:
            continue
        published = _feed_dt(e.get("reported_at") or e["time"]["start"])
        updated = max([published] + [_feed_dt(c["date"]) for c in e.get("corrections", [])])
        sources = []
        for c in e["sources"]:
            publisher = record_label(records[c["ref"]]) if c.get("ref") in records else None
            sources.append({"publisher": publisher or (urlparse(c["url"]).hostname or c["url"]),
                            "title": c.get("title") or c["url"], "url": c["url"],
                            "archives": [a["url"] for a in c.get("archives", [])]})
        entries.append({
            "id": rid, "record": e, "status": status,
            "path": rec.path.relative_to(ROOT).as_posix(),
            "url": f"{REPO_URL}/blob/main/{rec.path.relative_to(ROOT).as_posix()}",
            "published": published, "updated": updated, "sources": sources,
            "regions": [{"code": r, "tr": _text(region_labels.get(r), "tr") or r,
                         "en": _text(region_labels.get(r), "en") or r} for r in e["regions"]],
        })
    entries.sort(key=lambda it: (it["published"], it["id"]), reverse=True)
    return entries


def entry_title(entry: dict) -> str:
    tr, en = STATUS_LABEL[entry["status"]]
    return f"[{tr} / {en}] {_text(entry['record'].get('title'), 'en')}"


def entry_html(entry: dict) -> str:
    """The item body: status first, then both summaries, the caveats and every source with its archive."""
    e, esc = entry["record"], _esc
    a = e["assessment"]
    tr, en = STATUS_LABEL[entry["status"]]
    t = e["time"]
    when = t["start"] + (f" – {t['end']}" if t.get("end") else "")
    parts = [
        f"<p><strong>[{esc(tr)} / {esc(en)}]</strong> — Admiralty {a['credibility']}/6 "
        f"({esc(CREDIBILITY_NOTE)})"
        + (f" · {esc(', '.join(a['method']))}" if a.get("method") else "") + "</p>",
        f"<p lang=\"tr\">{esc(_text(e.get('summary'), 'tr'))}</p>",
        f"<p lang=\"en\">{esc(_text(e.get('summary'), 'en'))}</p>",
        "<p>Bölge / Region: " + esc(", ".join(f"{r['tr']} / {r['en']}" for r in entry["regions"]))
        + (" · Ülkeler / Countries: " + esc(", ".join(e["countries"])) if e.get("countries") else "")
        + f" · Zaman / Time: {esc(when)} (UTC, {esc(t['precision'])}, {esc(t['basis'])})</p>",
    ]
    for lang in ("tr", "en"):
        if _text(a.get("note"), lang):
            parts.append(f"<p lang=\"{lang}\">Değerlendirme notu / Assessment note: "
                         f"{esc(_text(a.get('note'), lang))}</p>")
    for c in e.get("corrections", []):
        parts.append(f"<p>Düzeltme / Correction {esc(c['date'])}: {esc(_text(c.get('note'), 'en'))}</p>")
    parts.append(f"<p>{esc(ATTRIBUTION_NOTE)}</p><p>Kaynaklar / Sources:</p><ul>")
    for s in entry["sources"]:
        archives = "".join(f" — <a href=\"{esc(u, attr=True)}\">arşiv / archive</a>" for u in s["archives"])
        parts.append(f"<li>{esc(s['publisher'])}: <a href=\"{esc(s['url'], attr=True)}\">{esc(s['title'])}</a>{archives}</li>")
    parts.append("</ul>")
    parts.append(f"<p>Kayıt / Record: <a href=\"{esc(entry['url'], attr=True)}\">{esc(entry['id'])}</a> · {esc(FEED_RIGHTS)}</p>")
    return "".join(parts)


def render_rss(entries: list[dict]) -> str:
    esc = _esc
    updated = max((it["updated"] for it in entries), default=None)
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', "  <channel>",
             f"    <title>{esc(FEED_TITLE)}</title>", f"    <link>{esc(FEED_SITE)}</link>",
             f"    <description>{esc(FEED_DESCRIPTION)}</description>",
             "    <language>tr</language>", f"    <copyright>{esc(FEED_RIGHTS)}</copyright>",
             "    <docs>https://www.rssboard.org/rss-specification</docs>",
             f'    <atom:link href="{esc(FEED_SITE, attr=True)}feed.xml" rel="self" type="application/rss+xml"/>']
    if updated:
        lines.append(f"    <lastBuildDate>{format_datetime(updated)}</lastBuildDate>")
    for it in entries:
        lines += ["    <item>", f"      <title>{esc(entry_title(it))}</title>",
                  f"      <link>{esc(it['url'])}</link>",
                  f'      <guid isPermaLink="false">urn:gt:record:{it["id"]}</guid>',
                  f"      <pubDate>{format_datetime(it['published'])}</pubDate>",
                  f"      <category>status:{it['status']}</category>"]
        lines += [f"      <category>region:{esc(r['code'])}</category>" for r in it["regions"]]
        lines += [f"      <description>{esc(entry_html(it))}</description>", "    </item>"]
    lines += ["  </channel>", "</rss>", ""]
    return "\n".join(lines)


def render_json_feed(entries: list[dict]) -> str:
    """JSON Feed 1.1; `_gt` carries the verification status in machine-readable form."""
    doc = {
        "version": "https://jsonfeed.org/version/1.1", "title": FEED_TITLE,
        "home_page_url": FEED_SITE, "feed_url": f"{FEED_SITE}feed.json",
        "description": FEED_DESCRIPTION, "language": "tr",
        "authors": [{"name": "Greater Türkiye", "url": REPO_URL}],
        "items": [{
            "id": f"urn:gt:record:{it['id']}", "url": it["url"], "title": entry_title(it),
            "summary": _text(it["record"].get("summary"), "en"), "content_html": entry_html(it),
            "date_published": _iso_z(it["published"]), "date_modified": _iso_z(it["updated"]),
            "tags": [f"status:{it['status']}"] + [f"region:{r['code']}" for r in it["regions"]],
            "_gt": {"record_id": it["id"], "verification_status": it["status"],
                    "credibility": it["record"]["assessment"]["credibility"],
                    "method": it["record"]["assessment"].get("method", []),
                    "event_type": it["record"]["event_type"],
                    "regions": [r["code"] for r in it["regions"]],
                    "countries": it["record"].get("countries", []),
                    "title": it["record"]["title"], "record_path": it["path"],
                    "sources": it["sources"], "rights": "CC-BY-4.0"},
        } for it in entries],
    }
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def render_digest(entries: list[dict], days: int = DIGEST_DAYS) -> str:
    """A copy-paste digest. The window ends at the newest item, not at the build time, so it is stable."""
    newest = max((it["published"] for it in entries), default=None)
    window = [it for it in entries if newest and it["published"] > newest - timedelta(days=days)]
    since = (newest - timedelta(days=days)) if newest else None
    header = (f"{_iso_z(since)[:10]} → {_iso_z(newest)[:10]} (UTC)" if newest else "—")
    out = [f"# Greater Türkiye — son {days} gün / last {days} days", "", header, "",
           f"{len(window)} doğrulanmış veya kısmen doğrulanmış kayıt / verified or partially verified records", ""]
    if not window:
        out += ["Bu pencerede yayımlanacak kayıt yok. / No records to publish in this window.", ""]
    for it in window:
        tr, en = STATUS_LABEL[it["status"]]
        e = it["record"]
        out += [f"## [{tr} / {en}] {_text(e.get('title'), 'en')}", "",
                f"- TR: {_clip(_text(e.get('summary'), 'tr'), DIGEST_SUMMARY_CHARS)}",
                f"- EN: {_clip(_text(e.get('summary'), 'en'), DIGEST_SUMMARY_CHARS)}",
                f"- Bölge / Region: {', '.join(r['en'] for r in it['regions'])}"
                f" · {_iso_z(it['published'])[:10]} (UTC)"
                f" · Admiralty {e['assessment']['credibility']}/6",
                "- Kaynaklar / Sources: " + "; ".join(f"{s['publisher']} — {s['url']}" for s in it["sources"]),
                f"- Kayıt / Record: {it['url']}", ""]
    out += ["---", "",
            f"Tümü / full feed: {FEED_SITE}feed.xml · {FEED_SITE}feed.json", "",
            ATTRIBUTION_NOTE, "", FEED_RIGHTS, ""]
    return "\n".join(out)


def write_feeds(out: Path, records: dict[str, Record], region_labels: dict[str, dict]) -> dict:
    entries = feed_entries(records, region_labels)
    (out / "feed.xml").write_text(render_rss(entries), encoding="utf-8", newline="\n")
    (out / "feed.json").write_text(render_json_feed(entries), encoding="utf-8", newline="\n")
    (out / "feed.md").write_text(render_digest(entries), encoding="utf-8", newline="\n")
    return {"items": len(entries), "statuses": {s: sum(1 for it in entries if it["status"] == s)
                                                for s in FEED_STATUSES},
            "updated": _iso_z(max(it["updated"] for it in entries)) if entries else None}


# --- commands

def cmd_validate(args) -> int:
    report = Report()
    records = validate_all(report, args.base)
    print(f"{len(records)} records, {report.errors} errors, {report.warnings} warnings")
    return 1 if report.errors else 0


def cmd_build(args) -> int:
    report = Report()
    records = validate_all(report)
    if report.errors:
        print(f"build aborted: {report.errors} errors")
        return 1
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    by_kind: dict[str, list[dict]] = {k: [] for k in list(KINDS) + ["tombstone"]}
    examples: list[dict] = []
    for rid in sorted(records):
        rec = records[rid]
        if rec.base == "data":
            by_kind[rec.kind].append(rec.data)
        else:
            examples.append(rec.data)
    # examples.jsonl holds fictional records for demos only; consumers must label them as such
    for name, rows in [("withdrawn" if k == "tombstone" else k, v) for k, v in by_kind.items()] + [("examples", examples)]:
        with open(out / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    features = []
    with open(out / "events.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "event_type", "start", "end", "precision", "regions", "countries", "title_tr", "title_en",
                     "status", "credibility", "location_precision", "lon", "lat", "source_urls"])
        for e in by_kind["event"]:
            loc = e.get("location") or {}
            geom = loc.get("geometry")
            lon, lat = (geom["coordinates"] if geom and geom["type"] == "Point" else ("", ""))
            w.writerow([e["id"], e["event_type"], e["time"]["start"], e["time"].get("end", ""), e["time"]["precision"],
                        ";".join(e["regions"]), ";".join(e.get("countries", [])), e["title"]["tr"],
                        e["title"].get("en", ""), e["assessment"]["status"], e["assessment"]["credibility"],
                        loc.get("precision", ""), lon, lat, " ".join(s["url"] for s in e["sources"])])
            if geom:
                features.append({"type": "Feature", "id": e["id"], "geometry": geom, "properties": {
                    "event_type": e["event_type"], "start": e["time"]["start"], "regions": e["regions"],
                    "title_tr": e["title"]["tr"], "title_en": e["title"].get("en"),
                    "status": e["assessment"]["status"], "credibility": e["assessment"]["credibility"],
                    "location_precision": loc["precision"]}})
    (out / "events.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                                   ensure_ascii=False), encoding="utf-8")
    vocab = {f.stem: load_yaml(f)["codes"] for f in sorted(VOCAB_DIR.glob("*.yaml"))}
    (out / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False, indent=1), encoding="utf-8")
    region_labels = {c["code"]: c.get("label", {}) for c in vocab.get("regions", [])}
    feed = write_feeds(out, records, region_labels)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    manifest = {"schema_major": 1, "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "commit": commit or None, "counts": {k: len(v) for k, v in by_kind.items()},
                "feed": feed, "license": "CC-BY-4.0"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"dist/ written: {manifest['counts']}; feed: {feed['items']} items")
    return 0


TEMPLATES = {
    "event": """id: {id}
schema: event/1
event_type: TODO            # vocab/event-types.yaml
title:
  tr: TODO
  en: TODO
summary:
  tr: TODO
  en: TODO
time:
  start: TODO               # 2026-09-10T11:00Z (UTC)
  precision: day            # minute | hour | day | month | year
  basis: reported           # reported | chronolocated | estimated
regions: [TODO]             # vocab/regions.yaml
countries: []               # ISO 3166-1 alpha-3, vocab/countries.yaml
actors: []                  # - {ref: act_..., role: participant}
sources:
  - url: TODO
    lang: tr
    archives: []            # - {service: wayback, url: https://web.archive.org/web/...}
assessment:
  status: unverified
  credibility: 6            # 1 confirmed ... 6 cannot be judged
""",
    "actor": """id: {id}
schema: actor/1
name:
  tr: TODO
  en: TODO
kind: TODO                  # vocab/actor-kinds.yaml
country: TODO               # ISO 3166-1 alpha-3
""",
    "site": """id: {id}
schema: site/1
name:
  tr: TODO
  en: TODO
site_type: TODO             # vocab/site-types.yaml
country: TODO
location:
  precision: locality
  method: reported
sources:
  - url: TODO
    lang: en
""",
    "equipment": """id: {id}
schema: equipment/1
name:
  tr: TODO
  en: TODO
category: TODO              # vocab/equipment-categories.yaml
origin_country: TODO
""",
    "source": """id: {id}
schema: source/1
name:
  tr: TODO
  en: TODO
url: TODO
source_type: TODO           # vocab/source-types.yaml
reliability: F              # A reliable ... F cannot be judged
terms: restricted           # open | attribution | restricted | no-redistribution
""",
}


def cmd_new(args) -> int:
    rid = new_id(KINDS[args.kind][0])
    path = ROOT / expected_path(rid, "examples" if args.example else "data")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATES[args.kind].replace("{id}", rid), encoding="utf-8", newline="\n")
    print(path.relative_to(ROOT).as_posix())
    return 0


def cmd_id(args) -> int:
    print(new_id(args.prefix))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate")
    p.add_argument("--base", help="git ref to diff against (blocks deleted/renamed records)")
    p.set_defaults(func=cmd_validate)
    sub.add_parser("build").set_defaults(func=cmd_build)
    p = sub.add_parser("fmt")
    p.add_argument("--check", action="store_true", help="do not write; exit 1 if any record would change")
    p.set_defaults(func=cmd_fmt)
    p = sub.add_parser("new")
    p.add_argument("kind", choices=list(KINDS))
    p.add_argument("--example", action="store_true", help="create under examples/ instead of data/")
    p.set_defaults(func=cmd_new)
    p = sub.add_parser("id")
    p.add_argument("prefix", choices=list(DIR_OF_PREFIX))
    p.set_defaults(func=cmd_id)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
