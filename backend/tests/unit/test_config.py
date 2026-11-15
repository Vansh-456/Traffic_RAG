from app.config import Settings


def test_csv_lists_and_flags(monkeypatch):
    monkeypatch.setenv("NEWS_QUERIES", "a, b ,,c")
    monkeypatch.setenv("EXTRA_RSS_FEEDS", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("TOMTOM_API_KEY", "")
    s = Settings(_env_file=None)
    assert s.news_queries == ["a", "b", "c"]
    assert s.extra_rss_feeds == []
    assert s.tomtom_enabled is False


def test_llm_enabled_with_groq_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert Settings(_env_file=None).llm_enabled is True


def test_sync_url():
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h/d")
    assert s.sync_database_url == "postgresql+psycopg://u:p@h/d"


def test_comment_only_values_are_treated_as_empty(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "# live flow + incidents disabled when empty")
    monkeypatch.setenv("SMTP_HOST", "# ... or SMTP")
    s = Settings(_env_file=None)
    assert s.tomtom_api_key == "" and s.tomtom_enabled is False
    assert s.smtp_host == ""
