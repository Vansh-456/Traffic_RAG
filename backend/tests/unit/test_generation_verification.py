from datetime import datetime

from app.config import Settings
from app.providers.llm import LLMClient
from app.rag import generation as gen
from app.rag.verification import split_claims, verify
from app.timeutil import IST


def test_followup_stripper_hides_block_across_tokens():
    s = gen.FollowupStripper()
    shown = ""
    for tok in [
        "Road closed [1].",
        " Use Ring",
        " Road [2].\n<follo",
        'wups>["a","b",',
        '"c"]</followups>',
    ]:
        shown += s.feed(tok)
    shown += s.flush()
    assert shown == "Road closed [1]. Use Ring Road [2].\n"
    assert s.followups() == ["a", "b", "c"]
    assert s.answer_text() == "Road closed [1]. Use Ring Road [2]."


def test_stripper_passes_angle_brackets_that_are_not_tags():
    s = gen.FollowupStripper()
    out = s.feed("speed <30 km/h") + s.flush()
    assert out == "speed <30 km/h"


def test_cited_ids():
    assert gen.cited_ids("A [1]. B [2, 3]. C [10]") == {1, 2, 3, 10}


def _evidence():
    return [
        gen.Evidence(
            n=1,
            kind="chunk",
            title="Diversions near ITO",
            text="Bahadur Shah Zafar Marg will be closed between Delhi Gate and ITO from 10 am to 6 pm.",
            source="Delhi Traffic Police",
            url="https://x",
            published_at=None,
            chunk_id=5,
        ),
        gen.Evidence(
            n=2,
            kind="tool",
            title="Live traffic",
            text="Live TomTom flow: Ring Road near ITO: 14 km/h vs free-flow 45 km/h (heavy)",
            source="TomTom (live)",
            url=None,
            published_at=None,
            tool="get_live_traffic",
        ),
    ]


def test_split_claims():
    assert split_claims("First claim here [1]. Second claim follows [2].\n- bullet item one") == [
        "First claim here [1].",
        "Second claim follows [2].",
        "- bullet item one",
    ]


async def test_lexical_verification_removes_unsupported():
    llm = LLMClient(
        Settings(
            _env_file=None,
            groq_api_key="",
            gemini_api_key="",
            anthropic_api_key="",
            openai_api_key="",
        )
    )
    answer = (
        "Bahadur Shah Zafar Marg is closed between Delhi Gate and ITO until 6 pm [1]. "
        "Ring Road near ITO is moving at 14 km/h [2]. "
        "Metro services on the Yellow Line are suspended due to flooding [1]."
    )
    res = await verify(answer, _evidence(), llm)
    assert res.method == "lexical"
    supported = [c.supported for c in res.checks]
    assert supported == [True, True, False]
    assert res.changed and "(Unconfirmed)" in res.revised_text
    assert res.faithfulness == 2 / 3


async def test_citation_to_missing_evidence_is_unsupported():
    llm = LLMClient(Settings(_env_file=None, groq_api_key="", gemini_api_key=""))
    res = await verify("Bahadur Shah Zafar Marg is closed near ITO today [7].", _evidence(), llm)
    assert res.checks[0].supported is False


def test_extractive_answer_and_abstain_language():
    class QU:
        language = "hi"
        locations: list = []

    text = gen.extractive_answer(QU(), [], abstain=True)  # type: ignore[arg-type]
    assert "पर्याप्त" in text and "1095" in text


def test_map_features_and_cards_from_tools():
    ev = _evidence()
    ev[1].extra["result"] = {
        "segments": [
            {
                "name": "Ring Road near ITO",
                "lat": 28.63,
                "lon": 77.25,
                "geometry": None,
                "current_speed_kmph": 14,
                "free_flow_speed_kmph": 45,
                "level": "heavy",
                "trend_60min": "worsening",
                "as_of": "08:55 IST",
            }
        ]
    }

    class QU:
        locations: list = []

    fc = gen.map_features(ev, QU(), {2})  # type: ignore[arg-type]
    assert fc["features"][0]["properties"]["kind"] == "segment"
    cards = gen.cards(ev)
    assert cards[0]["type"] == "live_traffic" and cards[0]["current_speed"] == 14
    _ = datetime.now(IST)


async def test_lexical_fallback_does_not_rewrite_llm_answers():
    llm = LLMClient(Settings(_env_file=None, groq_api_key="", gemini_api_key=""))
    answer = "Ring Road is jammed because of a protest near ITO [1]."
    res = await verify(answer, _evidence(), llm, lexical_may_rewrite=False)
    assert res.method == "lexical-advisory" and not res.changed and res.revised_text == answer


def test_split_keeps_list_markers_with_items():
    from app.rag.verification import _tidy

    parts = split_claims(
        "**Likely causes:**\n1. Festival diversions on Vikas Marg [1].\n2. Heavy rain [2].\n**Tips:**"
    )
    assert parts[1].startswith("1. Festival") and parts[2].startswith("2. Heavy")
    assert _tidy(["**Likely causes:**", "1.", "2.", "**Tips:**"]) == []
    assert _tidy(["**Tips:**", "- Use the metro."]) == ["**Tips:**", "- Use the metro."]
