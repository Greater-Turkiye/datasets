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


def test_strike_in_the_military_sense_is_not_a_labour_strike():
    en = "Koretskyi: Russia's massive strikes cause damage and destruction in energy sector"
    tr = "Koretskyi: Rusya'nın kitlesel grevleri enerji sektöründe hasara ve yıkıma neden oluyor"
    assert AR.military_sense(tr, en) == "Koretskyi: Rusya'nın kitlesel saldırıları enerji sektöründe hasara ve yıkıma neden oluyor"
    assert AR.military_sense("KAB grevleri iki kişiyi yaraladı", "KAB strikes wound two") == "KAB saldırıları iki kişiyi yaraladı"
    assert AR.military_sense("Grev, limanı vurdu", "Strike hits the port") == "Saldırı, limanı vurdu"
    assert AR.military_sense("Sağlayıcı grevden etkilendi", "Provider hit by a strike") == "Sağlayıcı saldırıdan etkilendi"


def test_a_real_labour_strike_stays_a_strike():
    en = "Dock workers strike over wages in Odesa port"
    tr = "Odesa limanında liman işçileri ücretler nedeniyle greve gitti"
    assert AR.military_sense(tr, en) == tr
    assert AR.military_sense("Grev sürüyor", "The walkout goes on") == "Grev sürüyor"


def test_an_eu_president_is_no_head_of_state():
    en = "Statement by President von der Leyen at the joint press conference with Albanian Prime Minister Rama"
    tr = "Cumhurbaşkanı von der Leyen'in Arnavutluk Başbakanı Rama ile ortak basın toplantısında yaptığı açıklama"
    assert AR.office_sense(tr, en) == "AB Komisyonu Başkanı von der Leyen'in Arnavutluk Başbakanı Rama ile ortak basın toplantısında yaptığı açıklama"
    assert AR.office_sense("Cumhurbaşkanı António Costa Kiev'de", "President António Costa in Kyiv") == "Avrupa Konseyi Başkanı António Costa Kiev'de"
    # a head of state is left alone
    assert AR.office_sense("Cumhurbaşkanı Zelenskiy konuştu", "President Zelensky spoke") == "Cumhurbaşkanı Zelenskiy konuştu"
    assert AR.sense("KAB grevleri, Cumhurbaşkanı von der Leyen", "KAB strikes, President von der Leyen") == "KAB saldırıları, AB Komisyonu Başkanı von der Leyen"


def test_turkish_forces_named_as_the_country_with_a_base_or_a_move():
    for title in ["Turkiye to hand over Bashiqa-Zilkan base to Iraq",
                  "Türkiye, Başika-Zilkan üssünü Irak'a devredecek",
                  "Turkey deploys troops to northern Syria",
                  "Ankara withdraws forces from Idlib outpost",
                  "Turkish drones strike targets in Iraq"]:
        assert AR.TUR_FORCES.search(title), title
    for title in ["Turkey hosts talks between Armenia and Azerbaijan",
                  "Greece and Turkey hold confidence-building talks in Athens",
                  "Italy ends twelve-year military presence in Iraq",
                  "Russian drone attack on Kharkiv injures two"]:
        assert not AR.TUR_FORCES.search(title), title


def test_a_verdict_on_a_trend_is_analysis():
    for title in ["Iran's Hormuz Leverage Is Fading, But It Isn't Gone",
                  "Iranian Hard-Liners Up In Arms After Araqchi-Witkoff Meeting In New York"]:
        assert AR.NOT_AN_EVENT.search(title), title
    for title in ["US Reinforcing Military Presence In Middle East As Iran Tensions Rise",
                  "Two 'terrorists' killed in Kirkuk clashes"]:
        assert not AR.NOT_AN_EVENT.search(title), title


