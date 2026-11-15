import numpy as np

from app.providers.embeddings import HashEmbedder, cosine, fit_dim


async def test_hash_embedder_shape_and_norm():
    e = HashEmbedder(1024)
    [v] = await e.embed(["Diversion near ITO"])
    assert len(v) == 1024
    assert abs(np.linalg.norm(v) - 1) < 1e-5


async def test_hash_embedder_semantic_ordering():
    e = HashEmbedder(1024)
    q = await e.embed_query("diversions near ITO today")
    near, far = await e.embed(
        ["Traffic diversion at ITO crossing due to procession", "Weather is pleasant in Shimla"]
    )
    assert cosine(q, near) > cosine(q, far)


async def test_hash_embedder_handles_hindi():
    e = HashEmbedder(1024)
    q = await e.embed_query("लाल किला ट्रैफिक")
    a, b = await e.embed(["लाल किला के पास ट्रैफिक डायवर्जन", "Cricket match result"])
    assert cosine(q, a) > cosine(q, b)


def test_fit_dim_pads_and_truncates():
    assert len(fit_dim([1.0, 2.0], 4)) == 4
    assert len(fit_dim(list(range(10)), 4)) == 4
