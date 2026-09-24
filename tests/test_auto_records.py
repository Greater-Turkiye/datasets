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


def fake_translate(url, token=None, timeout=90):
    return {"responseStatus": 200, "quotaFinished": False, "responseData": {"translatedText": "Çevrilmiş başlık"}}


def test_a_record_says_what_it_is():
    AR.http_json = fake_translate
    AR.PAUSE = 0
    cl = AR.cluster(AR.candidates(batch(row("Cargo ship attacked in Black Sea, captain killed", "https://e.org/s"))))
    rec = AR.record(cl[0], AR.titles(cl[0]), set(AR.vocab_codes()))
    assert rec["assessment"]["status"] == "unverified" and rec["assessment"]["credibility"] == 6
    assert rec["event_type"] == "maritime.incident" and rec["regions"] == ["black-sea"]
    assert rec["title"] == {"tr": "Çevrilmiş başlık", "en": "Cargo ship attacked in Black Sea, captain killed"}
    assert rec["i18n"] == {"source": "en", "machine": ["tr"]}
    assert rec["tags"] == ["otomatik"] and "ADR 0023" in rec["assessment"]["note"]["tr"]
    assert "summary" not in rec  # the excerpt is the source's text, not ours


def test_a_quota_stops_translation_instead_of_writing_a_wrong_language():
    AR.http_json = lambda url, token=None, timeout=90: {"responseStatus": 429, "quotaFinished": True}
    cl = AR.cluster(AR.candidates(batch(row("Cargo ship attacked", "https://e.org/s"))))
    try:
        AR.titles(cl[0])
    except AR.TranslationUnavailable:
        pass
    else:
        raise AssertionError("expected TranslationUnavailable")


def test_the_headline_decides_the_type_before_the_keyword_match():
    codes = set(AR.vocab_codes())
    kinds = {
        "G7 Statement on Bab al-Mandab and Navigational Rights": "diplomatic.statement",
        "Croatia Buys South Korean Rocket Launchers": "procurement.contract",
        "Telephone conversation with President of Kazakhstan": "diplomatic.talks",
        "Hague Court Convicts Kosovo Liberation Army Leaders": "other",
        "Russia strikes Kramatorsk with Uragan MLRS, injuring nine": "kinetic.shelling",
        "Russians drop guided glide bombs on Sumy": "kinetic.airstrike",
    }
    for title, want in kinds.items():
        c = AR.candidates(batch(row(title, "https://e.org/x", topics=("kinetic.attack",))))[0]
        assert AR.classify(c, codes) == want, title


def test_analysis_is_not_recorded():
    for title, feed in (("Could the GCC and Iran solve the Strait of Hormuz crisis?", "rss-x"),
                        ("Democracy Digest: Hungary opens a debate", "rss-x"),
                        ("Strait talk podcast: Yemen and the Iran war", "rss-x"),
                        ("Russia's bombing campaign is terrorizing schoolchildren", "rss-usa-atlanticcouncil")):
        c = AR.candidates(batch(row(title, "https://e.org/x", feed=feed)))[0]
        assert AR.refused(c) == "analysis, not an occurrence", title


def test_the_note_carries_no_long_digit_runs_the_policy_would_flag():
    import re
    cl = AR.cluster(AR.candidates(batch(row("Cargo ship attacked", "https://e.org/s"))))
    t = {"tr": "a", "en": "a", "i18n": {"source": "en", "machine": ["tr"]}}
    note = AR.record(cl[0], t, set())["assessment"]["note"]
    assert not re.search(r"\d{8,}", note["tr"] + note["en"])


def test_a_named_casualty_waits_for_a_person():
    for title in ("Ministry of Defence confirms the death of Major Paul Wilks",
                  "Tribute to Sergeant Jane Doe, killed in a training accident"):
        c = AR.candidates(batch(row(title, "https://e.org/x")))[0]
        assert AR.refused(c) == "names a casualty", title


def test_opinion_headlines_are_analysis():
    c = AR.candidates(batch(row("Kosovo Verdict Reflects West's Strategic Priorities", "https://e.org/x",
                                region="balkans")))[0]
    assert AR.refused(c) == "analysis, not an occurrence"


def test_a_town_in_the_headline_places_the_event_at_city_scale():
    c = AR.candidates(batch(row("Russia strikes Kramatorsk with Uragan MLRS, injuring nine", "https://e.org/k")))[0]
    loc = AR.locate(c)
    assert loc["place_name"]["en"] == "Kramatorsk" and loc["precision"] == "locality"
    assert loc["method"] == "inferred" and loc["uncertainty_m"] == 20_000


def test_a_province_is_placed_at_province_scale():
    c = AR.candidates(batch(row("Russian attack hits shopping center in Odesa region", "https://e.org/o")))[0]
    loc = AR.locate(c)
    assert loc["precision"] == "admin1" and loc["uncertainty_m"] == 100_000
    assert loc["place_name"]["en"].endswith(" region")


def test_a_person_named_like_a_town_is_not_placed_there():
    c = AR.candidates(batch(row("Telephone conversation with President of Turkmenistan Serdar Berdimuhamedov",
                                "https://e.org/t", region="central-asia")))[0]
    assert AR.locate(c) is None


def test_no_place_in_turkiye_is_in_the_gazetteer():
    import json
    places = json.loads(AR.PLACES_FILE.read_text(encoding="utf-8"))["places"]
    assert places and not any(p["a"] == "TUR" for p in places)
