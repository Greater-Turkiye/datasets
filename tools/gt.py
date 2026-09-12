#!/usr/bin/env python3
"""Greater-Turkiye dataset tool.

  python tools/gt.py validate [--base REF]   schema + vocab + reference + policy checks
  python tools/gt.py build                   compile data/ into dist/ (JSONL, CSV, GeoJSON)
  python tools/gt.py new <kind>              create a record skeleton with a fresh ID
  python tools/gt.py id <prefix>             print a fresh ID (evt, act, sit, eqp, src)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def load_validators(report: Report) -> dict[str, Draft202012Validator]:
    schemas, resources = {}, []
    for f in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = json.loads(f.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        resources.append((schema["$id"], Resource.from_contents(schema)))
        schemas[f.name.removesuffix(".schema.json")] = schema
    registry = Registry().with_resources(resources)
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
        if not (gated or flags.get("involves_tur_forces")):
            return
        err = lambda msg: self.report.error(rec.path, f"[Türk kuvvetleri kapısı / TUR forces gate] {msg}")
        if not flags.get("involves_tur_forces") or flags.get("sensitivity") != "elevated":
            err("set policy.involves_tur_forces: true and policy.sensitivity: elevated")
        loc = d.get("location")
        if loc:
            if "geometry" in loc:
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
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    manifest = {"schema_major": 1, "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "commit": commit or None, "counts": {k: len(v) for k, v in by_kind.items()},
                "license": "CC-BY-4.0"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"dist/ written: {manifest['counts']}")
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
