from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import generate_static
from models import TAIPEI_TZ, Anime
from services import anime_service as anime_service_module
from services.anime_service import AnimeCrawlerService, parse_date_time
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import CrawlerError, NotificationError
from services.notifier import DiscordNotifier, Notification
from services.settings import ProjectPaths


class _CacheSpy:
    def __init__(self) -> None:
        self.save_count = 0

    def save_if_changed(self) -> bool:
        self.save_count += 1
        return False


class _ImageStoreSpy:
    def __init__(self) -> None:
        self.quota_checked = False

    def assert_quota_available(self) -> None:
        self.quota_checked = True

    def store(self, source_url: str, anime_name: str) -> str:
        raise AssertionError("invalid parser data must not reach image storage")


class _StaticSourceClient:
    def __init__(self, document: str) -> None:
        self.document = document

    def fetch_quarter_html(self, year: str, season: str) -> tuple[str, str]:
        return "https://acgsecrets.hk/bangumi/202607/", self.document


def test_all_item_failures_raise_instead_of_returning_an_error_list(
    fixture_dir: Path,
) -> None:
    document = (fixture_dir / "acgsecrets_202607_minimal.html").read_text(
        encoding="utf-8"
    )
    document = document.replace(
        ' acgs-bangumi-anime-id="anime-2200"',
        "",
        1,
    )
    cache = _CacheSpy()
    image_store = _ImageStoreSpy()
    crawler = AnimeCrawlerService(
        settings=SimpleNamespace(max_workers=1),
        source_client=_StaticSourceClient(document),
        image_store=image_store,
        cache=cache,
    )

    with pytest.raises(CrawlerError, match="produced no valid records"):
        crawler.fetch_quarter("2026", "夏")

    assert image_store.quota_checked is True
    assert cache.save_count == 1


def test_static_crawl_orchestrator_re_raises_crawler_failure(
    project_paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = CrawlerError("simulated crawler failure")

    class FailingCrawler:
        def fetch_quarter(self, year: str, season: str) -> None:
            raise failure

    notifications: list[object] = []

    class NotifierSpy:
        def __init__(self, webhook_url: str | None) -> None:
            del webhook_url

        def send(self, notification: object) -> bool:
            notifications.append(notification)
            return True

    monkeypatch.setattr(
        anime_service_module.AnimeCrawlerService,
        "from_environment",
        classmethod(lambda cls: FailingCrawler()),
    )
    monkeypatch.setattr(generate_static, "DiscordNotifier", NotifierSpy)
    monkeypatch.setattr(
        generate_static.sentry_sdk,
        "capture_exception",
        lambda exc: None,
    )
    repository = DataRepository(
        project_paths.data_dir,
        DataQualityPolicy(),
    )

    with pytest.raises(CrawlerError, match="simulated crawler failure"):
        generate_static.crawl_quarters(
            project_paths,
            repository,
            datetime(2026, 7, 10, 12, 0, tzinfo=TAIPEI_TZ),
        )

    assert notifications
    assert notifications[-1].status == "FAILURE"
    assert "simulated crawler failure" in notifications[-1].message


def test_configured_discord_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingResponse:
        def raise_for_status(self) -> None:
            import requests

            raise requests.HTTPError("simulated webhook failure")

    monkeypatch.setattr(
        "services.notifier.requests.post",
        lambda *args, **kwargs: FailingResponse(),
    )

    with pytest.raises(NotificationError, match="Discord notification failed"):
        DiscordNotifier("https://discord.example/webhook").send(
            Notification(status="SUCCESS", year="2026", season="夏")
        )


def test_same_times_have_a_stable_id_and_name_tie_breaker() -> None:
    def anime(bangumi_id: str, name: str) -> Anime:
        return Anime(
            bangumi_id=bangumi_id,
            anime_name=name,
            anime_image_url=(
                "https://res.cloudinary.com/test/image/upload/"
                f"anime_covers/{bangumi_id}.webp"
            ),
            premiere_date="一",
            premiere_time="22:00",
            story="測試",
        )

    records = [anime("anime-2", "乙"), anime("anime-1", "甲")]

    assert [record.bangumi_id for record in sorted(records, key=parse_date_time)] == [
        "anime-1",
        "anime-2",
    ]
