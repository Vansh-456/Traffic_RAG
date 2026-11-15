from app.config import Settings
from app.providers.reranker import CohereReranker, LexicalReranker, build_reranker


async def test_lexical_reranker_orders_relevant_first():
    r = LexicalReranker()
    scores = await r.score(
        "Ring Road closed near AIIMS",
        ["Ring Road will remain closed near AIIMS flyover", "Metro timings extended on Sunday"],
    )
    assert scores[0] > scores[1]
    assert all(0 <= s <= 1 for s in scores)


def test_build_reranker_falls_back_without_key():
    s = Settings(_env_file=None, reranker_provider="cohere", cohere_api_key="")
    assert isinstance(build_reranker(s), LexicalReranker)
    s2 = Settings(_env_file=None, reranker_provider="cohere", cohere_api_key="x")
    assert isinstance(build_reranker(s2), CohereReranker)
