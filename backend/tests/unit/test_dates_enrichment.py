from datetime import date, datetime, time

from app.ingestion.dates import extract_dates, extract_times, validity_window
from app.ingestion.enrichment import Enrichment, heuristic_enrichment
from app.timeutil import IST

REF = date(2026, 11, 10)


def test_extract_dates_formats():
    assert extract_dates("on 15.11.2026 and 16/11/2026", REF) == [date(2026, 11, 15), date(2026, 11, 16)]
    assert extract_dates("News Date: 13-Nov-2026", REF) == [date(2026, 11, 13)]
    assert extract_dates("Chhath Puja (15 & 16 Nov 2026)", REF) == [date(2026, 11, 15), date(2026, 11, 16)]
    assert extract_dates("from 14–27 Nov 2026", REF) == [date(2026, 11, 14), date(2026, 11, 27)]
    assert extract_dates("on November 15, 2026", REF) == [date(2026, 11, 15)]
    assert extract_dates("15 नवंबर 2026 को", REF) == [date(2026, 11, 15)]


def test_extract_times():
    assert extract_times("from 10:00 AM to 6 pm") == [time(10, 0), time(18, 0)]
    assert extract_times("between 23:00 and 05:00") == [time(23, 0), time(5, 0)]


def test_validity_window():
    pub = datetime(2026, 11, 9, 18, 0, tzinfo=IST)
    vf, vt = validity_window("Procession on 10.11.2026 between 10:00 AM and 6:00 PM", pub)
    assert vf == datetime(2026, 11, 10, 10, 0, tzinfo=IST)
    assert vt == datetime(2026, 11, 10, 18, 0, tzinfo=IST)
    assert validity_window("no dates here", pub) == (None, None)


def test_heuristic_enrichment():
    text = "A procession will move via ITO on 10.11.2026 from 10 AM to 6 PM. Bahadur Shah Zafar Marg will be closed."
    e = heuristic_enrichment(text, "Diversions near ITO for procession", datetime(2026, 11, 9, tzinfo=IST))
    assert e.event_type == "procession"
    assert "ITO" in e.locations and "Bahadur Shah Zafar Marg" in e.roads
    assert e.severity == "high"
    assert e.valid_from and e.valid_from.startswith("2026-11-10T10:00")
    assert len(e.hypothetical_questions) >= 3


def test_enrichment_schema_parses_window():
    e = Enrichment(event_type="closure", valid_from="2026-11-10T10:00:00+05:30", valid_to="10/11/2026 18:00")
    vf, vt = e.parsed_window()
    assert vf == datetime(2026, 11, 10, 10, 0, tzinfo=IST)
    assert vt is not None and vt.hour == 18
