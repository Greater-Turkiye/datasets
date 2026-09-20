"""Tests for tools/gt.py.

Record-level tests run against a throwaway copy of the repo (schemas/, vocab/, policy.yaml, tools/) under
tmp_path, so real records are never touched.  Run:  python -m pytest tests
"""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc)


def load_gt(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


GT = load_gt(REPO / "tools" / "gt.py", "gt_real")  # pure functions only; never pointed at a temp repo


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


class Repo:
    def __init__(self, root: Path, name: str) -> None:
        self.root = root
        self.gt = load_gt(root / "tools" / "gt.py", name)

    def write(self, data: dict, base: str = "data", path: Path | None = None, text: str | None = None) -> Path:
        path = path or self.root / self.gt.expected_path(data["id"], base)
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def validate(self) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []

        class Capture(self.gt.Report):
            def _emit(self, level, where, msg):
                (errors if level == "error" else warnings).append(msg)

        self.gt.validate_all(Capture())
        return errors, warnings


@pytest.fixture
def repo(tmp_path):
    for d in ("schemas", "vocab", "tools"):
        shutil.copytree(REPO / d, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "policy.yaml", tmp_path / "policy.yaml")
    name = f"gt_tmp_{id(tmp_path)}"
    yield Repo(tmp_path, name)
    sys.modules.pop(name, None)


@pytest.fixture
def world(repo):
    """Baseline registry: a Greek and a Turkish military actor, an open and a restricted source."""
    gt = repo.gt
    ids = SimpleNamespace(grc=gt.new_id("act"), tur=gt.new_id("act"), open=gt.new_id("src"), restricted=gt.new_id("src"))
    repo.write({"id": ids.grc, "schema": "actor/1", "name": {"tr": "Yunan Deniz Kuvvetleri", "en": "Hellenic Navy"},
                "kind": "service-branch", "country": "GRC"})
    repo.write({"id": ids.tur, "schema": "actor/1", "name": {"tr": "Türk Silahlı Kuvvetleri", "en": "Turkish Armed Forces"},
                "kind": "armed-forces", "country": "TUR"})
    repo.write({"id": ids.open, "schema": "source/1", "name": {"tr": "Örnek Haber", "en": "Example News"},
                "url": "https://example.org/", "source_type": "media", "reliability": "C", "terms": "attribution"})
    repo.write({"id": ids.restricted, "schema": "source/1", "name": {"tr": "Kısıtlı Veri", "en": "Restricted Data"},
                "url": "https://data.example.org/", "source_type": "dataset", "reliability": "B", "terms": "restricted"})
    return ids


def good_event(repo, ids) -> dict:
    return {
        "id": repo.gt.new_id("evt"),
        "schema": "event/1",
        "event_type": "air.airspace-incident",
        "title": {"tr": "Ege'de bildirilen hava sahası olayı", "en": "Reported airspace incident over the Aegean"},
        "time": {"start": iso(NOW - timedelta(days=3)), "precision": "hour", "basis": "reported"},
        "location": {"geometry": {"type": "Point", "coordinates": [25.5, 38.9]}, "precision": "sea-area",
                     "method": "reported", "uncertainty_m": 15000},
        "regions": ["aegean"],
        "countries": ["GRC"],
        "actors": [{"ref": ids.grc, "role": "participant"}],
        "sources": [{"ref": ids.open, "url": "https://example.org/a", "lang": "en"}],
        "assessment": {"status": "unverified", "credibility": 3},
    }


# --- IDs and paths

def test_id_roundtrip_and_uuid7_layout(monkeypatch):
    ms = 1_788_000_000_123
    monkeypatch.setattr(GT, "time", SimpleNamespace(time_ns=lambda: ms * 1_000_000))
    rid = GT.new_id("evt")
    assert GT.ID_RE.match(rid)
    assert GT.id_time(rid) == datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    n = 0
    for c in rid.split("_", 1)[1]:
        n = n * 32 + GT.ALPHABET.index(c)
    assert n >> 80 == ms and (n >> 76) & 0xF == 7 and (n >> 62) & 0b11 == 0b10  # timestamp, version 7, RFC variant
    t = GT.id_time(rid)
    assert GT.expected_path(rid) == Path("data", "events", f"{t:%Y}", f"{t:%m}", f"{rid}.yaml")
    assert GT.expected_path(rid, "examples").parts[:2] == ("examples", "events")


def test_ids_sort_by_time(monkeypatch):
    out = []
    for ms in (1_700_000_000_000, 1_700_000_000_001, 1_800_000_000_000):
        monkeypatch.setattr(GT, "time", SimpleNamespace(time_ns=lambda ms=ms: ms * 1_000_000))
        out.append(GT.new_id("act"))
    assert out == sorted(out)


def test_expected_path_of_real_record():
    rid = "act_01m2bez1w9ef8vp8p9n6z7krjg"
    path = GT.expected_path(rid)
    assert path == Path("data/actors/2026/09/act_01m2bez1w9ef8vp8p9n6z7krjg.yaml")
    assert (REPO / path).exists()


# --- YAML loader

def test_loader_keeps_no_and_timestamps_as_strings():
    doc = yaml.load("country: no\nlang: NO\nyes_: yes\nswitch: off\nstart: 2026-09-01T10:00:00Z\n"
                    "short: 2026-09-01T10:00Z\nday: 2026-09-01\nflag: true\nother: false\nn: 3\n", Loader=GT.Loader)
    assert doc == {"country": "no", "lang": "NO", "yes_": "yes", "switch": "off", "start": "2026-09-01T10:00:00Z",
                   "short": "2026-09-01T10:00Z", "day": "2026-09-01", "flag": True, "other": False, "n": 3}


# --- policy gates

def test_good_records_pass(repo, world):
    repo.write(good_event(repo, world))
    assert repo.validate() == ([], [])


def test_tur_gate_compliant_record_only_needs_approval(repo, world):
    e = good_event(repo, world)
    e["actors"] = [{"ref": world.tur, "role": "participant"}]
    e["policy"] = {"involves_tur_forces": True, "sensitivity": "elevated"}
    e["location"] = {"precision": "sea-area", "method": "reported"}
    repo.write(e)
    errors, warnings = repo.validate()
    assert errors == []
    assert any("policy:approved" in w for w in warnings)


def test_tur_actor_as_reporter_is_not_gated(repo, world):
    e = good_event(repo, world)
    e["actors"].append({"ref": world.tur, "role": "reporter"})
    repo.write(e)
    assert repo.validate() == ([], [])


def _tur(e, ids, flags=True, **location):
    e["actors"] = [{"ref": ids.tur, "role": "participant"}]
    if flags:
        e["policy"] = {"involves_tur_forces": True, "sensitivity": "elevated"}
    e["location"] = location or {"precision": "sea-area", "method": "reported"}


def _set(path, value):
    def mutate(e, ids):
        *parents, last = path.split(".")
        node = e
        for p in parents:
            node = node[p]
        node[last] = value(ids) if callable(value) else value
    return mutate


def _text(s):
    return _set("summary", {"tr": f"Kurgusal metin: {s}", "en": "Fictional text."})


def _geometry(geometry, precision="sea-area", uncertainty_m=None):
    floors = {"sea-area": 10000, "locality": 500, "admin2": 5000, "site": 50, "exact": 0}
    return _set("location", {"geometry": geometry, "precision": precision, "method": "reported",
                             "uncertainty_m": floors[precision] if uncertainty_m is None else uncertainty_m})


def _polygon(*ring):
    return {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}


GATE = "[Türk kuvvetleri kapısı / TUR forces gate]"
FENCE = "inside Türkiye's land territory or within 12 nm of its coast"
CASES = [
    pytest.param(lambda e, i: _tur(e, i, flags=False), "set policy.involves_tur_forces: true", id="tur-participant-unflagged"),
    pytest.param(lambda e, i: _set("policy", {"involves_tur_forces": True})(e, i), "set policy.involves_tur_forces", id="tur-flag-without-elevated"),
    pytest.param(lambda e, i: _tur(e, i, geometry={"type": "Point", "coordinates": [25.5, 38.9]}, precision="sea-area", method="reported"),
                 "must not contain coordinates", id="tur-coordinates"),
    pytest.param(lambda e, i: _tur(e, i, precision="locality", method="reported"), "location precision must be one of", id="tur-precision"),
    pytest.param(lambda e, i: (_tur(e, i), _set("time.start", iso(NOW - timedelta(hours=2)))(e, i)), "at least 24 hours old", id="tur-under-24h"),
    pytest.param(_geometry({"type": "Point", "coordinates": [32.85, 39.93]}, "locality"), FENCE, id="geofence-ankara"),
    pytest.param(_geometry({"type": "Point", "coordinates": [30.7, 36.78]}), FENCE, id="geofence-12nm-off-antalya"),
    pytest.param(_geometry({"type": "Point", "coordinates": [28.0, 40.75]}), FENCE, id="geofence-marmara"),
    pytest.param(_geometry(_polygon((20, 39), (20, 39.1), (50, 39.1), (50, 39), (20, 39))), FENCE, id="geofence-polygon-edge-crosses"),
    pytest.param(_geometry(_polygon((20, 30), (50, 30), (50, 46), (20, 46), (20, 30))), FENCE, id="geofence-polygon-encloses"),
    pytest.param(_text("iletisim@ornek.com"), "looks like personal data (email)", id="pii-email"),
    pytest.param(_text("0532 123 45 67"), "looks like personal data (phone)", id="pii-phone"),
    pytest.param(_text("12345678901"), "looks like personal data (tc_kimlik)", id="pii-tc-kimlik"),
    pytest.param(_text("TR33 0006 1005 1978 6457 8413 26"), "looks like personal data (iban)", id="pii-iban"),
    pytest.param(_text("belgede GİZLİ ibaresi"), "classification marking", id="marker-gizli"),
    pytest.param(_text("stamped TOP SECRET"), "classification marking", id="marker-top-secret"),
    pytest.param(_set("sources", lambda i: [{"ref": i.open, "url": "https://bit.ly/abc", "lang": "en"}]),
                 "domain bit.ly is not allowed", id="shortener"),
    pytest.param(_set("sources", lambda i: [{"url": "https://www.tinyurl.com/abc", "lang": "en"}]),
                 "domain www.tinyurl.com is not allowed", id="shortener-subdomain"),
    pytest.param(_set("sources", lambda i: [{"ref": i.restricted, "url": "https://data.example.org/x", "lang": "en"}]),
                 "restricted/no-redistribution sources can only be leads", id="restricted-only-sources"),
    pytest.param(_set("event_type", "air.no-such-type"), "unknown code 'air.no-such-type'", id="unknown-event-type"),
    pytest.param(_set("regions", ["atlantis"]), "unknown code 'atlantis'", id="unknown-region"),
    pytest.param(_set("countries", ["XXX"]), "unknown code 'XXX'", id="unknown-country"),
    pytest.param(_set("email", "someone"), "email: personal-data field is not allowed", id="forbidden-key"),
    pytest.param(_set("actors", lambda i: [{"ref": GT.new_id("act"), "role": "participant"}]), "does not exist", id="dangling-ref"),
    pytest.param(_set("assessment", {"status": "verified", "credibility": 2, "method": ["geolocation"]}),
                 "[verified] sources/0 needs at least one archive", id="verified-without-archives"),
    pytest.param(_set("assessment", {"status": "verified", "credibility": 3, "method": ["geolocation"]}),
                 "[verified] credibility must be <= 2", id="verified-low-credibility"),
]


@pytest.mark.parametrize("mutate, expected", CASES)
def test_policy_gate_rejects(repo, world, mutate, expected):
    e = good_event(repo, world)
    mutate(e, world)
    repo.write(e)
    errors, _ = repo.validate()
    assert any(expected in m for m in errors), errors


def test_geofence_error_is_a_tur_gate_error(repo, world):
    e = good_event(repo, world)
    _geometry({"type": "Point", "coordinates": [32.85, 39.93]}, "locality")(e, world)
    repo.write(e)
    errors, _ = repo.validate()
    assert f"{GATE} location/geometry is {FENCE}; remove the geometry and use a coarse location precision" in errors
    assert not any("set policy.involves_tur_forces" in m for m in errors)  # the coordinates are the problem


def test_verified_with_archives_passes(repo, world):
    e = good_event(repo, world)
    e["assessment"] = {"status": "verified", "credibility": 2, "method": ["geolocation"]}
    e["sources"][0]["archives"] = [{"service": "wayback", "url": "https://web.archive.org/web/2026/https://example.org/a"}]
    repo.write(e)
    assert repo.validate() == ([], [])


def site(repo, ids, lon, lat) -> dict:
    return {"id": repo.gt.new_id("sit"), "schema": "site/1", "name": {"tr": "Deniz Üssü", "en": "Naval Base"},
            "site_type": "naval-base", "country": "GRC", "operators": [ids.grc],
            "location": {"geometry": {"type": "Point", "coordinates": [lon, lat]}, "precision": "locality",
                         "method": "reported", "uncertainty_m": 800},
            "sources": [{"url": "https://example.org/base", "lang": "en"}]}


def test_site_outside_geofence_passes(repo, world):
    repo.write(site(repo, world, 24.12, 35.49))  # Souda Bay
    repo.write(site(repo, world, 26.555, 39.105))  # Mytilene: Greek land, inside the 12 nm sea band
    assert repo.validate() == ([], [])


def test_site_inside_geofence_rejected(repo, world):
    repo.write(site(repo, world, 27.14, 38.42))  # İzmir
    errors, _ = repo.validate()
    assert any(GATE in m and FENCE in m for m in errors), errors


# --- coordinates carry their provenance (ADR 0021)

def test_a_coordinate_without_an_uncertainty_is_rejected(repo, world):
    s = site(repo, world, 24.12, 35.49)
    del s["location"]["uncertainty_m"]
    repo.write(s)
    errors, _ = repo.validate()
    assert any("needs uncertainty_m" in m for m in errors), errors


def test_an_uncertainty_below_the_floor_for_its_precision_is_rejected(repo, world):
    s = site(repo, world, 24.12, 35.49)
    s["location"]["uncertainty_m"] = 10  # ten metres at locality precision claims what nobody has
    repo.write(s)
    errors, _ = repo.validate()
    assert any("below the floor for precision" in m for m in errors), errors


def test_a_register_record_may_now_carry_a_sourced_coordinate(repo, world):
    """The old gate refused coordinates on these records outright; ADR 0021 asks for provenance."""
    s = site(repo, world, 26.14, 39.09)  # Lesbos, Greek territory, outside the geofence
    s["tags"] = ["ege-silahsizlandirilmis-statu"]
    repo.write(s)
    assert repo.validate() == ([], [])


def test_wrong_path_rejected(repo, world):
    e = good_event(repo, world)
    repo.write(e, path=repo.root / "data" / "events" / "2020" / "01" / f"{e['id']}.yaml")
    errors, _ = repo.validate()
    assert any("path is derived from the ID" in m for m in errors), errors


def test_duplicate_id_rejected(repo, world):
    e = good_event(repo, world)
    repo.write(e)
    repo.write(e, base="examples")
    errors, _ = repo.validate()
    assert any(m.startswith("duplicate id") for m in errors), errors


def test_real_records_validate_and_are_formatted(repo):
    for d in ("data", "examples"):
        shutil.copytree(REPO / d, repo.root / d)
    errors, _ = repo.validate()
    assert errors == []
    assert repo.gt.cmd_fmt(Namespace(check=True)) == 0


# --- geofence geometry

@pytest.fixture(scope="module")
def fence():
    return GT.Geofence(GT.TUR_BOUNDARY)


@pytest.mark.parametrize("lon, lat, inside", [
    (32.85, 39.93, True),    # Ankara
    (29.03, 41.05, True),    # Bosphorus
    (28.0, 40.75, True),     # centre of the Sea of Marmara (> 12 nm from both coasts)
    (30.7, 36.78, True),     # ~9 km off Antalya
    (35.15, 42.15, True),    # Black Sea, ~15 km off Sinop
    (26.62, 39.30, True),    # strait between Lesbos and Ayvalık
    (25.5, 38.9, False),     # the Aegean example event
    (24.12, 35.49, False),   # Souda Bay
    (26.555, 39.105, False), # Mytilene, Lesbos (foreign land within the sea band)
    (25.87, 40.85, False),   # Alexandroupoli
    (41.22, 37.05, False),   # Qamishli: no 12 nm buffer on land borders
    (37.16, 36.20, False),   # Aleppo
    (33.32, 35.34, False),   # Kyrenia
    (-3.7, 40.4, False),     # Madrid (bbox fast path)
])
def test_geofence_points(fence, lon, lat, inside):
    assert fence.contains(lon, lat) is inside


def test_aegean_example_is_well_beyond_12nm(fence):
    doc = json.loads(GT.TUR_BOUNDARY.read_text(encoding="utf-8"))
    nearest = min(fence.distance_km(25.5, 38.9, line) for line in doc["coast"])
    assert nearest > GT.TERRITORIAL_SEA_KM + doc["coast_tolerance_km"]
    assert 75 < nearest < 90  # ~82 km to the Karaburun peninsula


def test_distance_approximation():
    # 0.2 deg of latitude along a meridian, and 0.2 deg of longitude at 39N (cos 39 = 0.7771)
    assert GT.Geofence.distance_km(30.0, 39.0, [[30.0, 38.8], [30.0, 38.8]]) == pytest.approx(22.239, abs=0.01)
    assert GT.Geofence.distance_km(30.0, 39.0, [[30.2, 38.0], [30.2, 40.0]]) == pytest.approx(17.28, abs=0.02)


def test_boundary_data_is_small_and_documented():
    doc = json.loads(GT.TUR_BOUNDARY.read_text(encoding="utf-8"))
    assert "Natural Earth" in doc["source"] and "ublic domain" in doc["license"]
    assert sum(len(r) for r in doc["polygons"]) < 700
    for ring in doc["polygons"] + doc["internal_waters"] + [f["ring"] for f in doc["foreign_land"]]:
        assert ring[0] == ring[-1] and len(ring) >= 4


# --- build

def test_build_outputs(repo, world):
    gt = repo.gt
    e = good_event(repo, world)
    repo.write(e)
    ex = good_event(repo, world)
    ex["tags"] = ["example"]
    repo.write(ex, base="examples")
    tomb = {"id": gt.new_id("eqp"), "schema": "tombstone/1", "withdrawn_at": "2026-09-10", "reason": "duplicate"}
    repo.write(tomb)
    assert gt.cmd_build(Namespace()) == 0

    dist = repo.root / "dist"
    assert sorted(p.name for p in dist.iterdir()) == sorted([
        "event.jsonl", "actor.jsonl", "site.jsonl", "equipment.jsonl", "source.jsonl", "withdrawn.jsonl",
        "examples.jsonl", "events.csv", "events.geojson", "vocab.json", "manifest.json",
        "feed.xml", "feed.json", "feed.md"])

    def ids(name):
        return [json.loads(line)["id"] for line in (dist / name).read_text(encoding="utf-8").splitlines()]

    assert ids("event.jsonl") == [e["id"]]
    assert ids("examples.jsonl") == [ex["id"]]  # fictional records never land in event.jsonl
    assert ids("withdrawn.jsonl") == [tomb["id"]]
    assert ids("actor.jsonl") == sorted([world.grc, world.tur])
    assert ids("source.jsonl") == sorted([world.open, world.restricted])

    with open(dist / "events.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(r["id"], r["lon"], r["lat"], r["location_precision"]) for r in rows] == [(e["id"], "25.5", "38.9", "sea-area")]
    features = json.loads((dist / "events.geojson").read_text(encoding="utf-8"))["features"]
    assert [f["id"] for f in features] == [e["id"]]
    manifest = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {"event": 1, "actor": 2, "site": 0, "equipment": 0, "source": 2, "tombstone": 1}
    assert manifest["feed"] == {"items": 0, "statuses": {"verified": 0, "partially_verified": 0}, "updated": None}
    assert "aegean" in {c["code"] for c in json.loads((dist / "vocab.json").read_text(encoding="utf-8"))["regions"]}


def test_build_aborts_on_errors(repo, world):
    e = good_event(repo, world)
    e["event_type"] = "air.no-such-type"
    repo.write(e)
    assert repo.gt.cmd_build(Namespace()) == 1
    assert not (repo.root / "dist").exists()


# --- fmt

def test_fmt_orders_keys_adds_ref_comments_and_is_idempotent(repo, world):
    rid = repo.gt.new_id("sit")
    messy = f"""# ÖRNEK başlık / example header
# second header line

sources:
  - lang: en
    url: https://example.org/site
operators: [{world.grc}]   # stale comment
location: {{method: reported, uncertainty_m: 800, precision: locality, geometry: {{coordinates: [24.12, 35.49], type: Point}}}}
site_type: naval-base
name: {{en: Souda Bay Naval Base, tr: Suda Deniz Üssü}}
schema: site/1
id: {rid}   # the id itself is not annotated
aliases: ["no", "a: b", "x #y", "2026-09-01", "[x]", plain words]
"""
    path = repo.write({"id": rid}, text=messy)
    assert repo.gt.cmd_fmt(Namespace(check=True)) == 1
    assert path.read_text(encoding="utf-8") == messy  # --check never writes
    assert repo.gt.cmd_fmt(Namespace(check=False)) == 0
    assert path.read_text(encoding="utf-8") == f"""# ÖRNEK başlık / example header
# second header line
id: {rid}
schema: site/1
name:
  tr: Suda Deniz Üssü
  en: Souda Bay Naval Base
aliases: ["no", "a: b", "x #y", 2026-09-01, "[x]", plain words]
site_type: naval-base
operators: [{world.grc}]  # Hellenic Navy
location:
  geometry: {{type: Point, coordinates: [24.12, 35.49]}}
  precision: locality
  uncertainty_m: 800
  method: reported
sources:
  - url: https://example.org/site
    lang: en
"""
    assert repo.gt.cmd_fmt(Namespace(check=True)) == 0
    assert repo.validate() == ([], [])


def test_fmt_block_lists_and_long_values(repo, world):
    e = good_event(repo, world)
    e["actors"].append({"ref": world.tur, "role": "reporter"})
    e["tags"] = [f"tag-{i:02d}-" + "x" * 12 for i in range(8)]  # too wide for one flow line
    e["sources"][0]["archives"] = [{"service": "wayback", "url": "https://web.archive.org/web/2026/https://example.org/a"}]
    path = repo.write(e)
    assert repo.gt.cmd_fmt(Namespace(check=False)) == 0
    out = path.read_text(encoding="utf-8")
    assert f"  - {{ref: {world.tur}, role: reporter}}  # Turkish Armed Forces\n" in out
    assert "tags:\n  - tag-00-xxxxxxxxxxxx\n" in out
    assert f"sources:\n  - ref: {world.open}  # Example News\n    url: https://example.org/a\n    lang: en\n" \
           "    archives:\n      - {service: wayback, url: https://web.archive.org/web/2026/https://example.org/a}\n" in out
    assert yaml.load(out, Loader=repo.gt.Loader) == e
    assert repo.gt.cmd_fmt(Namespace(check=True)) == 0


def test_fmt_scalars_roundtrip():
    f = GT.Formatter(GT.load_schemas(), {})
    for value in ["no", "yes", "on", "null", "~", "123", "1.5", "true", "", " lead", "trail ", "a\nb", "tab\tx",
                  "quote\"s", "it's", "# hash", "- dash", "key: value", "@at", "`tick", "%pct", "!bang", "&amp", "*star",
                  "|pipe", ">gt", "{curly}", "[square]", "Ünicode ğüşiöç", "2026-09-01T10:00Z", "2026-09-01"]:
        for flow in (False, True):
            text = f"k: {f.scalar(value, flow)}" if not flow else f"k: [{f.scalar(value, flow)}]"
            loaded = yaml.load(text, Loader=GT.Loader)["k"]
            assert (loaded[0] if flow else loaded) == value, (value, flow, text)
    assert f.scalar(1e-05) == "0.00001" and f.scalar(24.12) == "24.12" and f.scalar(True) == "true"


# --- feed

def published_event(repo, ids, slug: str, status: str = "verified", days_ago: int = 3) -> dict:
    """An event the feed is allowed to publish: verified/partially verified, bilingual, archived sources."""
    e = good_event(repo, ids)
    e["title"] = {"tr": f"TR {slug}", "en": f"EN {slug}"}
    e["summary"] = {"tr": f"Kaynağa göre {slug}.", "en": f"According to the source, {slug}."}
    e["time"]["start"] = iso(NOW - timedelta(days=days_ago))
    e["reported_at"] = iso(NOW - timedelta(days=days_ago))
    e["assessment"] = {"status": status, "credibility": 2, "method": ["geolocation"]}
    e["sources"][0]["archives"] = [{"service": "wayback", "url": "https://web.archive.org/web/2026/https://example.org/a"}]
    return e


def build_feed(repo) -> tuple[dict, str, str]:
    """Build and return (feed.json document, feed.xml text, feed.md text)."""
    assert repo.gt.cmd_build(Namespace()) == 0
    dist = repo.root / "dist"
    return (json.loads((dist / "feed.json").read_text(encoding="utf-8")),
            (dist / "feed.xml").read_text(encoding="utf-8"),
            (dist / "feed.md").read_text(encoding="utf-8"))


def feed_ids(doc: dict) -> list[str]:
    return [item["_gt"]["record_id"] for item in doc["items"]]


def test_feed_publishes_only_verified_and_partially_verified(repo, world):
    keep = {s: published_event(repo, world, s, status=s) for s in ("verified", "partially_verified")}
    drop = {s: published_event(repo, world, s, status=s) for s in ("unverified", "disputed", "false")}
    for e in list(keep.values()) + list(drop.values()):
        repo.write(e)
    doc, xml, digest = build_feed(repo)
    assert set(feed_ids(doc)) == {e["id"] for e in keep.values()}
    for e in drop.values():
        assert e["id"] not in xml and e["id"] not in digest
    root = ET.fromstring(xml)
    assert len(root.findall(".//item")) == 2
    manifest = json.loads((repo.root / "dist" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["feed"]["statuses"] == {"verified": 1, "partially_verified": 1}


def test_feed_never_publishes_fictional_examples(repo, world):
    real = published_event(repo, world, "real")
    fictional = published_event(repo, world, "fictional")
    repo.write(real)
    repo.write(fictional, base="examples")
    doc, xml, digest = build_feed(repo)
    assert feed_ids(doc) == [real["id"]]
    assert fictional["id"] not in xml and fictional["id"] not in digest


def test_feed_item_carries_status_credibility_sources_and_attribution(repo, world):
    e = published_event(repo, world, "memorandum", status="partially_verified")
    e["assessment"]["note"] = {"tr": "Tek kaynak.", "en": "Single source."}
    repo.write(e)
    doc, xml, digest = build_feed(repo)
    item = doc["items"][0]
    assert item["_gt"]["verification_status"] == "partially_verified"
    assert item["_gt"]["credibility"] == 2 and item["_gt"]["method"] == ["geolocation"]
    assert item["tags"] == ["status:partially_verified", "region:aegean"]
    assert item["title"].startswith("[KISMEN DOĞRULANMIŞ / PARTIALLY VERIFIED] ")
    for text in (item["content_html"], digest):
        assert "PARTIALLY VERIFIED" in text
        assert "https://example.org/a" in text                      # the source
        assert e["id"] in text                                       # the permalink to the record
    assert "https://web.archive.org/web/2026/" in item["content_html"]  # the archive
    assert "contested characterisations belong to their sources" in item["content_html"]
    assert "Single source." in item["content_html"]                  # the assessment caveat travels with the item
    root = ET.fromstring(xml)
    entry = root.find(".//item")
    assert entry.findtext("title").startswith("[KISMEN DOĞRULANMIŞ / PARTIALLY VERIFIED] ")
    assert [c.text for c in entry.findall("category")] == ["status:partially_verified", "region:aegean"]
    assert entry.find("guid").text == f"urn:gt:record:{e['id']}"
    assert e["id"] in entry.findtext("link")


def test_feed_is_newest_first_and_stable_across_rebuilds(repo, world):
    old = published_event(repo, world, "older", days_ago=5)
    new = published_event(repo, world, "newer", days_ago=1)
    tie_a, tie_b = (published_event(repo, world, f"tie-{n}", days_ago=3) for n in ("a", "b"))
    for e in (old, new, tie_a, tie_b):
        repo.write(e)
    doc, xml, digest = build_feed(repo)
    ties = sorted([tie_a["id"], tie_b["id"]], reverse=True)  # equal timestamps: by ID, never by filesystem order
    assert feed_ids(doc) == [new["id"], *ties, old["id"]]

    before = [(repo.root / "dist" / n).read_bytes() for n in ("feed.xml", "feed.json", "feed.md")]
    build_feed(repo)
    assert [(repo.root / "dist" / n).read_bytes() for n in ("feed.xml", "feed.json", "feed.md")] == before

    # the channel timestamp comes from the newest record, not from the moment of the build
    root = ET.fromstring(xml)
    assert root.findtext("./channel/lastBuildDate") == root.findtext(".//item/pubDate")
    assert doc["items"][0]["date_published"] == repo.gt._iso_z(GT.parse_dt(new["reported_at"]))


def test_feed_updated_time_follows_the_latest_correction(repo, world):
    e = published_event(repo, world, "corrected", days_ago=5)
    e["corrections"] = [{"date": iso(NOW - timedelta(days=2))[:10], "note": {"tr": "Düzeltildi.", "en": "Corrected."}}]
    repo.write(e)
    doc, _, _ = build_feed(repo)
    item = doc["items"][0]
    assert item["date_modified"] > item["date_published"]
    assert item["date_modified"].startswith(iso(NOW - timedelta(days=2))[:10])
    assert "Corrected." in item["content_html"]


def test_digest_window_is_anchored_on_the_newest_record(repo, world):
    recent = published_event(repo, world, "recent", days_ago=1)
    stale = published_event(repo, world, "stale", days_ago=30)
    repo.write(recent)
    repo.write(stale)
    doc, xml, digest = build_feed(repo)
    assert feed_ids(doc) == [recent["id"], stale["id"]]        # the feed keeps everything
    assert "EN recent" in digest and "EN stale" not in digest  # the digest is the last 7 days only
    assert "1 doğrulanmış veya kısmen doğrulanmış kayıt" in digest
    assert repo.gt._iso_z(GT.parse_dt(recent["reported_at"]))[:10] in digest


def test_feed_is_valid_when_nothing_is_published_yet(repo, world):
    repo.write(good_event(repo, world))  # unverified only
    doc, xml, digest = build_feed(repo)
    assert doc["items"] == [] and doc["version"] == "https://jsonfeed.org/version/1.1"
    root = ET.fromstring(xml)
    assert root.findall(".//item") == [] and root.findtext("./channel/title")
    assert root.findtext("./channel/lastBuildDate") is None
    assert "No records to publish in this window." in digest
