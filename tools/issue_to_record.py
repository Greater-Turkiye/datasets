#!/usr/bin/env python3
"""Turn an approved data-submission issue into a draft event record.

  python tools/issue_to_record.py --event <github event json> --out-dir <dir>

Reads a GitHub `issues.labeled` event payload, parses the fields of the
`01-data-submission.yml` issue form, creates a record skeleton with
`python tools/gt.py new event`, fills in what the form actually states, then runs
`gt.py fmt` and `gt.py validate` on the result.

Nothing here decides that anything is true.  The output is always a *draft*:
`assessment.status` is `unverified`, the credibility never starts better than 4,
and every uncertain field is left out instead of guessed.  A human reviewer fills
the gaps and approves the pull request.

The issue body is untrusted text.  It is only ever read from the event JSON file
and written into YAML and Markdown files; it is never interpolated into a shell
command, and `gt.py` is invoked with an argument list (no shell).

Exit codes: 0 = a draft was produced *or* the submission was rejected (see the
`status` output), 1 = the workflow could not run at all.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "tools" / "gt.py"
VOCAB_DIR = ROOT / "vocab"

APPROVAL_LABEL = "kayda-gec"
SUBMISSION_LABEL = "data-submission"

# Headings rendered by .github/ISSUE_TEMPLATE/01-data-submission.yml, keyed by the form's field id.
# Matching is on a normalised prefix so that punctuation or a trailing translation cannot break it.
FIELD_HEADINGS = {
    "what_tr": "ne oldu? (türkçe)",
    "what_en": "ne oldu? (i̇ngilizce",
    "when_utc": "ne zaman?",
    "region": "bölge",
    "place": "yer",
    "event_type": "olay türü",
    "source_urls": "kaynak bağlantıları",
    "archive_urls": "arşiv bağlantıları",
    "confidence": "güven düzeyiniz",
    "notes": "notlar",
    "red_lines": "kırmızı çizgiler",
}
NO_RESPONSE = {"_no response_", "_yanıt yok_", "*no response*", "n/a", "-"}

# Free text -> vocab/event-types.yaml.  Longest match wins; anything unmatched becomes `other`,
# which the reviewer checklist calls out.  Never invents a code that is not in the vocabulary.
EVENT_TYPE_HINTS = [
    ("exercise.naval", ["deniz tatbikat", "naval exercise", "donanma tatbikat", "atisli egitim", "live-fire"]),
    ("exercise.air", ["hava tatbikat", "air exercise", "hava kuvvetleri tatbikat"]),
    ("exercise.multinational", ["cok uluslu", "multinational exercise", "ortak tatbikat", "joint exercise"]),
    ("exercise.military", ["tatbikat", "exercise", "drill", "manevra"]),
    ("deployment.withdrawal", ["cekilme", "withdrawal", "withdraw", "geri cekil"]),
    ("deployment.observed", ["gozlenen konuslanma", "observed deployment", "uydu goruntusu konuslanma"]),
    ("deployment.announced", ["konuslanma", "deployment", "deploy", "kuvvet gonder", "asker gonder"]),
    ("basing.construction", ["askeri insaat", "us insaat", "base construction", "military construction"]),
    ("basing.agreement", ["us anlasmasi", "basing agreement", "base agreement", "us kullanim"]),
    ("procurement.delivery", ["teslimat", "delivery", "teslim edildi", "delivered"]),
    ("procurement.announcement", ["tedarik aciklamasi", "ihracat onayi", "export approval", "procurement announcement"]),
    ("procurement.contract", ["tedarik", "procurement", "silah alimi", "alim sozlesmesi", "contract", "sozlesme", "arms deal"]),
    ("test.missile", ["fuze testi", "missile test", "fuze denemesi", "test launch"]),
    ("test.weapon", ["silah testi", "weapon test"]),
    ("kinetic.drone-strike", ["iha saldiri", "siha saldiri", "drone strike", "insansiz hava araci saldiri", "uav strike"]),
    ("kinetic.airstrike", ["hava saldirisi", "airstrike", "air strike", "hava bombardimani"]),
    ("kinetic.missile-strike", ["fuze saldirisi", "missile strike", "roket saldirisi", "rocket attack"]),
    ("kinetic.shelling", ["topcu", "shelling", "havan", "artillery fire", "mortar"]),
    ("kinetic.clash", ["catisma", "clash", "armed clash", "musademe"]),
    ("kinetic.attack", ["eyp", "ied", "intihar saldirisi", "suicide attack", "bombali saldiri"]),
    ("air.intercept", ["onleme", "intercept", "it dalasi", "dogfight"]),
    ("air.airspace-incident", ["hava sahasi", "airspace", "fir ihlali", "fir violation"]),
    ("air.activity", ["hava faaliyeti", "air activity", "kesif ucusu", "isr flight", "devriye ucusu"]),
    ("maritime.navtex", ["navtex", "notam", "atis duyurusu", "firing notice"]),
    ("maritime.incident", ["deniz olayi", "maritime incident", "karasulari ihlal", "taciz", "carpisma"]),
    ("maritime.activity", ["deniz faaliyeti", "naval activity", "savas gemisi", "warship", "denizalti", "sismik arastirma"]),
    ("cyber.incident", ["siber", "cyber"]),
    ("diplomatic.agreement", ["savunma anlasmasi", "defence agreement", "defense agreement", "mutabakat", "memorandum"]),
    ("diplomatic.statement", ["aciklama", "statement", "beyanat", "announcement"]),
    ("policy.defense", ["savunma politikasi", "defence policy", "defense policy", "butce", "doktrin", "budget"]),
]

# "Your confidence" is the submitter's, not an assessment of the sources.  It is therefore mapped to a
# deliberately pessimistic Admiralty credibility; only a reviewer may raise it to 3 or better.
CONFIDENCE_CREDIBILITY = [("yuksek", 4), ("high", 4), ("orta", 5), ("medium", 5),
                          ("dusuk", 6), ("low", 6), ("emin degilim", 6), ("not sure", 6)]

ARCHIVE_SERVICES = [("wayback", ["web.archive.org"]),
                    ("archive-today", ["archive.today", "archive.ph", "archive.is", "archive.li",
                                       "archive.md", "archive.vn", "archive.fo"]),
                    ("ghostarchive", ["ghostarchive.org"])]
WAYBACK_STAMP = re.compile(r"/web/(\d{14})")
BRANCH_RE = re.compile(r"^record/issue-\d+$")
TITLE_PREFIX = re.compile(r"^\s*\[\s*veri\s*/\s*data\s*\]\s*", re.IGNORECASE)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Rejected(Exception):
    """The submission cannot become a draft record without a human fixing something first."""

    def __init__(self, reason: str, details: list[str] | None = None, block: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or []
        self.block = block  # preformatted output (validator errors), shown in a fenced code block


# --- text helpers

def fold(text: str) -> str:
    """Casefold and strip accents so that 'Bölge' and 'BOLGE' match the same heading/keyword."""
    text = unicodedata.normalize("NFKD", (text or "").replace("İ", "i").replace("ı", "i"))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()


def clean(text: str) -> str:
    """Strip control characters and normalise newlines; keeps the submitter's words otherwise."""
    return CONTROL.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def one_line(text: str, limit: int = 300) -> str:
    collapsed = re.sub(r"\s+", " ", clean(text)).strip()
    return collapsed[: limit - 1] + "…" if len(collapsed) > limit else collapsed


