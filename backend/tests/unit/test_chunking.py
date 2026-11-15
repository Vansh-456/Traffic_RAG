from datetime import datetime

from app.ingestion.chunking import (
    TARGET_MAX,
    build_context_header,
    chunk_text,
    estimate_tokens,
    is_heading,
    split_sections,
)
from app.timeutil import IST

SHORT = """Traffic Advisory
In view of the procession, the following diversions will be in place:
1. Traffic from Vikas Marg will be diverted at ITO.
2. BSZ Marg will be closed between Delhi Gate and ITO."""


def republic_day_advisory() -> str:
    sections = []
    for heading in [
        "RESTRICTIONS",
        "Diversions:",
        "Parking",
        "Metro",
        "Alternate routes",
        "Do's and Don'ts",
    ]:
        items = "\n".join(
            f"{i}. Traffic on road number {i} near Vijay Chowk and India Gate will not be permitted between 6 AM and 1 PM for the parade rehearsal, commuters should plan accordingly."
            for i in range(1, 16)
        )
        sections.append(f"{heading}\n{items}")
    return "Republic Day Parade Full Dress Rehearsal\n\n" + "\n\n".join(sections)


def test_short_document_is_single_chunk_and_keeps_list_with_heading():
    chunks = chunk_text(SHORT)
    assert len(chunks) == 1
    assert "Traffic Advisory" in chunks[0].text and "2. BSZ Marg" in chunks[0].text


def test_heading_detection():
    assert is_heading("Diversions:")
    assert is_heading("RESTRICTIONS")
    assert is_heading("Do's and Don'ts")
    assert is_heading("वैकल्पिक मार्ग")
    assert not is_heading("1. Traffic from Vikas Marg will be diverted at ITO.")


def test_long_advisory_splits_by_section_within_budget():
    text = republic_day_advisory()
    assert estimate_tokens(text) > 800
    sections = split_sections(text)
    assert {s.heading for s in sections if s.heading} >= {
        "RESTRICTIONS",
        "Diversions",
        "Parking",
        "Metro",
    }
    chunks = chunk_text(text)
    assert len(chunks) > 5
    for c in chunks:
        assert c.token_count <= TARGET_MAX + 80
        assert c.section  # every chunk knows its section
        assert c.section.split(" / ")[0] in c.text  # heading repeated in each window


def test_overlap_is_within_section_only():
    chunks = chunk_text(republic_day_advisory())
    by_section: dict[str, list[str]] = {}
    for c in chunks:
        by_section.setdefault(c.section or "", []).append(c.text)
    diversions = by_section["Diversions"]
    if len(diversions) > 1:
        first_last_item = diversions[0].strip().split("\n")[-1]
        assert first_last_item in diversions[1]  # overlap carried forward
    assert not any("Parking" in t for t in by_section.get("Diversions", []))


def test_context_header():
    h = build_context_header(
        title="Diversions near ITO",
        authority="Delhi Traffic Police",
        published_at=datetime(2026, 11, 9, 18, 0, tzinfo=IST),
        valid_from=datetime(2026, 11, 10, 10, 0, tzinfo=IST),
        valid_to=None,
        section="Diversions",
        source_label="Delhi Traffic Police (official)",
    )
    assert "Title: Diversions near ITO" in h
    assert "Valid: 10 Nov 2026 10:00 IST to until further notice" in h
    assert "Section: Diversions" in h
