from app.ingestion.parsing.html import clean_text, strip_emoji
from app.ingestion.parsing.language import (
    detect_document_language,
    detect_query_language,
    looks_garbled,
)


def test_document_language():
    assert detect_document_language("Traffic will be diverted at ITO") == "en"
    assert detect_document_language("आईटीओ पर यातायात को मोड़ा जाएगा") == "hi"
    assert detect_document_language("ITO पर traffic diversion रहेगा आज") == "mixed"


def test_query_language():
    assert detect_query_language("Any diversions near ITO today?") == "en"
    assert detect_query_language("Kal Red Fort ke paas traffic kaisa rahega?") == "hinglish"
    assert detect_query_language("आज आईटीओ के पास कोई डायवर्जन है?") == "hi"
    assert detect_query_language("Why is Ring Road slow right now?") == "en"


def test_garbled_detection():
    assert looks_garbled("")  # scanned PDF, no text layer
    kruti = "fnYyh ;krk;kr iqfyl }kjk vkSj ;krk;kr dks ekxZ ij Mk;oVZ fd;k tk,xkA " * 6
    assert looks_garbled(kruti)
    good = (
        "Traffic coming from Vikas Marg towards ITO will be diverted at ITO chowk towards Ring Road. "
        * 4
    )
    assert not looks_garbled(good)
    assert not looks_garbled(
        "विकास मार्ग से आईटीओ की ओर आने वाले यातायात को रिंग रोड की ओर मोड़ा जाएगा। " * 3
    )


def test_clean_text_drops_boilerplate_and_page_numbers():
    raw = "ADVISORY\nRoad closed at ITO.\nFollow us on Twitter @dtptraffic\nPage 1 of 2\n\n\nUse Ring Road."
    out = clean_text(raw)
    assert "Follow us" not in out and "Page 1" not in out
    assert "Road closed at ITO." in out and "Use Ring Road." in out


def test_strip_emoji():
    assert strip_emoji("🚨 Heavy Congestion 🏛️🚗").strip() == "Heavy Congestion"
