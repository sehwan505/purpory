from __future__ import annotations

import pytest

from purpory import ingest


def test_tweet_fetch_failure_does_not_create_a_stub(monkeypatch):
    monkeypatch.setattr(
        ingest,
        "safe_fetch_text",
        lambda _url: (_ for _ in ()).throw(OSError("network unavailable")),
    )

    with pytest.raises(OSError, match="network unavailable"):
        ingest._fetch_tweet("https://x.com/example/status/123", None, None)


def test_tweet_response_requires_content_and_author(monkeypatch):
    monkeypatch.setattr(ingest, "safe_fetch_text", lambda _url: '{"html": ""}')

    with pytest.raises(ValueError, match="missing content or author"):
        ingest._fetch_tweet("https://x.com/example/status/123", None, None)


def test_arxiv_fetch_requires_complete_metadata(monkeypatch):
    monkeypatch.setattr(ingest, "_fetch_html", lambda _url: "<html><title>partial</title></html>")

    with pytest.raises(ValueError, match="missing metadata"):
        ingest._fetch_arxiv("https://arxiv.org/abs/2401.12345", None, None)