def test_a_title_case_headline_is_put_in_sentence_case_for_the_translator():
    text = "State secretary Ivan Galić met with major general Manke of the German army in Zagreb on Monday."
    assert AR.sentence_case("State Secretary Galić Meets With Major General Manke", text) == \
        "State secretary Galić meets with major general Manke"
    # a headline already in sentence case is left alone
    assert AR.sentence_case("Two 'terrorists' killed in Kirkuk clashes", "") == "Two 'terrorists' killed in Kirkuk clashes"
    # with no excerpt, the common headline words are lowered and the names keep their capitals
    assert AR.sentence_case("US Targets Russia's A7 Network In New Iran Sanctions Push", "") == \
        "US targets Russia's A7 network in new Iran sanctions push"
    assert AR.sentence_case("Belgrade Showcases Lethal Chinese Tech", "") == "Belgrade showcases lethal Chinese tech"
    # a common word the excerpt capitalises is part of a name: "Joint Expeditionary Force" stays
    assert AR.sentence_case("UK Joint Expeditionary Force Holds Drills In Baltic", "The Joint Expeditionary Force began.") == \
        "UK Joint Expeditionary Force holds drills in Baltic"


def test_a_port_call_is_naval_activity_not_a_strike():
    codes = {"maritime.activity", "kinetic.missile-strike", "kinetic.drone-strike", "maritime.incident", "exercise.military"}
    def c(title, topic="kinetic.missile-strike"):
        return AR.Candidate(url="", title=title, text="guided-missile destroyer", lang="en", published_at="",
                            region="cyprus", topics=[topic], feed="rss-usa-c6f")
    assert AR.classify(c("USS Jason Dunham Arrives in Cyprus"), codes) == "maritime.activity"
    assert AR.classify(c("USS Roosevelt departs Cyprus"), codes) == "maritime.activity"
    # a ship with no act in the headline is not an attack, whatever the collector's topic
    assert AR.classify(c("USS Oscar Austin Holds Change of Command Ceremony"), codes) == "maritime.activity"
    # an attack on a ship stays an incident, a missile strike stays a strike
    assert AR.classify(c("Houthis attack a tanker in the Red Sea"), codes) == "maritime.incident"
    assert AR.classify(c("Russian missile strike on Odesa port"), codes) == "kinetic.missile-strike"
    assert AR.sentence_case("USS Jason Dunham Arrives In Cyprus", "") == "USS Jason Dunham arrives in Cyprus"


def test_defence_against_drones_is_not_a_drone_strike():
    codes = {"policy.defense", "kinetic.drone-strike", "kinetic.missile-strike"}
    def c(title):
        return AR.Candidate(url="", title=title, text="", lang="en", published_at="", region="black-sea",
                            topics=["kinetic.drone-strike"], feed="rss-ukr-ukrinform-war-en")
    assert AR.classify(c("Zelensky: More than 12 turrets to counter jet-powered drones installed in Kyiv"), codes) == "policy.defense"
    assert AR.classify(c("Ukraine expands production of interceptor drones"), codes) == "policy.defense"
    assert AR.classify(c("Anti-drone mobile fire groups shot down 40 Shaheds overnight"), codes) == "policy.defense"
    # an attack by drones stays a strike
    assert AR.classify(c("Russian drone attack on Kharkiv injures five"), codes) == "kinetic.drone-strike"
    assert AR.classify(c("Drone debris falls on roof of high-rise in Kyiv"), codes) == "kinetic.drone-strike"


def test_shelling_is_shelling():
    codes = {"kinetic.shelling", "kinetic.attack"}
    def c(title):
        return AR.Candidate(url="", title=title, text="", lang="en", published_at="", region="black-sea",
                            topics=["kinetic.attack"], feed="rss-ukr-ukrinform-war-en")
    assert AR.classify(c("Russian shelling damages infrastructure in Dnipropetrovsk region, killing one"), codes) == "kinetic.shelling"
    assert AR.classify(c("Kherson shelled 40 times overnight"), codes) == "kinetic.shelling"
    assert AR.classify(c("Russian attack kills two in Kharkiv"), codes) == "kinetic.attack"
