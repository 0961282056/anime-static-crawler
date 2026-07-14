from __future__ import annotations

from collections.abc import Iterable

import pytest
import requests

import services.http_client as http_client_module
from services.errors import ImageStoreError, SourceFetchError, SourceNotFoundError
from services.http_client import SafeImageDownloader, SourceClient
from services.settings import CrawlerSettings


def _settings(**overrides: object) -> CrawlerSettings:
    values: dict[str, object] = {
        "source_base_url": "https://acgsecrets.hk/bangumi",
        "source_user_agent": "crawler-tests/1.0",
        "max_workers": 2,
        "request_timeout_seconds": 3,
        "image_timeout_seconds": 3,
        "image_max_bytes": 10,
        "image_max_pixels": 1_000_000,
        "image_allowed_hosts": ("static.acgsecrets.hk",),
        "minimum_count_ratio": 0.7,
        "maximum_parse_failure_ratio": 0.0,
        "maximum_fallback_id_ratio": 0.0,
        "cloudinary_quota_limit_percent": 90.0,
    }
    values.update(overrides)
    return CrawlerSettings(**values)  # type: ignore[arg-type]


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: Iterable[bytes] = (),
        text: str = "document",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.text = text
        self.encoding: str | None = None
        self.closed = False
        self.is_redirect = status_code in {301, 302, 303, 307, 308}
        self.is_permanent_redirect = status_code in {301, 308}
        self.ok = status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        assert chunk_size == 64 * 1024
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, *responses: _Response | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://static.acgsecrets.hk/a.jpg", "must use HTTPS"),
        (
            "https://user:pass@static.acgsecrets.hk/a.jpg",
            "may not contain credentials",
        ),
        (
            "https://static.acgsecrets.hk:444/a.jpg",
            "default HTTPS port",
        ),
        ("https://example.invalid/a.jpg", "host is not allowed"),
    ],
)
def test_image_url_validation_rejects_unsafe_urls(
    url: str,
    message: str,
) -> None:
    downloader = SafeImageDownloader(_settings())

    with pytest.raises(ImageStoreError, match=message):
        downloader._validate_url(url)


def test_image_url_validation_rejects_private_dns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_client_module.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    downloader = SafeImageDownloader(_settings())

    with pytest.raises(ImageStoreError, match="blocked address"):
        downloader._validate_url("https://static.acgsecrets.hk/a.jpg")


def test_image_download_follows_one_approved_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_client_module.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    redirect = _Response(
        status_code=302,
        headers={"Location": "/final.jpg"},
    )
    final = _Response(
        headers={"Content-Type": "image/jpeg", "Content-Length": "6"},
        chunks=(b"abc", b"", b"def"),
    )
    session = _Session(redirect, final)
    downloader = SafeImageDownloader(_settings())
    downloader._thread_local.session = session

    downloaded = downloader.download("https://static.acgsecrets.hk/start.jpg")

    assert downloaded.content == b"abcdef"
    assert downloaded.content_type == "image/jpeg"
    assert downloaded.final_url == "https://static.acgsecrets.hk/final.jpg"
    assert redirect.closed and final.closed
    assert [call[0] for call in session.calls] == [
        "https://static.acgsecrets.hk/start.jpg",
        "https://static.acgsecrets.hk/final.jpg",
    ]


def test_image_download_revalidates_redirect_destination() -> None:
    redirect = _Response(
        status_code=302,
        headers={"Location": "https://evil.invalid/stolen.jpg"},
    )
    session = _Session(redirect)
    downloader = SafeImageDownloader(_settings())
    downloader._thread_local.session = session

    with pytest.raises(ImageStoreError, match="host is not allowed"):
        downloader.download("https://static.acgsecrets.hk/start.jpg")

    assert redirect.closed
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            _Response(
                headers={"Content-Type": "text/html"},
                chunks=(b"not an image",),
            ),
            "unsupported Content-Type",
        ),
        (
            _Response(
                headers={"Content-Type": "image/png", "Content-Length": "11"},
                chunks=(b"ignored",),
            ),
            "configured size limit",
        ),
        (
            _Response(headers={"Content-Type": "image/png"}, chunks=()),
            "response was empty",
        ),
    ],
)
def test_image_download_rejects_invalid_response_metadata(
    monkeypatch: pytest.MonkeyPatch,
    response: _Response,
    message: str,
) -> None:
    downloader = SafeImageDownloader(_settings())
    downloader._thread_local.session = _Session(response)
    monkeypatch.setattr(downloader, "_validate_url", lambda url: None)

    with pytest.raises(ImageStoreError, match=message):
        downloader.download("https://static.acgsecrets.hk/a.png")

    assert response.closed


def test_image_download_rejects_stream_that_grows_past_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(
        headers={"Content-Type": "image/png"},
        chunks=(b"123456", b"789012"),
    )
    downloader = SafeImageDownloader(_settings())
    downloader._thread_local.session = _Session(response)
    monkeypatch.setattr(downloader, "_validate_url", lambda url: None)

    with pytest.raises(ImageStoreError, match="configured size limit"):
        downloader.download("https://static.acgsecrets.hk/a.png")

    assert response.closed


def test_source_client_builds_url_and_surfaces_http_failures() -> None:
    settings = _settings()
    client = SourceClient(settings, session=_Session(_Response(status_code=404)))
    assert client.season_url("2026", "夏").endswith("/202607/")

    with pytest.raises(SourceNotFoundError):
        client.fetch_quarter_html("2026", "夏")

    with pytest.raises(ValueError, match="Unsupported season"):
        client.season_url("2026", "雨")
    with pytest.raises(ValueError, match="Invalid year"):
        client.season_url("26", "夏")


@pytest.mark.parametrize(
    ("response", "error_type", "message"),
    [
        (_Response(status_code=503), SourceFetchError, "HTTP 503"),
        (_Response(text="   "), SourceFetchError, "empty document"),
        (
            requests.ConnectionError("offline"),
            SourceFetchError,
            "Unable to fetch",
        ),
    ],
)
def test_source_client_rejects_failed_or_empty_responses(
    response: _Response | Exception,
    error_type: type[Exception],
    message: str,
) -> None:
    client = SourceClient(_settings(), session=_Session(response))

    with pytest.raises(error_type, match=message):
        client.fetch_quarter_html("2026", "夏")


def test_source_client_returns_nonempty_utf8_document() -> None:
    response = _Response(text="<html>ok</html>")
    client = SourceClient(_settings(), session=_Session(response))

    url, document = client.fetch_quarter_html("2026", "夏")

    assert url.endswith("/202607/")
    assert document == "<html>ok</html>"
    assert response.encoding == "utf-8"
