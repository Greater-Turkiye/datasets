#!/usr/bin/env python3
"""Turn the collector's relevant candidates into unverified event records, automatically.

    python tools/auto_records.py --days 3            # fetch recent batches, write records, validate
    python tools/auto_records.py --batch batch.json  # one local batch file
    python tools/auto_records.py --dry-run           # print what would be written

Handbook ADR 0023 lets automation publish **unverified** records without a person in between, and
ADR 0024 says how it does so without a language model. What that buys is volume; what it costs is
that nobody has read these records before they are public. So every record says so, in the fields a
reader already looks at:

- `assessment.status: unverified` and `credibility: 6` ("cannot be judged") — never better;
- a bilingual `assessment.note` naming the feed and saying that no person reviewed it;
- the tag `otomatik`, so a later verification pass can find every one of them;
- `i18n: {source: ..., machine: [...]}` — the English title is the source's own headline, the Turkish
  one is a machine translation of it (MyMemory), and the record says so.

There is no summary: the only text available is the source's excerpt, and copying it into a CC BY
dataset is what ADR 0009 rules out. The title and the link are the record until a person writes one.

What automation may not do is unchanged. A candidate is skipped when the collector marked it
`redline_check`, when its text names Turkish forces (a second net under the collector's own safety
filter), when its source is graded below the queue floor, when it reads as analysis rather than an
occurrence, or when a source it cites is already in a record. Every record passes `gt.py validate`
before it is kept; one that does not is deleted. When the translation service is unavailable nothing
more is written, and the next run tries again.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt  # noqa: E402

ROOT = gt.ROOT
PLATFORM = "Greater-Turkiye/platform"
STATE_REF = "collector-state"
BATCH_DIR = "collectors/state/batches"
TRANSLATE_URL = "https://api.mymemory.translated.net/get"
PAUSE = float(os.environ.get("AUTO_RECORDS_PAUSE", "1"))
AUTO_TAG = "otomatik"
USER_AGENT = "GreaterTurkiyeDatasets/auto-records (+https://github.com/Greater-Turkiye/datasets)"

# A second net under the collector's own safety filter (platform/collectors safety.py). Automation
# publishes nothing about Turkish forces; those items wait for a person, whatever they say.
TUR_FORCES = re.compile(
    r"\b(turkish|türk|turkiye'?s?|türkiye'?nin|turkey'?s?)\s+(armed forces|army|navy|air force|"
    r"troops|forces|soldiers|military|warships?|frigates?|jets?|drones?|silahlı kuvvetler\w*|"
    r"kara kuvvetler\w*|deniz kuvvetler\w*|hava kuvvetler\w*|asker\w*|ordu\w*)"
    r"|\bTSK\b|\bMehmetçik\w*|\bMSB\b|\bTAF\b",
    re.IGNORECASE,
)

# Analysis, opinion and newsletters are not occurrences. Without a model to read them, the feed and the
# headline's shape decide: an analysis institute's feed never takes this path, and neither does a
# headline that asks a question or announces a podcast, a digest or somebody quoted elsewhere.
ANALYSIS_FEEDS = frozenset({"rss-usa-atlanticcouncil"})
NOT_AN_EVENT = re.compile(
    r"\?\s*$|\b(podcast|digest|live blog|newsletter|explainer|analysis|opinion|interview|weekly|"
    r"quoted|cited|comments on|in the news|trial stories|questions|brace[sd]? for|"
    r"what .{0,40} means|how .{0,40} could|why .{0,40} (is|are))\b",
    re.IGNORECASE,
)
STOP = set("""a an and the of in on at to for by with from as is are was were be been has have had after
before over into about amid its it this that these those new says said say will would could
bir ve ile için olarak da de bu şu""".split())

# Headline -> event type, first match wins; the collector's own topic is the fallback. The collector
# matched keywords anywhere in the item; these read the headline and put the act first, so a
# statement condemning an attack is a statement and a purchase of rocket launchers is a purchase.
TYPE_RULES = [
    (r"\b(sentenc|convict|verdict|court|trial|indict)", "other"),
    (r"\b(statement|condemn|address(es)? (to )?the|explanation of vote|urges?|warns?|calls? (on|for)|"
     r"denounce|slams?|resolution|veto)", "diplomatic.statement"),
    (r"\b(telephone conversation|phone call|talks|meets?|meeting|visit|summit|negotiat)", "diplomatic.talks"),
    (r"\bsanction", "policy.sanctions"),
    (r"\b(signs?|signed|agreement|memorandum|treaty|accord)\b", "diplomatic.agreement"),
    (r"\b(buys?|purchas|orders?|contract|acqui|procure)", "procurement.contract"),
    (r"\bdeliver", "procurement.delivery"),
    (r"\b(exercise|drill|manoeuvre|maneuver)", "exercise.military"),
    (r"\b(missile test|test[- ]fire)", "test.missile"),
    (r"\b(ship|vessel|tanker|cargo)\b.{0,60}\b(attack|strike|struck|hit|seiz|board)|"
     r"\b(attack|strike|struck|hit|seiz|board)\w*\b.{0,60}\b(ship|vessel|tanker|cargo)\b", "maritime.incident"),
    (r"\b(drone|uav|shahed)", "kinetic.drone-strike"),
    (r"\b(missile|ballistic|cruise)", "kinetic.missile-strike"),
    (r"\b(airstrike|air strike|glide bomb|bombing|bombs?)\b", "kinetic.airstrike"),
    (r"\b(shell|artillery|mlrs|rocket launcher|uragan|grad)\b", "kinetic.shelling"),
    (r"\b(clash|fighting|offensive|assault|battle)", "kinetic.clash"),
    (r"\b(attack|strike|kill|injur|wound)", "kinetic.attack"),
    (r"\b(deploy|withdraw)", "deployment.announced"),
]


@dataclass
class Candidate:
    url: str
    title: str
    text: str
    lang: str
    published_at: str
    region: str
    topics: list[str]
    feed: str
    reliability: str | None = None
    run_url: str | None = None


@dataclass
class Cluster:
    items: list[Candidate] = field(default_factory=list)

    @property
    def lead(self) -> Candidate:
        return self.items[0]


# --- input -----------------------------------------------------------------------------------------

def http_json(url: str, token: str | None = None, timeout: int = 90):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise ValueError(f"HTTP {r.status} {r.headers.get('Content-Type')} is not JSON: {raw[:200]!r}") from None


def recent_batches(days: int, token: str | None) -> list[dict]:
    listing = http_json(f"https://api.github.com/repos/{PLATFORM}/contents/{BATCH_DIR}?ref={STATE_REF}", token)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    names = sorted(e["name"] for e in listing if re.match(r"^\d{4}-\d{2}-\d{2}-.+\.json$", e["name"]))
    return [http_json(f"https://raw.githubusercontent.com/{PLATFORM}/{STATE_REF}/{BATCH_DIR}/{n}", token)
            for n in names if n[:10] >= cutoff]


def candidates(batch: dict) -> list[Candidate]:
    out = []
    for row in batch.get("rows", []):
        if row.get("triage_status") not in ("queued", "pending"):
            continue
        labels = json.loads(row.get("triage_labels") or "{}")
        if labels.get("redline_check"):
            continue
        region = row.get("region")
        topics = [t for t in labels.get("topics", []) if t]
        when = row.get("published_at") or row.get("fetched_at")
        if not region or not topics or not row.get("url") or not row.get("title") or not when:
            continue
        out.append(Candidate(
            url=row["url"], title=" ".join(row["title"].split()), text=" ".join((row.get("text") or "").split()),
            lang=row.get("lang") or "en", published_at=when, region=region, topics=topics,
            feed=labels.get("feed") or row.get("collector_id") or "", reliability=labels.get("reliability"),
            run_url=batch.get("run_url"),
        ))
    return out


# --- selection -------------------------------------------------------------------------------------

def norm_url(url: str) -> str:
    u = url.strip().lower().replace("http://", "https://", 1)
    return u[:-1] if u.endswith("/") else u


def cited_urls(root: Path = ROOT) -> set[str]:
    seen = set()
    for path in (root / "data").rglob("*.yaml"):
        for m in re.finditer(r"^\s*-?\s*url:\s*\"?([^\"\s#]+)", path.read_text(encoding="utf-8"), re.M):
            seen.add(norm_url(m.group(1)))
    return seen


def refused(c: Candidate) -> str | None:
    if TUR_FORCES.search(f"{c.title} {c.text}"):
        return "mentions Turkish forces"
    if c.reliability and c.reliability > "D":
        return f"source graded {c.reliability}"
    if c.feed in ANALYSIS_FEEDS or NOT_AN_EVENT.search(c.title):
        return "analysis, not an occurrence"
    return None


def select(batches: list[dict], cited: set[str]) -> tuple[list[Candidate], dict[str, int]]:
    seen, pool, skipped = set(), [], {}
    for b in batches:
        for c in candidates(b):
            key = norm_url(c.url)
            reason = "already recorded" if key in cited or key in seen else refused(c)
            seen.add(key)
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            pool.append(c)
    pool.sort(key=lambda c: c.published_at)
    return pool, skipped


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[\w'-]+", s.lower()) if len(w) > 3 and w not in STOP}


def cluster(cands: list[Candidate], threshold: float = 0.4) -> list[Cluster]:
    """Items on the same day, in the same region, with most of their title words in common are one
    event reported more than once (a death toll that rises through the day). They become one record
    with several sources, not several records."""
    clusters: list[Cluster] = []
    for c in cands:
        d, w = c.published_at[:10], words(c.title)
        for cl in clusters:
            lw = words(cl.lead.title)
            if cl.lead.region != c.region or cl.lead.published_at[:10] != d or not (w | lw):
                continue
            if len(w & lw) / len(w | lw) >= threshold:
                cl.items.append(c)
                break
        else:
            clusters.append(Cluster([c]))
    return clusters


def classify(c: Candidate, codes: set[str]) -> str:
    for pattern, code in TYPE_RULES:
        if code in codes and re.search(pattern, c.title, re.IGNORECASE):
            return code
    return c.topics[0]


def vocab_codes() -> list[str]:
    doc = yaml.safe_load((ROOT / "vocab" / "event-types.yaml").read_text(encoding="utf-8"))
    rows = next(v for v in doc.values() if isinstance(v, list)) if isinstance(doc, dict) else doc
    return [r["code"] for r in rows if isinstance(r, dict) and "code" in r]


# --- translation -----------------------------------------------------------------------------------

class TranslationUnavailable(RuntimeError):
    pass


def translate(text: str, src: str, dst: str) -> str:
    """MyMemory's free endpoint: no account, a daily character quota per address. A quota or an error
    raises, so the caller stops asking and leaves the rest for the next run."""
    if src == dst:
        return text
    q = urllib.parse.urlencode({"q": text[:480], "langpair": f"{src}|{dst}"})
    try:
        data = http_json(f"{TRANSLATE_URL}?{q}", timeout=30)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        raise TranslationUnavailable(str(e)) from None
    if data.get("quotaFinished") or str(data.get("responseStatus")) != "200":
        raise TranslationUnavailable(f"{data.get('responseStatus')} {data.get('responseDetails')}")
    out = " ".join(((data.get("responseData") or {}).get("translatedText") or "").split())
    if not out or out.upper().startswith(("MYMEMORY WARNING", "QUERY LENGTH LIMIT", "INVALID")):
        raise TranslationUnavailable(out or "empty translation")
    return out


def clean(s, limit: int) -> str | None:
    if not isinstance(s, str):
        return None
    s = " ".join(s.split())
    return s[:limit].rstrip() if s else None


def titles(cl: Cluster) -> dict:
    """tr and en titles, and the i18n block saying which is the source and which are machine-made."""
    lead = cl.lead
    src = (lead.lang or "en").split("-")[0]
    head = clean(lead.title, 300)
    if src == "en":
        return {"tr": translate(head, "en", "tr"), "en": head, "i18n": {"source": "en", "machine": ["tr"]}}
    if src == "tr":
        return {"tr": head, "en": translate(head, "tr", "en"), "i18n": {"source": "tr", "machine": ["en"]}}
    en = translate(head, src, "en")
    time.sleep(PAUSE)
    return {"tr": translate(head, src, "tr"), "en": en, "i18n": {"source": "en", "machine": ["tr", "en"]}}


# --- records ---------------------------------------------------------------------------------------

def day(value: str) -> str:
    return f"{value[:10]}T00:00Z"


def minute(value: str) -> str:
    return value[:16] + "Z" if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value) else day(value)


def record(cl: Cluster, t: dict, codes: set[str]) -> dict:
    lead = cl.lead
    feeds = ", ".join(sorted({x.feed for x in cl.items if x.feed})) or "?"
    sources = []
    for x in cl.items:
        src = {"url": x.url, "title": x.title, "published_at": minute(x.published_at), "lang": x.lang}
        if x.reliability:
            src["reliability"] = x.reliability
        sources.append(src)
    return {
        "id": gt.new_id("evt"),
        "schema": "event/1",
        "event_type": classify(lead, codes),
        "title": {"tr": clean(t["tr"], 300), "en": clean(t["en"], 300)},
        "i18n": t["i18n"],
        "reported_at": minute(lead.published_at),
        "time": {"start": day(lead.published_at), "precision": "day", "basis": "reported"},
        "regions": [lead.region],
        "sources": sources,
        "assessment": {
            "status": "unverified",
            "credibility": 6,
            "note": {
                "tr": ("Otomatik kayıt: toplayıcının ilgi süzgecinden geçen bir aday, kimse okumadan yayımlandı "
                       f"(ADR 0023). Akış: {feeds}. Başlık kaynağın başlığıdır, çevirisi makinecedir (MyMemory); "
                       "tür ve bölge anahtar kelimeyle bulundu, olay tarihi yayın tarihidir. Doğrulanmamıştır."),
                "en": ("Automatic record: a candidate that passed the collector's relevance filter, published "
                       f"without anyone reading it (ADR 0023). Feed: {feeds}. The title is the source's headline, "
                       "machine-translated (MyMemory); type and region were found by keyword and the event date "
                       "is the publication date. Not verified."),
            },
        },
        "tags": [AUTO_TAG],
    }


def write(rec: dict, formatter: gt.Formatter) -> Path:
    path = ROOT / gt.expected_path(rec["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(rec, allow_unicode=True, sort_keys=False)
    path.write_text(formatter.format(text, rec, "event"), encoding="utf-8", newline="\n")
    return path


def failing(paths: list[Path]) -> set[Path]:
    """Validate the whole repository and return which of the new files carry errors."""
    bad: set[Path] = set()

    class Collect(gt.Report):
        def _emit(self, level, where, msg):
            super()._emit(level, where, msg)
            if level == "error" and isinstance(where, Path):
                bad.add(where.resolve())

    gt.validate_all(Collect())
    return {p for p in paths if p.resolve() in bad}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--days", type=int, default=3, help="batches from the last N days (default 3)")
    ap.add_argument("--batch", type=Path, action="append", help="a local batch file instead of fetching")
    ap.add_argument("--max", type=int, default=150, help="at most N records per run (default 150)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    batches = [json.loads(p.read_text(encoding="utf-8")) for p in args.batch] if args.batch \
        else recent_batches(args.days, token)
    pool, skipped = select(batches, cited_urls())
    clusters = cluster(pool)[: args.max]
    codes = set(vocab_codes())
    print(f"{len(batches)} batches, {len(pool)} new candidates, {len(clusters)} events; skipped: {skipped}")
    if args.dry_run:
        for cl in clusters:
            print(f"  {cl.lead.published_at[:10]} {cl.lead.region:14} {classify(cl.lead, codes):24} "
                  f"{len(cl.items)}x {cl.lead.title[:90]}")
        return 0
    if not clusters:
        return 0

    report = gt.Report()
    records = gt.load_records(report)
    formatter = gt.Formatter(gt.load_schemas(), {rid: gt.record_label(r) for rid, r in records.items()})
    written, waiting = [], 0
    for n, cl in enumerate(clusters):
        try:
            t = titles(cl)
        except TranslationUnavailable as e:
            waiting = len(clusters) - n
            print(f"translation unavailable ({e}); {waiting} items wait for the next run", file=sys.stderr)
            break
        try:
            written.append(write(record(cl, t, codes), formatter))
        except (ValueError, KeyError) as e:
            print(f"could not format {cl.lead.url}: {e}", file=sys.stderr)
        time.sleep(PAUSE)
    bad = failing(written)
    for p in bad:
        p.unlink()
    line = (f"{len(written) - len(bad)} records written, {len(bad)} removed for failing validation, "
            f"{waiting} waiting for translation; skipped {skipped}")
    print(line)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"### Automatic records\n\n{line}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