def md_code(text: str, limit: int = 200) -> str:
    """Untrusted text inside a Markdown code span: no backticks, no newlines, no HTML break-out."""
    return "`" + (one_line(text, limit).replace("`", "'") or "—") + "`"


def md_quote(text: str, limit: int = 4000) -> str:
    """Untrusted text as a blockquote: every line prefixed, so it cannot inject headings or HTML."""
    body = clean(text)
    body = (body[:limit] + "\n… (kısaltıldı / truncated)") if len(body) > limit else (body or "—")
    return "\n".join("> " + line.replace("<", "&lt;").replace(">", "&gt;") for line in body.split("\n"))


# --- issue form parsing

def parse_issue_form(body: str) -> dict[str, str]:
    """Split a rendered issue-form body into {field id: value}.

    GitHub renders each form field as `### <label>` followed by the value; `render: text` textareas
    arrive inside a fenced code block, and empty optional fields as `_No response_`.
    """
    sections: list[tuple[str, list[str]]] = []
    for line in clean(body).split("\n"):
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if heading:
            sections.append((heading.group(1), []))
        elif sections:
            sections[-1][1].append(line)

    fields: dict[str, str] = {}
    for heading, lines in sections:
        folded = fold(heading)
        for fid, prefix in FIELD_HEADINGS.items():
            if fid not in fields and folded.startswith(fold(prefix)):
                fields[fid] = strip_fence("\n".join(lines).strip())
                break
    return {k: ("" if fold(v) in NO_RESPONSE else v) for k, v in fields.items()}


