from datetime import datetime, timedelta

from app.ingestion.sources import google_news, tomtom, weather
from app.timeutil import IST

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Ring Road closed near AIIMS for repair - City Times</title><link>https://news.google.com/rss/articles/abc</link>
<description>Ring Road closed near AIIMS for repair  City Times</description><pubDate>{now}</pubDate><source url="https://citytimes.example">City Times</source></item>
<item><title>Mumbai local trains delayed - Daily</title><link>https://news.google.com/rss/articles/def</link><pubDate>{now}</pubDate></item>
<item><title>Delhi traffic jam on NH-48 - Old</title><link>https://news.google.com/rss/articles/old</link><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_google_news_parse_filters_relevance_and_age():
    now = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    xml = RSS.replace("{now}", now).replace("Ring Road closed", "Delhi: Ring Road closed")
    drafts = google_news.parse_feed(xml, "google_news")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.title == "Delhi: Ring Road closed near AIIMS for repair"
    assert d.publisher == "City Times" and d.trust_level == "news"


def test_google_news_urls_language():
    urls = google_news.google_news_urls(["delhi traffic", "दिल्ली जाम"])
    assert "hl=en-IN" in urls[0] and "ceid=IN:hi" in urls[1]


def test_tomtom_parse_and_render():
    flow = tomtom.parse_flow(
        "rr-ito",
        {
            "flowSegmentData": {
                "currentSpeed": 18,
                "freeFlowSpeed": 45,
                "currentTravelTime": 120,
                "freeFlowTravelTime": 50,
                "confidence": 0.9,
                "roadClosure": False,
                "coordinates": {
                    "coordinate": [
                        {"latitude": 28.63, "longitude": 77.25},
                        {"latitude": 28.64, "longitude": 77.25},
                    ]
                },
            }
        },
    )
    assert flow is not None and flow.current_speed == 18 and len(flow.coordinates) == 2
    payload = {
        "incidents": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [77.234, 28.564]},
                "properties": {
                    "id": "abc",
                    "iconCategory": 1,
                    "magnitudeOfDelay": 3,
                    "events": [{"description": "accident"}],
                    "startTime": "2026-11-10T12:50:00Z",
                    "from": "Moolchand",
                    "to": "Ashram",
                    "delay": 720,
                    "roadNumbers": ["Ring Road"],
                },
            }
        ]
    }
    [inc] = tomtom.parse_incidents(payload)
    assert inc.category == "accident" and inc.delay_seconds == 720
    text = tomtom.render_incident(inc, datetime(2026, 11, 10, 18, 40, tzinfo=IST))
    assert text.startswith("Accident on Ring Road between Moolchand and Ashram, delay ~12 min")
    assert "18:20 IST" in text and "as of 18:40 IST" in text
    draft = tomtom.incident_draft(inc, datetime(2026, 11, 10, 18, 40, tzinfo=IST))
    assert draft.url == "tomtom:incident:abc" and draft.trust_level == "sensor"
    assert draft.valid_to_hint == datetime(2026, 11, 10, 20, 40, tzinfo=IST)


class FakeRedis:
    """Minimal async Redis for the budget manager (GET/SET/EVAL spend script)."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v

    async def eval(self, _script, _n, key, n, cap):
        used = int(self.kv.get(key, 0))
        if used + int(n) > int(cap):
            return -1
        self.kv[key] = str(used + int(n))
        return used + int(n)


async def test_budget_never_exceeds_cap():
    bm = tomtom.BudgetManager(FakeRedis(), daily_budget=100)
    now = datetime(2026, 11, 10, 9, 0, tzinfo=IST)
    spent = 0
    for _ in range(50):
        if await bm.try_spend(37, now):
            spent += 37
    assert spent <= 100 and await bm.used(now) == spent == 74


async def test_budget_interval_adapts():
    redis = FakeRedis()
    bm = tomtom.BudgetManager(redis, daily_budget=2300)
    day = datetime(2026, 11, 10, 9, 0, tzinfo=IST)
    base = await bm.interval_minutes(day, cost_per_cycle=37)
    assert base >= tomtom.DAY_INTERVAL_MIN
    planned = bm.planned_requests(day, 37, base / tomtom.DAY_INTERVAL_MIN)
    assert planned <= 2300 + 37  # stretched cadence fits the budget
    night = datetime(2026, 11, 10, 23, 30, tzinfo=IST)
    assert await bm.interval_minutes(night, 37) >= tomtom.NIGHT_INTERVAL_MIN
    redis.kv[bm._day_key(day)] = "2300"
    assert not await bm.should_poll("flow", 37, day)


async def test_should_poll_respects_last_poll():
    bm = tomtom.BudgetManager(FakeRedis(), daily_budget=100000)
    now = datetime(2026, 11, 10, 9, 0, tzinfo=IST)
    assert await bm.should_poll("incidents", 1, now)
    await bm.mark_polled("incidents", now)
    assert not await bm.should_poll("incidents", 1, now + timedelta(minutes=5))
    assert await bm.should_poll("incidents", 1, now + timedelta(minutes=16))


def test_weather_parse_and_flags():
    now = datetime(2026, 11, 10, 9, 0, tzinfo=IST)
    loc = {
        "current": {
            "time": "2026-11-10T09:00",
            "temperature_2m": 21.0,
            "precipitation": 0,
            "rain": 0,
            "weather_code": 45,
            "visibility": 400,
        },
        "hourly": {
            "time": ["2026-11-10T08:00", "2026-11-10T12:00"],
            "temperature_2m": [20, 25],
            "precipitation": [0, 9],
            "rain": [0, 9],
            "weather_code": [45, 65],
            "visibility": [300, 8000],
        },
    }
    rows = weather.parse_open_meteo([loc], now)
    cur = next(r for r in rows if r.kind == "current")
    assert cur.zone == "central" and "fog_low_visibility" in cur.flags
    later = next(r for r in rows if r.observed_at.hour == 12)
    assert later.kind == "forecast" and "heavy_rain" in later.flags
    assert weather.derive_flags(None, None, 44, None) == ["extreme_heat"]
