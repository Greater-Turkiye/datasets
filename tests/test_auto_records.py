"""Tests for tools/auto_records.py — collector candidates -> unverified event records.

The pure parts run directly; the end-to-end test replaces the model with a stub and checks that what
comes out passes the real schema and content policy, and that the red-line net holds.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("auto_records", REPO / "tools" / "auto_records.py")
AR = importlib.util.module_from_spec(spec)
sys.modules["auto_records"] = AR
spec.loader.exec_module(AR)


def row(title, url, *, text="", region="black-sea", topics=("kinetic.drone-strike",), status="queued",
        redline=False, when="2026-09-23T08:00:00Z", feed="rss-ukr-ukrinform-war-en"):
    return {"title": title, "url": url, "text": text, "region": region, "lang": "en", "published_at": when,
            "triage_status": status, "collector_id": feed,
            "triage_labels": json.dumps({"topics": list(topics), "redline_check": redline, "feed": feed})}


def batch(*rows):
    return {"rows": list(rows), "run_url": "https://github.com/x/y/actions/runs/1"}


def test_only_queued_and_pending_rows_with_a_region_and_topic_are_candidates():
    b = batch(row("A", "https://e.org/a"), row("B", "https://e.org/b", status="pending"),
              row("C", "https://e.org/c", status="off-topic"), row("D", "https://e.org/d", region=None),
              row("E", "https://e.org/e", topics=()))
    assert [c.title for c in AR.candidates(b)] == ["A", "B"]


def test_redline_rows_never_become_candidates():
    assert AR.candidates(batch(row("A", "https://e.org/a", redline=True))) == []


def test_text_naming_turkish_forces_is_refused_in_both_languages():
    for title in ("Turkish Navy frigate joins exercise", "Türk Silahlı Kuvvetleri tatbikata katıldı",
                  "TSK unsurları bölgeye intikal etti", "Turkey's drones struck targets"):
        c = AR.candidates(batch(row(title, "https://e.org/x")))[0]
        assert AR.refused(c) == "mentions Turkish forces", title


def test_turkey_as_a_state_is_not_refused():
    c = AR.candidates(batch(row("Türkiye and Brazil ratify defence industry agreement", "https://e.org/x")))[0]
    assert AR.refused(c) is None


def test_already_cited_urls_are_skipped():
    b = batch(row("A", "http://E.org/a/"), row("B", "https://e.org/b"))
    pool, skipped = AR.select([b], {AR.norm_url("https://e.org/a")})
    assert [c.title for c in pool] == ["B"] and skipped == {"already recorded": 1}


def test_repeat_reports_of_one_event_cluster_into_one():
    b = batch(row("Russian attacks on Kyiv kill two, injure 36", "https://e.org/1"),
              row("Russian attacks on Kyiv kill two, injure 22", "https://e.org/2"),
              row("Croatia buys rocket launchers", "https://e.org/3", region="balkans"))
    clusters = AR.cluster(AR.candidates(b))
    assert [len(c.items) for c in clusters] == [2, 1]


def stub(is_event=True, region="black-sea"):
    def fake(url, token=None, data=None, timeout=90):
        import re
        ns = re.findall(r'<item n="(\d+)">', data["messages"][1]["content"])
        items = [{"i": int(n), "is_event": is_event, "dup_of": None, "event_type": "maritime.incident",
                  "region": region, "title_tr": "Karadeniz'de bir yük gemisine saldırı bildirildi",
                  "title_en": "Attack on a cargo ship reported in the Black Sea",
                  "summary_tr": "Ukrinform'a göre gemi saldırıya uğradı.",
                  "summary_en": "According to Ukrinform, the ship was attacked."} for n in ns]
        return {"choices": [{"message": {"content": json.dumps({"items": items})}}]}
    return fake


def test_model_output_becomes_a_record_that_says_what_it_is():
    cl = AR.cluster(AR.candidates(batch(row("Cargo ship attacked", "https://e.org/s"))))
    AR.http_json = stub()
    AR.PAUSE = 0
    texts = AR.write_text(cl, "t", AR.vocab_codes())
    rec = AR.record(cl[0], texts[0], set(AR.vocab_codes()))
    assert rec["assessment"]["status"] == "unverified" and rec["assessment"]["credibility"] == 6
    assert rec["event_type"] == "maritime.incident" and rec["regions"] == ["black-sea"]
    assert rec["tags"] == ["otomatik"] and rec["i18n"]["machine"] == ["tr", "en"]
    assert "ADR 0023" in rec["assessment"]["note"]["tr"]


def test_non_events_and_out_of_area_items_are_not_recorded():
    cl = AR.cluster(AR.candidates(batch(row("Podcast: the Iran war", "https://e.org/p"))))
    for kwargs in ({"is_event": False}, {"region": "none"}):
        AR.http_json = stub(**kwargs)
        texts = AR.write_text(cl, "t", AR.vocab_codes())
        assert AR.record(cl[0], texts[0], set(AR.vocab_codes())) is None


def test_an_invented_code_falls_back_to_the_collector_topic():
    cl = AR.cluster(AR.candidates(batch(row("Cargo ship attacked", "https://e.org/s"))))
    text = {"is_event": True, "event_type": "kinetic.invented", "region": "atlantis", "title_tr": "a",
            "title_en": "a", "summary_tr": "a", "summary_en": "a"}
    rec = AR.record(cl[0], text, set(AR.vocab_codes()))
    assert rec["event_type"] == "kinetic.drone-strike" and rec["regions"] == ["black-sea"]


def test_the_note_carries_no_long_digit_runs_the_policy_would_flag():
    cl = AR.cluster(AR.candidates(batch(row("Cargo ship attacked", "https://e.org/s"))))
    text = {"is_event": True, "title_tr": "a", "title_en": "a", "summary_tr": "a", "summary_en": "a"}
    import re
    note = AR.record(cl[0], text, set())["assessment"]["note"]
    assert not re.search(r"\d{8,}", note["tr"] + note["en"])