def strip_fence(value: str) -> str:
    """Unwrap the ``` fence that `render: text` textareas are rendered in."""
    lines = value.split("\n")
    if lines and lines[0].lstrip().startswith("```"):
        end = next((i for i in range(len(lines) - 1, 0, -1) if lines[i].strip().startswith("```")), len(lines))
        lines = lines[1:end]
    return "\n".join(lines).strip()


def checked_boxes(value: str) -> tuple[int, int]:
    """(checked, total) for a rendered checkboxes field."""
    boxes = re.findall(r"^\s*[-*]\s*\[([ xX])\]", value, re.MULTILINE)
    return sum(1 for b in boxes if b.lower() == "x"), len(boxes)


# --- field mapping

def vocab_codes(name: str) -> set[str]:
    doc = yaml.safe_load((VOCAB_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    return {e["code"] for e in doc.get("codes", [])}


def map_region(value: str) -> str:
    code = one_line(value).strip().strip(".").lower()
    known = vocab_codes("regions")
    if code not in known:
        raise Rejected(f"the region {md_code(value)} is not a code in `vocab/regions.yaml`",
                       ["Pick one of the dropdown values, or open a vocabulary proposal for a new region code."])
    return code


def map_event_type(value: str) -> tuple[str, bool]:
    """(code, matched).  Unrecognised free text becomes `other` rather than a guessed code."""
    known = vocab_codes("event-types")
    text = fold(value)
    if text in known:
        return text, True
    for code, hints in EVENT_TYPE_HINTS:
        if code in known and any(h in text for h in hints):
            return code, True
    return "other", False


def map_time(value: str) -> tuple[str, str]:
    """(time.start, time.precision).  A bare date stays day-precision at 00:00Z."""
    text = re.sub(r"\s+", "", one_line(value, 64)).rstrip(".")
    date_only = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", text)
    if date_only:
        return f"{date_only.group(1)}T00:00Z", "day"
    stamp = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T?(\d{2}):(\d{2})(?::\d{2})?(?:Z|\+00:?00)?", text)
    if stamp:
        return f"{stamp.group(1)}T{stamp.group(2)}:{stamp.group(3)}Z", "minute"
    raise Rejected(f"the time {md_code(value)} is not an ISO 8601 UTC date or date-time",
                   ["Expected `2026-09-12` or `2026-09-12T14:30Z`.",
                    "Edit the issue (or say the right time in a comment) and re-apply the label."])


def map_confidence(value: str) -> int:
    text = fold(value)
    for hint, credibility in CONFIDENCE_CREDIBILITY:
        if hint in text:
            return credibility
    return 6


def source_lang(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    return "tr" if host.endswith(".tr") else "en"


def archive_service(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    for service, hosts in ARCHIVE_SERVICES:
        if any(host == h or host.endswith("." + h) for h in hosts):
            return service
    return "other"


def urls(value: str) -> list[str]:
    found = []
    for line in clean(value).split("\n"):
        token = line.strip().strip("<>").strip().rstrip(",;")
        token = re.sub(r"^[-*+]\s+", "", token).strip()
        if re.match(r"^https?://\S+$", token):
            found.append(token)
    return found


def map_sources(source_value: str, archive_value: str) -> tuple[list[dict], bool]:
    """(sources[], archives_paired).  Archives are attached only when the counts line up exactly."""
    links = urls(source_value)
    if not links:
        raise Rejected("no usable source URL was found, and every record needs at least one source",
                       ["At least one source is a red line; a record is never written without one.",
                        "Put one `https://` link per line in the *Kaynak bağlantıları / Source URLs* field."])
    sources = [{"url": u, "lang": source_lang(u)} for u in links]
    archives = urls(archive_value)
    paired = bool(archives) and len(archives) == len(links)
    if paired:
        for source, archive in zip(sources, archives):
            entry = {"service": archive_service(archive), "url": archive}
            stamp = WAYBACK_STAMP.search(archive)
            if stamp:
                d = stamp.group(1)
                entry["captured_at"] = f"{d[0:4]}-{d[4:6]}-{d[6:8]}T{d[8:10]}:{d[10:12]}:{d[12:14]}Z"
            source["archives"] = [entry]
    return sources, paired


def map_title(issue_title: str, what_tr: str) -> str:
    title = one_line(TITLE_PREFIX.sub("", clean(issue_title)), 200)
    if not title:
        title = one_line(clean(what_tr).split("\n")[0].split(". ")[0], 200)
    if not title:
        raise Rejected("the issue has no usable title",
                       ["Give the issue a short, neutral headline and re-apply the label."])
    return title


# --- record assembly

def build_record(issue: dict, record_id: str) -> tuple[dict, list[str]]:
    """Map an issue-form submission onto an event record.  Returns (record, reviewer notes)."""
    fields = parse_issue_form(issue.get("body") or "")
    if not fields:
        raise Rejected("the issue body does not look like a data-submission form",
                       ["Only issues opened with `01-data-submission.yml` can be turned into a record.",
                        "Write the record by hand with `python tools/gt.py new event` instead."])

    checked, total = checked_boxes(fields.get("red_lines", ""))
    if total and checked < total:
        raise Rejected(f"only {checked} of {total} red-line confirmations are ticked",
                       ["All red-line boxes are required before a submission can become a record.",
                        "Anything involving Turkish forces, personal data or leaked material goes through "
                        "[SECURITY.md](https://github.com/Greater-Turkiye/.github/blob/main/SECURITY.md), not this form."])

    notes: list[str] = []
    start, precision = map_time(fields.get("when_utc", ""))
    event_type, matched = map_event_type(fields.get("event_type", ""))
    sources, paired = map_sources(fields.get("source_urls", ""), fields.get("archive_urls", ""))

    record: dict = {
        "id": record_id,
        "schema": "event/1",
        "event_type": event_type,
        "title": {"tr": map_title(issue.get("title") or "", fields.get("what_tr", ""))},
        "time": {"start": start, "precision": precision, "basis": "reported"},
        "regions": [map_region(fields.get("region", ""))],
        "sources": sources,
        "assessment": {"status": "unverified", "credibility": map_confidence(fields.get("confidence", ""))},
    }

    summary_tr = clean(fields.get("what_tr", ""))
    if summary_tr:
        record["summary"] = {"tr": summary_tr}
        summary_en = clean(fields.get("what_en", ""))
        if summary_en:
            record["summary"]["en"] = summary_en
        else:
            notes.append("`summary.en` is missing — English is required before publication.")
    place = one_line(fields.get("place", ""), 200)
    if place:
        record["location"] = {"precision": "locality", "method": "reported", "place_name": {"tr": place}}
        notes.append("`location.precision` is a default of `locality` — set what the source actually pins down. "
                     "No coordinates were generated; add them only from a geolocated source.")

    if not matched:
        raw = fields.get("event_type", "")
        notes.append(f"`event_type` could not be mapped and fell back to `other` (submitter wrote {md_code(raw)}) — "
                     "pick a code from `vocab/event-types.yaml` or open a vocabulary proposal.")
    if not paired:
        notes.append("Archive links were missing or did not line up one-to-one with the sources, so none were "
                     "attached — archive every source with `https://web.archive.org/save/<url>`.")
    notes.append("Source `lang` was guessed from the domain (`.tr` → `tr`, otherwise `en`) — correct it if wrong.")
    notes.append("No `sources[].ref` was set: register each outlet with `python tools/gt.py new source` and link it.")
    notes.append("`countries`, `actors`, `equipment`, `sites` and `claims` were left out on purpose — "
                 "a machine must not infer who was involved. Contested characterisations belong in `claims[]`, attributed.")
    notes.append("`assessment.credibility` starts pessimistic (it only reflects the submitter's own confidence) "
                 "and `assessment.status` is `unverified`; raise them only after checking the sources yourself.")
    return record, notes


def header(issue: dict, label: str) -> str:
    return "\n".join([
        "# Taslak kayıt — insan incelemesi gerektirir. / Draft record — needs human review.",
        f"# Kaynak / Provenance: {issue.get('html_url', '')} "
        f"(issue #{issue.get('number')}, label: {label})",
        "# Otomatik oluşturuldu / Generated by .github/workflows/record-from-issue.yml;",
        "# doğrulama bir insana aittir. / verification is a human decision.",
        "",
    ])


def run_gt(*args: str) -> subprocess.CompletedProcess:
    """Invoke tools/gt.py with an argument list — never a shell string."""
    return subprocess.run([sys.executable, str(GT), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def create_record_file(record: dict, issue: dict, label: str) -> Path:
    """`gt.py new event` picks the ID and the path; we only fill the skeleton in."""
    created = run_gt("new", "event")
    if created.returncode != 0:
        raise RuntimeError(f"gt.py new event failed: {created.stdout}{created.stderr}")
    path = ROOT / created.stdout.strip().splitlines()[-1].strip()
    record["id"] = path.stem
    body = yaml.safe_dump(record, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000)
    path.write_text(header(issue, label) + body, encoding="utf-8", newline="\n")
    return path


# --- outputs for the workflow

def pr_body(issue: dict, path: Path, record: dict, notes: list[str], repo: str) -> str:
    number = issue.get("number")
    checklist = [
        "The record says only what the sources say, in the project's own words (nothing copied).",
        "`title.tr` is a neutral headline and `title.en` / `summary.en` exist before publication.",
        "`time.start` and `time.precision` match the sources; the time is UTC.",
        "`event_type` and `regions` are the right vocabulary codes.",
        "Every source resolves, is not a shortener, and has an archive link.",
        "Actors, equipment, sites and countries are added and reference real records.",
        "Contested characterisations are in `claims[]`, attributed to whoever makes them.",
        "No Turkish force positions or movements, no personal data, no classified or leaked material.",
        "`assessment.status` / `credibility` reflect your own check, not the submitter's confidence.",
    ]
    return "\n".join([
        f"Draft record generated from #{number}. **Nothing here is verified.**",
        "",
        f"A maintainer or triager applied the `{APPROVAL_LABEL}` label to that submission; this pull request is the "
        "draft that follows from it. The decision that the record is true stays with the reviewers of this PR.",
        "",
        "| | |",
        "|---|---|",
        f"| Record | `{path.as_posix()}` |",
        f"| ID | `{record['id']}` |",
        f"| Event type | `{record['event_type']}` |",
        f"| Regions | `{', '.join(record['regions'])}` |",
        f"| Time | `{record['time']['start']}` (`{record['time']['precision']}`) |",
        f"| Sources | {len(record['sources'])} |",
        f"| Status | `{record['assessment']['status']}`, credibility `{record['assessment']['credibility']}` |",
        f"| Proposal | https://github.com/{repo}/issues/{number} |",
        "",
        "### What the generator left to you",
        "",
        *[f"- {n}" for n in notes],
        "",
        "### Reviewer checklist",
        "",
        *[f"- [ ] {c}" for c in checklist],
        "",
        "### The submitter's words, unedited",
        "",
        md_quote((issue.get("body") or "")),
        "",
        f"Closes #{number} once this is merged.",
        "",
        "> [!IMPORTANT]",
        "> A pull request opened with the workflow token does not start the `validate` check by itself. "
        "Push your review edits to this branch, or close and reopen the pull request, so that the required "
        "check reports before you merge. The same checks already ran inside the generating run — "
        "they are re-run here for the record, never skipped.",
        "",
        "---",
        "",
        f"Generated by [`.github/workflows/record-from-issue.yml`](https://github.com/{repo}/blob/main/"
        ".github/workflows/record-from-issue.yml). A machine never decides that something is true; "
        "this draft needs a human review and the `validate` check before it can be merged.",
    ])


def commit_message(issue: dict, record: dict) -> str:
    return "\n".join([
        f"Data: draft event record from issue #{issue.get('number')}",
        "",
        f"Generated by .github/workflows/record-from-issue.yml after the `{APPROVAL_LABEL}` label was",
        f"applied to #{issue.get('number')}. Unverified draft: the fields the form did not state are left",
        "empty for a human reviewer.",
        "",
        f"Record: {record['id']} ({record['event_type']}, {', '.join(record['regions'])})",
        f"Proposal: {issue.get('html_url', '')}",
    ])


def rejection_comment(issue: dict, rejected: Rejected, run_url: str) -> str:
    return "\n".join([
        "### Bu öneri kayda dönüştürülemedi / This proposal could not be turned into a record",
        "",
        f"**Sebep / Reason:** {rejected.reason}",
        "",
        *[f"- {d}" for d in rejected.details],
        *(["", "```", rejected.block.replace("```", "''' "), "```"] if rejected.block else []),
        "",
        "Kayıt oluşturulmadı ve hiçbir dal veya pull request açılmadı; kontroller kaldırılmaz. / "
        "No record was created and no branch or pull request was opened; the checks are never stripped.",
        "",
        f"Düzeltip `{APPROVAL_LABEL}` etiketini yeniden uygulayın. / Fix the submission and re-apply the "
        f"`{APPROVAL_LABEL}` label." + (f"\n\n[Workflow run]({run_url})" if run_url else ""),
    ])


def write_outputs(pairs: dict[str, str]) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as f:
        for key, value in pairs.items():
            f.write(f"{key}={value}\n")


# --- orchestration

def eligible(event: dict) -> str | None:
    """None when the event should produce a record, otherwise the reason to skip."""
    issue = event.get("issue") or {}
    if issue.get("pull_request"):
        return "the labelled item is a pull request, not an issue"
    if (event.get("label") or {}).get("name") != APPROVAL_LABEL:
        return f"the label is not `{APPROVAL_LABEL}`"
    labels = {lbl.get("name") for lbl in issue.get("labels") or []}
    if SUBMISSION_LABEL not in labels:
        return f"the issue does not carry the `{SUBMISSION_LABEL}` label"
    if issue.get("state") != "open":
        return "the issue is closed"
    return None


def process(event: dict, out_dir: Path) -> dict[str, str]:
    issue = event.get("issue") or {}
    repo = (event.get("repository") or {}).get("full_name") or os.environ.get("GITHUB_REPOSITORY", "")
    run_url = os.environ.get("RUN_URL", "")
    label = (event.get("label") or {}).get("name", APPROVAL_LABEL)

    try:
        record, notes = build_record(issue, record_id="")
        path = create_record_file(record, issue, label)
    except Rejected as e:
        (out_dir / "rejection.md").write_text(rejection_comment(issue, e, run_url), encoding="utf-8", newline="\n")
        print(f"rejected: {e.reason}")
        return {"status": "rejected"}

    fmt = run_gt("fmt")
    print(fmt.stdout, fmt.stderr, sep="\n")
    validate = run_gt("validate")
    print(validate.stdout, validate.stderr, sep="\n")
    if fmt.returncode != 0 or validate.returncode != 0:
        # Fail closed: the record is thrown away, nothing is pushed, and the checks are never weakened.
        # Only the per-record findings, not the "N records, M errors" summary lines.  gt.py names the
        # offending field, never its value, so nothing new is published back onto the issue.
        lines = [l for l in (fmt.stdout + "\n" + validate.stdout).splitlines()
                 if l.startswith("ERROR") or l.startswith("::error")]
        gate = any(k in l for l in lines for k in ("TUR forces gate", "personal data", "classification"))
        details = ["Kayıt atıldı; hiçbir şey itilmedi. / The draft was thrown away; nothing was pushed."]
        if gate:
            details.append("Bu bir kırmızı çizgidir; kontrol asla devre dışı bırakılmaz. / This is a red line; "
                           "the gate is never disabled. Turkish forces, personal data and classified material "
                           "are reported through [SECURITY.md](https://github.com/Greater-Turkiye/.github/blob/"
                           "main/SECURITY.md), not through a public issue.")
        path.unlink(missing_ok=True)
        rejected = Rejected("the generated record did not pass `python tools/gt.py validate`", details,
                            block="\n".join(lines[:20] or ["(see the workflow run for the full output)"]))
        (out_dir / "rejection.md").write_text(rejection_comment(issue, rejected, run_url), encoding="utf-8", newline="\n")
        return {"status": "rejected"}

    rel = path.relative_to(ROOT)
    branch = f"record/issue-{int(issue.get('number'))}"
    assert BRANCH_RE.match(branch), branch
    (out_dir / "pr_body.md").write_text(pr_body(issue, rel, record, notes, repo), encoding="utf-8", newline="\n")
    (out_dir / "commit_msg.txt").write_text(commit_message(issue, record), encoding="utf-8", newline="\n")
    return {"status": "ok", "branch": branch, "record_path": rel.as_posix(), "record_id": record["id"],
            "region": record["regions"][0],
            "pr_title": f"Data: draft record from issue #{int(issue.get('number'))}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"),
                        help="path to the GitHub event payload (never the issue text itself)")
    parser.add_argument("--out-dir", default=".", help="where to write pr_body.md / commit_msg.txt / rejection.md")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not args.event:
        print("no event payload: pass --event or set GITHUB_EVENT_PATH", file=sys.stderr)
        return 1

    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    skip = eligible(event)
    if skip:
        print(f"skipped: {skip}")
        write_outputs({"status": "skipped"})
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = process(event, out_dir)
    write_outputs(outputs)
    print(f"status={outputs['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
