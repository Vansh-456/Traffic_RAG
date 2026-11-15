import pytest

from app.ingestion.geocoding import (
    AliasIndex,
    get_geocoder,
    load_seed_places,
    normalize_mention,
    seed_path,
)


@pytest.fixture(scope="module")
def geocoder():
    return get_geocoder()


@pytest.mark.parametrize(
    ("mention", "expected"),
    [
        ("ITO", "ITO"),
        ("near ITO chowk", "ITO"),
        ("आईटीओ", "ITO"),
        ("AIIMS flyover", "AIIMS"),
        ("एम्स", "AIIMS"),
        ("CP", "Connaught Place"),
        ("Rajiv Chowk", "Connaught Place"),
        ("कनॉट प्लेस", "Connaught Place"),
        ("Lal Qila", "Red Fort"),
        ("लाल किला", "Red Fort"),
        ("Red Fort ke paas", "Red Fort"),
        ("DND", "DND Flyway"),
        ("NH 48", "NH-48"),
        ("Delhi-Gurugram Expressway", "NH-48"),
        ("Kotla", "Arun Jaitley Stadium"),
        ("Pragati Maidan", "Bharat Mandapam"),
        ("T3", "IGI Airport Terminal 3"),
        ("धौला कुआं", "Dhaula Kuan"),
    ],
)
async def test_alias_resolution(geocoder, mention, expected):
    match = await geocoder.geocode(mention)
    assert match is not None, mention
    assert match.name == expected
    assert match.method == "alias"


async def test_fuzzy_spelling_variant(geocoder):
    match = await geocoder.geocode("Dhaulakuan flyovr")
    assert match is not None and match.name == "Dhaula Kuan"


async def test_unknown_place_returns_none(geocoder):
    assert await geocoder.geocode("Atlantis Boulevard") is None


def test_roads_have_line_geometry():
    places = {p.name: p for p in load_seed_places(seed_path())}
    ring = places["Ring Road"]
    assert ring.type == "road"
    assert ring.geometry.geom_type == "LineString"
    assert 28.4 < ring.lat < 28.9


def test_find_mentions_in_free_text():
    index = AliasIndex(load_seed_places(seed_path()))
    found = [rec.name for _, rec in index.find_mentions("Traffic diverted at ITO towards Vikas Marg and Ring Road")]
    assert found == ["ITO", "Vikas Marg", "Ring Road"]


def test_short_alias_requires_word_boundary():
    index = AliasIndex(load_seed_places(seed_path()))
    names = [rec.name for _, rec in index.find_mentions("the script for the concept")]
    assert "Connaught Place" not in names


def test_normalize_mention_strips_fillers():
    assert normalize_mention("near the ITO") == "ito"
    assert normalize_mention("Red Fort ke paas") == "red fort"
    assert normalize_mention("लाल किला के पास") == "लाल किला"
