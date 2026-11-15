from datetime import datetime

from app.ingestion.sources import dtp_advisories
from app.timeutil import IST

NODE_HTML = """
<html><body>
<h1 class="page-header">🚨 Restrictions &amp; Diversions for Chhath Puja (15 &amp; 16 Nov 2026) 🌅</h1>
<article id="node-1392" class="node node-news-alert node-promoted node-sticky clearfix" about="/en/x">
<div class="field field-name-field-news-alert-advisory field-type-text-long field-label-above"><div class="field-label">News alert/advisory:&nbsp;</div><div class="field-items"><div class="field-item even">🚗 Traffic restrictions and diversions in place across East Delhi on Nov 15 &amp; 16 for Chhath Puja.</div></div></div>
<div class="form-item form-type-item form-group"> <label class="control-label">Language</label>
English</div>
<div class="field field-name-field-news field-type-taxonomy-term-reference"><div class="field-items"><div class="field-item even"><a href="/en/news-alertadvisory/advisory">Advisory</a></div></div></div>
<div class="field field-name-field-news-date field-type-text"><div class="field-items"><div class="field-item even">13-Nov-2026</div></div></div>
<div class="field field-name-field-end-date field-type-text"><div class="field-items"><div class="field-item even">2026-11-17</div></div></div>
<div class="field field-name-field-link field-type-text"><div class="field-items"><div class="field-item even">https://www.facebook.com/share/p/1JFSMGjaS4/</div></div></div>
<div class="field field-name-field-end-time field-type-text"><div class="field-items"><div class="field-item even">12:00</div></div></div>
<a href="/sites/default/files/Traffic%20Diversions/Advisory/chhath.pdf">PDF</a>
</article></body></html>
"""

LISTING_HTML = """<article id="node-1392" class="node node-news-alert node-teaser" about="/en/%F0%9F%9A%A8-chhath"></article>
<article id="node-1394" class="node node-news-alert node-teaser" about="/en/iitf"></article>"""


def test_parse_dtp_node():
    node = dtp_advisories.parse_node(NODE_HTML, "https://traffic.delhipolice.gov.in/en/x")
    assert node is not None
    assert node.node_id == 1392 and node.node_type == "news-alert"
    assert node.title.startswith("Restrictions & Diversions for Chhath Puja")
    assert "Chhath Puja" in node.body and "🚗" not in node.body
    assert node.language == "en" and node.category == "Advisory"
    assert node.news_date == datetime(2026, 11, 13, tzinfo=IST)
    assert node.end_at == datetime(2026, 11, 17, 12, 0, tzinfo=IST)
    assert node.pdf_links == ["https://traffic.delhipolice.gov.in/sites/default/files/Traffic%20Diversions/Advisory/chhath.pdf"]
    drafts = dtp_advisories.node_to_drafts(node)
    assert drafts[0].trust_level == "official" and drafts[0].valid_to_hint == node.end_at


def test_dtp_listing_discovery_and_max_id():
    urls = dtp_advisories.discover_from_listing(LISTING_HTML)
    assert urls == ["https://traffic.delhipolice.gov.in/en/%F0%9F%9A%A8-chhath", "https://traffic.delhipolice.gov.in/en/iitf"]
    assert dtp_advisories.max_node_id(LISTING_HTML) == 1394


def test_error_page_is_ignored():
    html = '<h1>Error Page</h1><article id="node-900" class="node node-page clearfix">'
    assert dtp_advisories.parse_node(html, "u") is None


