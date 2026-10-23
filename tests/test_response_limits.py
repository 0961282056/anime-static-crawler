"""Exercise actual Requests hook/redirect/decompression handling, without IO."""

import gzip
import io

import pytest
import requests
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse

from services.episode_metadata import BangumiClient, MetadataUnavailable
from services.errors import ImageStoreError, NotificationError, SourceFetchError
from services.http_client import SafeImageDownloader, SourceClient
from services.notifier import DiscordNotifier, Notification
from services.official_catalogue import NHKCatalogueClient, OfficialSourceUnavailable
from services.production_verification import PublicReader
from services.response_limits import ResponseSizeExceeded, bounded_response
from services.review_notifications import GitHubReviewClient
from services.secondary_source import AniListClient, SecondarySourceError
from services.settings import CrawlerSettings


def settings():
    return CrawlerSettings.from_environment()


class ResponseAdapter(BaseAdapter):
    def __init__(self, body=b"", *, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.requests = []
        self.responses = []

    def send(self, request, **kwargs):
        self.requests.append((request, kwargs))
        response = requests.Response()
        response.status_code = self.status
        response.headers.update(self.headers)
        response.url = request.url
        response.request = request
        response.raw = HTTPResponse(
            body=io.BytesIO(self.body),
            headers=self.headers,
            preload_content=False,
            decode_content=False,
        )
        self.responses.append(response)
        return response

    def close(self):
        pass


def session_for(adapter):
    session = requests.Session()
    # This adapter handles every URL; no test opens a network connection.
    session.mount("https://", adapter)
    return session


@pytest.mark.parametrize(
    "headers",
    [{}, {"Content-Length": "1"}, {"Transfer-Encoding": "chunked"}],
)
def test_body_limit_counts_received_bytes_not_just_declared_length(headers):
    adapter = ResponseAdapter(b"x" * 200_000, headers=headers)
    with pytest.raises(ResponseSizeExceeded, match="size limit"):
        session_for(adapter).get(
            "https://source.invalid/",
            stream=True,
            hooks={"response": bounded_response(100_000)},
        )
    assert adapter.responses[0].raw.closed
    # Reading stops when the cap is crossed; the rest was not downloaded.
    assert adapter.responses[0].raw.tell() == 2 * 64 * 1024


def test_gzip_limit_counts_decoded_bytes_and_closes_response():
    adapter = ResponseAdapter(
        gzip.compress(b"x" * 200_000), headers={"Content-Encoding": "gzip"}
    )
    with pytest.raises(ResponseSizeExceeded):
        session_for(adapter).get(
            "https://source.invalid/",
            stream=True,
            hooks={"response": bounded_response(100_000)},
        )
    assert adapter.responses[0].raw.closed


@pytest.mark.parametrize("length", ["100001", "9" * 5000, "-1", "invalid", "１２"])
def test_invalid_or_excessive_length_is_rejected_before_body_read(length):
    adapter = ResponseAdapter(b"body", headers={"Content-Length": length})
    with pytest.raises(ResponseSizeExceeded):
        session_for(adapter).get(
            "https://source.invalid/",
            stream=True,
            hooks={"response": bounded_response(100_000)},
        )
    assert adapter.responses[0].raw.tell() == 0
    assert adapter.responses[0].raw.closed


def test_success_at_limit_preserves_json_text_and_iteration():
    body = '{"title":"中文"}'.encode()
    adapter = ResponseAdapter(body)
    response = session_for(adapter).get(
        "https://source.invalid/",
        stream=True,
        hooks={"response": bounded_response(len(body))},
    )
    response.encoding = "utf-8"
    assert response.json() == {"title": "中文"}
    assert response.text == body.decode()
    assert b"".join(response.iter_content(3)) == body
    response.close()


def test_read_failure_closes_response_without_retaining_partial_body(monkeypatch):
    response = requests.Response()
    response.raw = io.BytesIO()

    def chunks(**kwargs):
        yield b"partial"
        raise requests.ConnectionError("interrupted")

    monkeypatch.setattr(response, "iter_content", chunks)
    with pytest.raises(requests.ConnectionError):
        bounded_response(100)(response)
    assert response.raw.closed
    assert response._content is False


def test_existing_production_deadline_is_enforced_during_hook(monkeypatch):
    adapter = ResponseAdapter(b"body")
    monkeypatch.setattr("services.response_limits.time.monotonic", lambda: 11)
    with pytest.raises(requests.Timeout, match="time budget"):
        session_for(adapter).get(
            "https://source.invalid/",
            stream=True,
            hooks={"response": bounded_response(100, deadline=10)},
        )
    assert adapter.responses[0].raw.closed


@pytest.mark.parametrize("status", [200, 204, 302, 307, 308])
def test_discord_success_and_redirect_contract_without_sending_notifications(
    status, monkeypatch
):
    adapter = ResponseAdapter(
        status=status, headers={"Location": "https://other.invalid/"}
    )
    monkeypatch.setattr("services.notifier.requests.post", session_for(adapter).post)
    notifier = DiscordNotifier("https://notify.invalid/webhook/test")
    notification = Notification(status="FAILURE", year="2026", season="夏")
    if status in {200, 204}:
        assert notifier.send(notification) is True
    else:
        with pytest.raises(NotificationError, match=f"HTTP {status}"):
            notifier.send(notification)
    assert len(adapter.requests) == 1
    assert adapter.responses[0].raw.closed


def test_hook_bounds_redirect_body_before_requests_consumes_it():
    adapter = ResponseAdapter(
        b"x" * 200_000,
        status=302,
        headers={"Location": "https://other.invalid/"},
    )
    with pytest.raises(ResponseSizeExceeded):
        session_for(adapter).get(
            "https://source.invalid/",
            stream=True,
            allow_redirects=False,
            hooks={"response": bounded_response(100_000)},
        )
    assert len(adapter.requests) == 1
    assert adapter.responses[0].raw.tell() == 2 * 64 * 1024


@pytest.mark.parametrize("destination", ["/other/", "https://other.invalid/"])
def test_source_rejects_redirect_without_following_or_parsing(destination):
    adapter = ResponseAdapter(
        b"<html>wrong source</html>", status=302, headers={"Location": destination}
    )
    client = SourceClient(settings(), session=session_for(adapter))
    with pytest.raises(SourceFetchError, match="HTTP 302"):
        client.fetch_quarter_html("2026", "夏")
    assert len(adapter.requests) == 1
    assert adapter.responses[0].raw.closed


@pytest.mark.parametrize(
    "provider",
    ["source", "image", "bangumi", "nhk", "github", "anilist", "discord", "production"],
)
def test_every_upstream_installs_bound_before_eager_loading(provider, monkeypatch):
    adapter = ResponseAdapter(headers={"Content-Length": str(17 * 1024 * 1024)})
    session = session_for(adapter)
    if provider == "source":
        client = SourceClient(settings(), session=session)

        def call():
            return client.fetch_quarter_html("2026", "夏")

        error = SourceFetchError
    elif provider == "image":
        client = SafeImageDownloader(settings())
        client._thread_local.session = session
        monkeypatch.setattr(client, "_validate_url", lambda url: None)

        def call():
            return client.download("https://image.invalid/cover.png")

        error = ImageStoreError
    elif provider == "bangumi":
        client = BangumiClient(session=session)

        def call():
            return client._get("/subjects/1")

        error = MetadataUnavailable
    elif provider == "nhk":
        client = NHKCatalogueClient(session=session)

        def call():
            return client._get("https://official.invalid/")

        error = OfficialSourceUnavailable
    elif provider == "github":
        client = GitHubReviewClient("owner/repo", "test-token", session=session)

        def call():
            return client.request("GET", "/issues")

        error = RuntimeError
    elif provider == "production":
        client = PublicReader()
        client.local.session = session

        def call():
            return client("https://public.invalid/index.html")

        error = ResponseSizeExceeded
    elif provider == "discord":
        monkeypatch.setattr("services.notifier.requests.post", session.post)
        client = DiscordNotifier("https://notify.invalid/webhook/test")

        def call():
            return client.send(Notification(status="FAILURE", year="2026", season="夏"))

        error = NotificationError
    else:
        # Only a local mock exercises this opt-in client; AniList stays disabled.
        client = AniListClient(session=session)

        def call():
            return client.fetch([1])

        error = SecondarySourceError
    with pytest.raises(error):
        call()
    assert len(adapter.requests) == 1
    assert adapter.requests[0][1]["stream"] is True
    assert adapter.responses[0].raw.tell() == 0
    assert adapter.responses[0].raw.closed
