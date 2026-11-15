import math
from datetime import datetime, timedelta

import pytest

from app.ingestion.dedup import Member, choose_canonical, hamming, simhash64
from app.rag.query_understanding import heuristic_understanding, keyword_query
from app.rag.retrieval import freshness
from app.rag.retrieval.fusion import rrf
from app.rag.retrieval.sparse import or_tsquery
from app.rag.timewindow import resolve_time_window
from app.timeutil import IST

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=IST)  # Tuesday


def test_rrf_scores_and_ranks():
    scores, ranks = rrf({"dense": [1, 2, 3], "sparse": [3, 1]})
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert ranks[3] == {"dense": 3, "sparse": 1}
    assert max(scores, key=scores.get) in (1, 3)


def test_freshness_trust_proximity():
    assert freshness.freshness("live_conditions", 0) == 1.0
    assert freshness.freshness("live_conditions", 6) == pytest.approx(math.exp(-1))
    assert freshness.freshness("historical_lookup", 500) == 1.0
    assert freshness.freshness("advisory_lookup", 72, enabled=False) == 1.0
    assert freshness.trust("official") == 1.0 and freshness.trust("news") == 0.85
    assert freshness.proximity(0, 1500) == pytest.approx(1.3)
    assert freshness.proximity(2000, 1500) == pytest.approx(math.exp(-500 / 3000))
    assert freshness.proximity(18000, 2500) == freshness.PROXIMITY_FLOOR  # other side of the city
    assert freshness.proximity(None, 1500) == 1.0


def test_delta_hours_inside_validity_is_zero():
    vf, vt = NOW - timedelta(hours=1), NOW + timedelta(hours=5)
    assert freshness.delta_hours(NOW, vf, vt, None) == 0
    assert freshness.delta_hours(NOW, NOW + timedelta(hours=3), None, None) == pytest.approx(3)
    assert freshness.delta_hours(NOW, None, None, NOW - timedelta(hours=10)) == pytest.approx(10)


def test_simhash_near_duplicates():
    a = "Bahadur Shah Zafar Marg will be closed between Delhi Gate and ITO from 10 am to 6 pm on Tuesday"
    b = "Bahadur Shah Zafar Marg will be closed between Delhi Gate and ITO from 10 am to 6 pm on Tuesday."
    c = "Heavy rain causes waterlogging at Dhaula Kuan on Ring Road towards the airport"
    assert hamming(simhash64(a), simhash64(b)) <= 3
    assert hamming(simhash64(a), simhash64(c)) > 10
    assert -(2**63) <= simhash64(a) < 2**63


def test_canonical_prefers_official_then_longest():
    members = [Member(1, "news", 900), Member(2, "official", 300), Member(3, "official", 500)]
    assert choose_canonical(members) == 3


@pytest.mark.parametrize(
    ("text", "label", "start_day", "start_hour"),
    [
        ("Why is Ring Road slow right now?", "now", 10, 7),
        ("Any diversions near ITO today?", "today", 10, 0),
        ("Kal Red Fort ke paas traffic kaisa rahega?", "tomorrow", 11, 0),
        ("Kal Red Fort pe kya hua tha?", "yesterday", 9, 0),
        ("What happened on Outer Ring Road last Tuesday evening?", "last_weekday", 3, 17),
        ("Is 6 pm Friday a bad time to drive to IGI Airport?", "weekday", 13, 17),
        ("Traffic this weekend near India Gate", "this_weekend", 14, 0),
        ("कल लाल किला के पास ट्रैफिक कैसा रहेगा?", "tomorrow", 11, 0),
    ],
)
def test_time_windows(text, label, start_day, start_hour):
    w = resolve_time_window(text, NOW)
    assert w.label == label
    assert w.start.day == start_day and w.start.hour == start_hour


def test_time_window_intent_defaults():
    assert resolve_time_window("Ring Road", NOW, "live_conditions").is_live
    assert resolve_time_window("closures on Ring Road", NOW, "historical_lookup").is_historical


async def test_heuristic_understanding():
    qu = await heuristic_understanding("Kal Red Fort ke paas traffic kaisa rahega?", NOW)
    assert qu.language == "hinglish"
    assert [m.name for m in qu.locations] == ["Red Fort"]
    assert qu.time_window.label == "tomorrow"
    assert qu.intent in ("live_conditions", "advisory_lookup")
    oos = await heuristic_understanding("Give me a recipe for paneer butter masala", NOW)
    assert oos.intent == "out_of_scope"


async def test_followup_inherits_location():
    history = [
        {"role": "user", "content": "Any diversions near ITO today?"},
        {"role": "assistant", "content": "..."},
    ]
    qu = await heuristic_understanding("and what about tomorrow?", NOW, history)
    assert [m.name for m in qu.locations] == ["ITO"]
    assert qu.time_window.label == "tomorrow"


def test_keyword_query_translates_hinglish():
    q = keyword_query("Kal Red Fort ke paas traffic kaisa rahega", [])
    assert "tomorrow" in q and "near" in q


def test_or_tsquery():
    assert or_tsquery("Any diversions near ITO today?") == "diversions:* | ito"


def test_window_gap_hours():
    ws, we = NOW, NOW + timedelta(days=1)
    assert (
        freshness.window_gap_hours(ws, we, NOW + timedelta(hours=5), NOW + timedelta(days=3)) == 0
    )
    assert freshness.window_gap_hours(ws, we, NOW + timedelta(days=2), None) == pytest.approx(24)
    assert freshness.window_gap_hours(ws, we, None, NOW - timedelta(hours=6)) == pytest.approx(6)
