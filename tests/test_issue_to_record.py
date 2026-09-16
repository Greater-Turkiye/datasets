"""Tests for tools/issue_to_record.py — the issue form -> draft record mapping.

End-to-end tests run against a throwaway copy of the repo (schemas/, vocab/, policy.yaml, tools/) under
tmp_path, so real records are never touched.  Run:  python -m pytest tests

The fixtures in tests/fixtures/issues/ are rendered bodies of `01-data-submission.yml` issues, including
one full of shell metacharacters and one that the policy gate must reject.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "issues"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ITR = load_module(REPO / "tools" / "issue_to_record.py", "itr_real")  # pure functions only


def body(name: str) -> str:
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def event(name: str, *, title: str = "[Veri / Data] Ege'de bildirilen önleme", number: int = 42,
          label: str = "kayda-gec", labels: tuple[str, ...] = ("data-submission", "triage"),
          state: str = "open", pull_request: bool = False) -> dict:
    issue = {"number": number, "title": title, "body": body(name), "state": state,
             "labels": [{"name": n} for n in labels],
             "html_url": f"https://github.com/Greater-Turkiye/datasets/issues/{number}"}
    if pull_request:
        issue["pull_request"] = {"url": "https://api.github.com/x"}
    return {"action": "labeled", "label": {"name": label}, "issue": issue,
            "repository": {"full_name": "Greater-Turkiye/datasets"}}


class Repo:
    """A throwaway copy of the repo with its own copy of tools/issue_to_record.py."""

    def __init__(self, root: Path, name: str) -> None:
        self.root = root
        self.itr = load_module(root / "tools" / "issue_to_record.py", name)

    def run(self, payload: dict, out: Path | None = None) -> tuple[dict, Path]:
        out = out or self.root / "out"
        out.mkdir(parents=True, exist_ok=True)
        return self.itr.process(payload, out), out

    def record(self) -> tuple[Path, dict]:
        paths = sorted((self.root / "data").rglob("*.yaml"))
        assert len(paths) == 1, paths
        gt = load_module(self.root / "tools" / "gt.py", f"gt_for_{self.root.name}")
        return paths[0], yaml.load(paths[0].read_text(encoding="utf-8"), Loader=gt.Loader)


@pytest.fixture
def repo(tmp_path):
    for d in ("schemas", "vocab", "tools"):
        shutil.copytree(REPO / d, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "policy.yaml", tmp_path / "policy.yaml")
    name = f"itr_tmp_{id(tmp_path)}"
    yield Repo(tmp_path, name)
    sys.modules.pop(name, None)


# --- form parsing

def test_parse_complete_form_fields():
    fields = ITR.parse_issue_form(body("complete"))
    assert set(fields) == {"what_tr", "what_en", "when_utc", "region", "place", "event_type",
                           "source_urls", "archive_urls", "confidence", "notes", "red_lines"}
    assert fields["when_utc"] == "2026-09-12T14:30Z"
    assert fields["region"] == "aegean"
    assert fields["place"] == "Limni / Lemnos"
    assert fields["what_tr"].startswith("Yunan Genelkurmay Başkanlığı (GEETHA)")
    assert fields["source_urls"].splitlines() == [
        "https://www.kathimerini.gr/politics/defence/2026/09/12/aegean-intercept-report/",
        "https://www.ekathimerini.com/news/2026/09/12/aegean-intercept-report-en/"]  # the ``` fence is stripped


def test_optional_fields_rendered_as_no_response_become_empty():
    fields = ITR.parse_issue_form(body("minimal"))
    assert fields["what_en"] == "" and fields["place"] == "" and fields["event_type"] == ""
    assert fields["archive_urls"] == "" and fields["notes"] == ""


def test_checkboxes_are_read_only_from_the_red_lines_field():
    fields = ITR.parse_issue_form(body("malicious"))
    assert ITR.checked_boxes(fields["red_lines"]) == (4, 4)
    assert ITR.checked_boxes(fields["what_tr"]) == (0, 1)  # the fake checklist in the summary is not authority


def test_a_body_that_is_not_the_form_is_rejected():
    with pytest.raises(ITR.Rejected, match="does not look like a data-submission form"):
        ITR.build_record({"title": "x", "body": "just some free text\n\nand a link"}, "evt_x")


# --- field mapping

@pytest.mark.parametrize("text, expected, matched", [
    ("hava sahası olayı / airspace incident", "air.airspace-incident", True),
    ("deniz tatbikatı", "exercise.naval", True),
    ("tatbikat / exercise", "exercise.military", True),
    ("çok uluslu tatbikat", "exercise.multinational", True),
    ("konuşlanma / deployment", "deployment.announced", True),
    ("çekilme", "deployment.withdrawal", True),
    ("SİHA saldırısı", "kinetic.drone-strike", True),
    ("hava saldırısı", "kinetic.airstrike", True),
    ("NAVTEX yayını", "maritime.navtex", True),
    ("silah alımı sözleşmesi", "procurement.contract", True),
    ("teslimat", "procurement.delivery", True),
    ("savunma anlaşması", "diplomatic.agreement", True),
    ("procurement.contract", "procurement.contract", True),  # an exact vocabulary code is taken as-is
    ("", "other", False),
    ("bilmiyorum, garip bir şey", "other", False),
])
def test_event_type_mapping(text, expected, matched):
    assert ITR.map_event_type(text) == (expected, matched)


def test_event_type_never_leaves_the_vocabulary():
    codes = ITR.vocab_codes("event-types")
    for text in ["tatbikat", "$(reboot)", "air.no-such-type", "", "deniz olayı & $(reboot)"]:
        assert ITR.map_event_type(text)[0] in codes


@pytest.mark.parametrize("text, start, precision", [
    ("2026-09-12", "2026-09-12T00:00Z", "day"),
    ("2026-09-12T14:30Z", "2026-09-12T14:30Z", "minute"),
    ("2026-09-12T14:30:05Z", "2026-09-12T14:30Z", "minute"),
    ("2026-09-12 14:30", "2026-09-12T14:30Z", "minute"),
    ("2026-09-12T14:30+00:00", "2026-09-12T14:30Z", "minute"),
])
def test_time_mapping(text, start, precision):
    assert ITR.map_time(text) == (start, precision)


@pytest.mark.parametrize("text", ["", "geçen hafta", "12/09/2026", "$(date)", "2026-09-12T14:30 EEST"])
def test_unparseable_time_is_rejected(text):
    with pytest.raises(ITR.Rejected, match="ISO 8601"):
        ITR.map_time(text)


def test_region_must_be_a_vocabulary_code():
    assert ITR.map_region("aegean") == "aegean"
    with pytest.raises(ITR.Rejected, match="vocab/regions.yaml"):
        ITR.map_region("atlantis")


@pytest.mark.parametrize("text, credibility", [
    ("Yüksek — resmî açıklama veya görsel doğrulama / High — official statement or visual verification", 4),
    ("Orta — birden fazla bağımsız kaynak / Medium — several independent sources", 5),
    ("Düşük — tek kaynak veya doğrulanmamış / Low — single or unverified source", 6),
    ("Emin değilim / Not sure", 6),
    ("", 6),
])
def test_confidence_never_maps_better_than_four(text, credibility):
    assert ITR.map_confidence(text) == credibility


def test_sources_pair_with_archives_and_parse_the_wayback_timestamp():
    fields = ITR.parse_issue_form(body("complete"))
    sources, paired = ITR.map_sources(fields["source_urls"], fields["archive_urls"])
    assert paired and len(sources) == 2
    assert sources[0]["archives"] == [{"service": "wayback", "captured_at": "2026-09-13T08:15:00Z",
                                       "url": "https://web.archive.org/web/20260913081500/"
                                              "https://www.kathimerini.gr/politics/defence/2026/09/12/"
                                              "aegean-intercept-report/"}]
    assert sources[1]["archives"] == [{"service": "archive-today", "url": "https://archive.ph/abc12"}]
    assert [s["lang"] for s in sources] == ["en", "en"]


def test_mismatched_archive_count_attaches_nothing():
    sources, paired = ITR.map_sources("https://a.example.org/1\nhttps://b.example.org/2",
                                      "https://web.archive.org/web/20260101000000/https://a.example.org/1")
    assert not paired and all("archives" not in s for s in sources)


def test_source_lang_guessed_from_the_domain():
    sources, _ = ITR.map_sources("https://www.aa.com.tr/x\nhttps://www.reuters.com/y", "")
    assert [s["lang"] for s in sources] == ["tr", "en"]


def test_a_submission_without_a_source_is_rejected():
    with pytest.raises(ITR.Rejected, match="at least one source"):
        ITR.map_sources("kaynak yok / no source", "")


def test_title_falls_back_to_the_summary_when_the_prefix_is_all_there_is():
    assert ITR.map_title("[Veri / Data] Ege'de önleme", "x") == "Ege'de önleme"
    assert ITR.map_title("[Veri / Data] ", "İlk cümle. İkinci cümle.") == "İlk cümle"
    with pytest.raises(ITR.Rejected, match="no usable title"):
        ITR.map_title("[Veri / Data]", "")


# --- eligibility

@pytest.mark.parametrize("kwargs, reason", [
    ({"label": "triage"}, "not `kayda-gec`"),
    ({"labels": ("bug",)}, "`data-submission`"),
    ({"state": "closed"}, "closed"),
    ({"pull_request": True}, "pull request"),
])
def test_ineligible_events_are_skipped(kwargs, reason):
    assert reason in ITR.eligible(event("complete", **kwargs))


def test_an_approved_data_submission_is_eligible():
    assert ITR.eligible(event("complete")) is None


# --- end to end: a draft record, validated by the real gates

def test_complete_submission_becomes_a_validated_draft(repo):
    outputs, out = repo.run(event("complete"))
    assert outputs["status"] == "ok"
    assert outputs["branch"] == "record/issue-42"
    path, record = repo.record()

    assert record["schema"] == "event/1"
    assert record["id"] == path.stem and path.parent.match("data/events/*/*")
    assert record["event_type"] == "air.airspace-incident"
    assert record["regions"] == ["aegean"]
    assert record["time"] == {"start": "2026-09-12T14:30Z", "precision": "minute", "basis": "reported"}
    assert record["title"]["tr"] == "Ege'de bildirilen önleme"
    assert record["summary"]["tr"].startswith("Yunan Genelkurmay Başkanlığı")
    assert record["summary"]["en"].startswith("The Hellenic National Defence General Staff")
    assert record["location"] == {"precision": "locality", "method": "reported",
                                  "place_name": {"tr": "Limni / Lemnos"}}
    assert "geometry" not in record["location"]  # coordinates are never invented
    assert len(record["sources"]) == 2 and all(s["archives"] for s in record["sources"])
    # a machine never decides that something is true
    assert record["assessment"] == {"status": "unverified", "credibility": 5}
    # nothing was inferred about who was involved
    assert not any(k in record for k in ("countries", "actors", "equipment", "sites", "claims"))

    header = path.read_text(encoding="utf-8").splitlines()[:4]
    assert any("issues/42" in line for line in header) and all(line.startswith("#") for line in header)


def test_minimal_submission_falls_back_without_inventing(repo):
    outputs, out = repo.run(event("minimal", title="[Veri / Data] Süleymaniye yakınlarında tatbikat", number=7))
    assert outputs["status"] == "ok" and outputs["branch"] == "record/issue-7"
    _, record = repo.record()
    assert record["event_type"] == "other"  # the field was empty; no code is guessed
    assert record["time"] == {"start": "2026-09-01T00:00Z", "precision": "day", "basis": "reported"}
    assert record["assessment"]["credibility"] == 6
    assert "location" not in record and "en" not in record["summary"]
    assert "archives" not in record["sources"][0]
    body_md = (out / "pr_body.md").read_text(encoding="utf-8")
    assert "fell back to `other`" in body_md and "Archive links were missing" in body_md


def test_the_draft_passes_fmt_and_validate(repo):
    repo.run(event("complete"))
    for args in (["validate"], ["fmt", "--check"]):
        done = subprocess.run([sys.executable, str(repo.root / "tools" / "gt.py"), *args],
                              cwd=repo.root, capture_output=True, text=True, encoding="utf-8")
        assert done.returncode == 0, done.stdout + done.stderr


# --- untrusted input

def test_shell_metacharacters_are_data_not_commands(repo):
    outputs, out = repo.run(event("malicious", title="[Veri / Data] $(whoami) & `id`", number=13))
    assert outputs["status"] == "ok"
    path, record = repo.record()

    assert record["event_type"] == "maritime.incident"  # mapped from the words, not from `$(reboot)`
    assert "$(whoami)" in record["summary"]["tr"]
    assert "${{ secrets.GITHUB_TOKEN }}" in record["summary"]["tr"]
    assert "rm -rf /" in record["summary"]["tr"]
    assert record["location"]["place_name"]["tr"].startswith("`$(hostname)`")
    # the YAML injected into the summary stayed inside the string
    assert record["assessment"] == {"status": "unverified", "credibility": 6}
    assert "policy" not in record
    assert record["summary"]["tr"].count("\n") >= 4
    # only real links became sources; the prose line did not
    assert [s["url"] for s in record["sources"]] == ["https://www.example.org/report?q=%24%28id%29&x=1",
                                                     "https://evil.example.com/$(id)"]
    # nothing was executed: no file was created by the payload
    assert not (repo.root / "pwned").exists()
    assert sorted(p.name for p in (repo.root / "data").rglob("*.yaml")) == [path.name]


def test_untrusted_text_in_the_pull_request_body_cannot_break_out(repo):
    _, out = repo.run(event("malicious", title="[Veri / Data] `rm -rf /`", number=13))
    md = (out / "pr_body.md").read_text(encoding="utf-8")
    quoted = md.split("### The submitter's words, unedited")[1]
    assert "<script>" not in quoted and "&lt;script&gt;" in quoted
    tail = ("Closes #", "---", "Generated by [`.github/workflows")
    for line in quoted.strip().splitlines():
        assert line == "" or line.startswith(">") or line.startswith(tail), line
    assert "<script>" not in md and "<img" not in md


def test_untrusted_text_in_a_markdown_code_span_cannot_break_out():
    assert ITR.md_code("a`b`c\nd") == "`a'b'c d`"
    assert ITR.md_code("") == "`—`"
    assert ITR.md_quote("<img src=x>\nikinci satır") == "> &lt;img src=x&gt;\n> ikinci satır"


def test_gt_is_invoked_as_an_argument_list_never_through_a_shell(repo, monkeypatch):
    calls = []
    real = subprocess.run

    def spy(args, **kwargs):
        calls.append((args, kwargs))
        assert isinstance(args, list) and not kwargs.get("shell")
        return real(args, **kwargs)

    monkeypatch.setattr(repo.itr.subprocess, "run", spy)
    repo.run(event("malicious"))
    assert calls and all(a[0] == sys.executable for a, _ in calls)


# --- fail closed

def test_policy_gate_rejection_blocks_the_pull_request(repo):
    outputs, out = repo.run(event("policy-pii", title="[Veri / Data] İddia edilen olay", number=99))
    assert outputs["status"] == "rejected"
    assert "branch" not in outputs and not (out / "pr_body.md").exists()
    assert not list((repo.root / "data").rglob("*.yaml"))  # the draft was thrown away
    comment = (out / "rejection.md").read_text(encoding="utf-8")
    assert "could not be turned into a record" in comment
    assert "personal data" in comment and "classification marking" in comment and "SECURITY.md" in comment
    assert "records, 3 errors" not in comment  # only the findings, not gt.py's summary line
    assert "tanik.kisi@ornek.com" not in comment  # the validator names the field, never the value
    assert "No record was created and no branch or pull request was opened" in comment


def test_unticked_red_line_is_rejected_before_anything_is_written(repo):
    outputs, out = repo.run(event("red-lines-unticked", number=5))
    assert outputs["status"] == "rejected"
    assert not list((repo.root / "data").rglob("*.yaml"))
    comment = (out / "rejection.md").read_text(encoding="utf-8")
    assert "3 of 4 red-line confirmations are ticked" in comment and "SECURITY.md" in comment


def test_main_skips_an_unrelated_label(repo, tmp_path, capsys):
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps(event("complete", label="triage")), encoding="utf-8")
    assert repo.itr.main(["--event", str(payload), "--out-dir", str(tmp_path / "out")]) == 0
    assert "skipped" in capsys.readouterr().out
    assert not list((repo.root / "data").rglob("*.yaml"))
