from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import generate_static
from models import TAIPEI_TZ, Anime
from services import anime_service as anime_service_module
from services.anime_service import AnimeCrawlerService, parse_date_time
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import CrawlerError, ImageStoreError, ItemParseError
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


def _managed_image_url(seed: str) -> str:
    public_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"https://res.cloudinary.com/test/image/upload/anime_covers/{public_id}.webp"


def _valid_anime(bangumi_id: str = "anime-2200") -> Anime:
    return Anime(
        bangumi_id=bangumi_id,
        anime_name=f"測試動畫 {bangumi_id}",
        anime_image_url=_managed_image_url(bangumi_id),
        premiere_date="一",
        premiere_time="22:00",
        story="測試",
    )


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


def test_only_item_parse_errors_may_be_recorded_as_partial_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    image_store = _ImageStoreSpy()
    crawler = AnimeCrawlerService(
        settings=SimpleNamespace(max_workers=1),
        source_client=_StaticSourceClient("unused"),
        image_store=image_store,
        cache=cache,
    )
    monkeypatch.setattr(
        anime_service_module,
        "extract_item_html",
        lambda document: ["bad-card", "good-card"],
    )

    def process(item_html: str) -> Anime:
        if item_html == "bad-card":
            raise ItemParseError("known card format problem")
        return _valid_anime()

    monkeypatch.setattr(crawler, "_process_item", process)

    result = crawler.fetch_quarter("2026", "夏")

    assert [record.bangumi_id for record in result.anime_list] == ["anime-2200"]
    assert result.parse_failure_count == 1
    assert result.failures[0].error_type == "ItemParseError"
    assert cache.save_count == 1


def test_system_errors_always_abort_the_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _CacheSpy()
    image_store = _ImageStoreSpy()
    crawler = AnimeCrawlerService(
        settings=SimpleNamespace(max_workers=1),
        source_client=_StaticSourceClient("unused"),
        image_store=image_store,
        cache=cache,
    )
    monkeypatch.setattr(
        anime_service_module,
        "extract_item_html",
        lambda document: ["good-card", "image-store-error"],
    )

    def process(item_html: str) -> Anime:
        if item_html == "image-store-error":
            raise ImageStoreError("simulated system failure")
        return _valid_anime()

    monkeypatch.setattr(crawler, "_process_item", process)

    with pytest.raises(ImageStoreError, match="simulated system failure"):
        crawler.fetch_quarter("2026", "夏")

    assert cache.save_count == 1


def test_static_crawl_orchestrator_re_raises_crawler_failure(
    project_paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = CrawlerError("simulated crawler failure")

    class FailingCrawler:
        def fetch_quarter(self, year: str, season: str) -> None:
            raise failure

    monkeypatch.setattr(
        anime_service_module.AnimeCrawlerService,
        "from_environment",
        classmethod(lambda cls: FailingCrawler()),
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


def test_crawl_summary_is_published_only_after_the_full_build_succeeds(
    project_paths: ProjectPaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = generate_static.CrawlSummary(
        processed_quarters=6,
        changed_quarters=2,
        total_records=321,
        parse_failures=4,
    )
    repository = SimpleNamespace(validate_all=lambda: [])
    github_output = tmp_path / "github-output.txt"

    monkeypatch.setenv("BUILD_ONLY", "false")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setattr(generate_static, "load_dotenv", lambda: None)
    monkeypatch.setattr(generate_static, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        generate_static.ProjectPaths,
        "from_environment",
        classmethod(lambda cls: project_paths),
    )
    monkeypatch.setattr(
        generate_static.CrawlerSettings,
        "from_environment",
        classmethod(
            lambda cls: SimpleNamespace(
                minimum_count_ratio=0.5,
                maximum_parse_failure_ratio=0.2,
                maximum_fallback_id_ratio=0.1,
            )
        ),
    )
    monkeypatch.setattr(
        generate_static,
        "DataRepository",
        lambda *args, **kwargs: repository,
    )
    monkeypatch.setattr(
        generate_static,
        "crawl_quarters",
        lambda paths, data_repository, now: summary,
    )
    monkeypatch.setattr(generate_static, "sync_static_assets", lambda paths: None)

    def fail_render(*args: object, **kwargs: object) -> Path:
        raise RuntimeError("simulated final render failure")

    monkeypatch.setattr(generate_static, "render_index", fail_render)

    with pytest.raises(RuntimeError, match="simulated final render failure"):
        generate_static.generate_static_files()

    assert not github_output.exists()

    monkeypatch.setattr(
        generate_static,
        "render_index",
        lambda paths, data_repository, now: project_paths.output_dir / "index.html",
    )

    generate_static.generate_static_files()

    assert github_output.read_bytes() == (
        b"processed_quarters=6\n"
        b"changed_quarters=2\n"
        b"record_count=321\n"
        b"parse_failures=4\n"
    )


def test_same_times_have_a_stable_id_and_name_tie_breaker() -> None:
    def anime(bangumi_id: str, name: str) -> Anime:
        return Anime(
            bangumi_id=bangumi_id,
            anime_name=name,
            anime_image_url=_managed_image_url(bangumi_id),
            premiere_date="一",
            premiere_time="22:00",
            story="測試",
        )

    records = [anime("anime-2", "乙"), anime("anime-1", "甲")]

    assert [record.bangumi_id for record in sorted(records, key=parse_date_time)] == [
        "anime-1",
        "anime-2",
    ]
