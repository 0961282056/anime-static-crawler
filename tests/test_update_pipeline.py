import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from models import TAIPEI_TZ, Anime, AnimeCandidate, EpisodeMetadata, ExternalIds
from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_metadata import BangumiClient, EpisodeEnricher
from services.episode_source_reviews import source_reviews
from services.errors import ImageStoreError, SourceFetchError
from services.official_catalogue import OfficialSourceUnavailable
from services.update_pipeline import run_update_pipeline

NOW = datetime(2026, 10, 19, 9, tzinfo=TAIPEI_TZ)
URL = "https://acgsecrets.hk/bangumi/202610/"


def metadata_only_pipeline(paths, repo, enricher, monkeypatch):
    monkeypatch.setattr("services.update_pipeline.EpisodeEnricher", lambda: enricher)
    return run_update_pipeline(
        paths,
        repo,
        NOW,
        [],
        future=lambda *args: False,
        source=SimpleNamespace(
            fetch_quarter_html=lambda *args: pytest.fail("unexpected source request")
        ),
        image_factory=lambda: pytest.fail("unexpected image request"),
    )


def test_budget_deferral_reports_partial_without_changing_metadata_or_check_times(
    project_paths, anime_record_factory, monkeypatch
):
    repo = DataRepository(project_paths.data_dir, DataQualityPolicy())
    old = Anime(
        **anime_record_factory(1),
        external_ids=ExternalIds(bangumi=123),
        episodes=EpisodeMetadata(
            airing_status="airing",
            fetch_state="ok",
            checked_at=NOW - timedelta(days=1),
            attempted_at=NOW - timedelta(days=1),
        ),
    )
    path = repo.write_quarter(
        year="2026",
        season="秋",
        records=[old],
        source_url=URL,
        source_count=1,
        parse_failure_count=0,
    ).path
    before = path.read_bytes()
    enricher = EpisodeEnricher(BangumiClient(request_budget=0))
    result = metadata_only_pipeline(project_paths, repo, enricher, monkeypatch)
    report = json.loads(
        (project_paths.root / "audit-output/update-pipeline.json").read_text()
    )
    assert result["partial_failures"] == 1
    assert report["status"] == "partial"
    assert report["metadata_failures"] == 0 and report["metadata_deferred"] == 1
    assert report["metadata_issues"] == [
        {
            "kind": "deferred",
            "card_id": old.bangumi_id,
            "anime_name": old.anime_name,
            "original_name": old.original_name,
            "subject_id": 123,
            "provider": "bangumi",
            "code": "request_time_budget_exhausted",
        }
    ]
    assert path.read_bytes() == before


def test_official_failure_report_identifies_source_and_status_without_exception_text(
    project_paths, anime_record_factory, monkeypatch, caplog
):
    repo = DataRepository(project_paths.data_dir, DataQualityPolicy())
    review = next(r for r in source_reviews() if r["card_id"] == "anime-663")
    old = Anime(
        **{
            **anime_record_factory(663),
            "bangumi_id": "anime-663",
            "anime_name": review["anime_name"],
            "original_name": review["original_name"],
            "external_ids": {"bangumi": 315772},
            "episodes": {
                **review["metadata"],
                "fetch_state": "stale",
                "attempted_at": NOW - timedelta(days=2),
            },
        }
    )
    for year, season, month in [("2020", "秋", "10"), ("2021", "冬", "01")]:
        repo.write_quarter(
            year=year,
            season=season,
            records=[old],
            source_url=f"https://acgsecrets.hk/bangumi/{year}{month}/",
            source_count=1,
            parse_failure_count=0,
        )
    official = Mock()
    secret_message = "private transport URL with token=do-not-publish"
    official.fetch.side_effect = OfficialSourceUnavailable(
        secret_message,
        code="official_http_error",
        http_status=403,
    )
    enricher = EpisodeEnricher(
        BangumiClient(request_budget=0), official_client=official
    )
    result = metadata_only_pipeline(project_paths, repo, enricher, monkeypatch)
    report_text = (project_paths.root / "audit-output/update-pipeline.json").read_text()
    report = json.loads(report_text)
    assert result["partial_failures"] == report["metadata_failures"] == 1
    assert report["metadata_deferred"] == 0
    assert report["metadata_issues"][0]["provider"] == "nhk"
    assert report["metadata_issues"][0]["card_id"] == "anime-663"
    assert report["metadata_issues"][0]["http_status"] == 403
    assert report["metadata_issues"][0]["code"] == "official_http_error"
    assert secret_message not in report_text and secret_message not in caplog.text
    official.fetch.assert_called_once()
    for path in repo.validate_all():
        info = repo.load_path(path).anime_list[0].episodes
        assert info.aired_episodes == old.episodes.aired_episodes
        assert info.checked_at == old.episodes.checked_at
        assert info.attempted_at == NOW and info.fetch_state == "stale"


@pytest.fixture
def setup_pipeline(project_paths, anime_record_factory, monkeypatch):
    repo = DataRepository(project_paths.data_dir, DataQualityPolicy())
    raw = anime_record_factory(1, name="同一作品")
    old = Anime(**raw, original_name="Same Work")
    repo.write_quarter(
        year="2026",
        season="秋",
        records=[old],
        source_url=URL,
        source_count=1,
        parse_failure_count=0,
    )
    candidate = AnimeCandidate(
        **{k: v for k, v in raw.items() if k != "anime_image_url"},
        original_name="Same Work",
        source_image_url="https://static.acgsecrets.hk/new.png",
    )
    monkeypatch.setattr("services.update_pipeline.extract_item_html", lambda html: html)
    monkeypatch.setattr("services.update_pipeline.parse_anime_item", lambda card: card)
    return project_paths, repo, old, candidate


def run(setup, cards, factory, refresh):
    paths, repo, _, _ = setup
    return run_update_pipeline(
        paths,
        repo,
        NOW,
        [("2026", "秋")],
        future=lambda *args: False,
        source=SimpleNamespace(fetch_quarter_html=lambda *args: (URL, cards)),
        image_factory=factory,
        refresh=refresh,
    )


def test_source_failure_does_not_block_independent_episode_refresh(setup_pipeline):
    paths, repo, old, candidate = setup_pipeline
    calls = []

    def fail(*args):
        raise SourceFetchError("offline")

    result = run_update_pipeline(
        paths,
        repo,
        NOW,
        [("2026", "秋")],
        future=lambda *args: False,
        source=SimpleNamespace(fetch_quarter_html=fail),
        image_factory=lambda: pytest.fail("image stage must not run"),
        refresh=lambda *args: calls.append("episodes") or set(),
    )
    assert calls == ["episodes"]
    assert result["partial_failures"] == 1
    assert repo.load_quarter("2026", "秋").anime_list == [old]
    assert (
        json.loads((paths.root / "audit-output/update-pipeline.json").read_text())[
            "status"
        ]
        == "partial"
    )


def test_bad_card_link_retains_quarter_and_continues_other_stages(
    project_paths, anime_record_factory, fixture_dir
):
    from services.parser import extract_item_html, parse_anime_item

    repo = DataRepository(project_paths.data_dir, DataQualityPolicy())
    document = (fixture_dir / "acgsecrets_202607_minimal.html").read_text()
    candidate = parse_anime_item(extract_item_html(document)[0])
    old = Anime(
        **{
            key: value
            for key, value in candidate.model_dump().items()
            if key != "source_image_url"
        },
        anime_image_url=anime_record_factory(2200)["anime_image_url"],
    )
    repo.write_quarter(
        year="2026",
        season="夏",
        records=[old],
        source_url="https://acgsecrets.hk/bangumi/202607/",
        source_count=1,
        parse_failure_count=0,
    )
    before = (project_paths.data_dir / "2026_夏.json").read_bytes()
    bad = document.replace(
        '<div class="anime_content">',
        '<div class="anime_content"><div class="anime_link_group">'
        '<a href="https://[invalid">link</a></div>',
    )
    events = []

    def fetch(year, season):
        events.append("source-" + season)
        return (
            f"https://acgsecrets.hk/bangumi/{year}{'07' if season == '夏' else '10'}/",
            bad if season == "夏" else document,
        )

    class Images:
        def assert_quota_available(self):
            events.append("quota")

        def store(self, *args):
            events.append("image")
            return old.anime_image_url

    result = run_update_pipeline(
        project_paths,
        repo,
        NOW,
        [("2026", "夏"), ("2026", "秋")],
        future=lambda *args: False,
        source=SimpleNamespace(fetch_quarter_html=fetch),
        image_factory=Images,
        refresh=lambda *args: events.append("episodes") or set(),
    )
    assert events == ["source-夏", "source-秋", "quota", "image", "episodes"]
    assert (project_paths.data_dir / "2026_夏.json").read_bytes() == before
    assert repo.load_quarter("2026", "秋").anime_list == [old]
    assert result["partial_failures"] == result["parse_failures"] == 1
    report = json.loads(
        (project_paths.root / "audit-output/update-pipeline.json").read_text()
    )
    assert report["status"] == "partial" and report["episodes"] == "completed"
    assert report["collection"][0]["error"] == "ItemParseError"


def test_cached_images_do_not_require_cloudinary_credentials(setup_pipeline):
    paths, repo, old, candidate = setup_pipeline
    import hashlib

    from services.cache_repository import CacheRepository

    cache = CacheRepository(paths.cache_file)
    cache.set(
        "source_" + hashlib.sha256(candidate.source_image_url.encode()).hexdigest(),
        old.anime_image_url,
    )
    cache.save_if_changed()
    result = run(
        setup_pipeline,
        [candidate],
        lambda: pytest.fail("cache hit must not construct Cloudinary"),
        lambda *args: set(),
    )
    assert result["partial_failures"] == 0


def test_missing_source_summary_keeps_existing_text_and_pipeline_succeeds(
    setup_pipeline,
):
    paths, repo, old, candidate = setup_pipeline
    refreshed = []
    result = run(
        setup_pipeline,
        [candidate.model_copy(update={"story": "暫無簡介"})],
        lambda: SimpleNamespace(
            assert_quota_available=lambda: None,
            store=lambda *args: old.anime_image_url,
        ),
        lambda *args: refreshed.append(True) or set(),
    )
    saved = repo.load_quarter("2026", "秋").anime_list[0]
    assert saved.story == old.story
    assert saved.story_source is None
    assert refreshed and result["partial_failures"] == 0
    assert result["changed_quarters"] == 0


def test_image_outage_reuses_only_exact_existing_work(setup_pipeline):
    paths, repo, old, candidate = setup_pipeline

    def fail():
        raise ImageStoreError("quota endpoint offline")

    refreshed = []
    changed = candidate.model_copy(update={"story": "來源有新的簡介"})
    result = run(
        setup_pipeline, [changed], fail, lambda *args: refreshed.append(True) or set()
    )
    saved = repo.load_quarter("2026", "秋").anime_list[0]
    assert saved.anime_image_url == old.anime_image_url
    assert saved.story == changed.story
    assert refreshed and result["partial_failures"] > 0


@pytest.mark.parametrize("port", ["not-a-port", "70000"])
def test_bad_image_port_keeps_existing_cover_and_still_refreshes_episodes(
    setup_pipeline, monkeypatch, port
):
    from services.http_client import SafeImageDownloader

    paths, repo, old, candidate = setup_pipeline
    downloader = SafeImageDownloader(SimpleNamespace())
    downloader._thread_local.session = SimpleNamespace(
        get=lambda *args, **kwargs: pytest.fail("Invalid URL must not reach HTTP")
    )
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: pytest.fail("Invalid URL must not reach DNS"),
    )
    refreshed = []
    result = run(
        setup_pipeline,
        [
            candidate.model_copy(
                update={
                    "source_image_url": f"https://static.acgsecrets.hk:{port}/cover.jpg"
                }
            )
        ],
        lambda: SimpleNamespace(
            assert_quota_available=lambda: None,
            store=lambda url, name: downloader.download(url),
        ),
        lambda *args: refreshed.append(True) or set(),
    )
    assert refreshed and result["partial_failures"] == 1
    assert (
        repo.load_quarter("2026", "秋").anime_list[0].anime_image_url
        == old.anime_image_url
    )
    report = json.loads((paths.root / "audit-output/update-pipeline.json").read_text())
    assert report["status"] == "partial" and report["episodes"] == "completed"


@pytest.mark.parametrize(
    "change", [{"bangumi_id": "anime-2"}, {"anime_name": "另一作品"}]
)
def test_unresolved_new_or_different_work_retains_complete_quarter(
    setup_pipeline, change
):
    paths, repo, old, candidate = setup_pipeline
    other = candidate.model_copy(update=change)

    def fail():
        raise ImageStoreError("offline")

    calls = []
    cards = [candidate, other] if change.get("bangumi_id") else [other]
    run(setup_pipeline, cards, fail, lambda *args: calls.append("episodes") or set())
    assert calls == ["episodes"]
    assert repo.load_quarter("2026", "秋").anime_list == [old]


def test_source_card_disappearance_is_not_hidden_by_independent_stages(setup_pipeline):
    paths, repo, old, candidate = setup_pipeline
    changed = candidate.model_copy(update={"bangumi_id": "anime-2"})
    result = run(
        setup_pipeline,
        [changed],
        lambda: pytest.fail("missing card must be caught before images"),
        lambda *args: set(),
    )
    assert result["partial_failures"] == 1
    assert repo.load_quarter("2026", "秋").anime_list == [old]


def test_unexpected_filesystem_error_is_not_converted_to_partial_success(
    setup_pipeline,
):
    def fail():
        raise PermissionError("disk denied")

    with pytest.raises(PermissionError):
        run(
            setup_pipeline,
            [setup_pipeline[3]],
            fail,
            lambda *args: pytest.fail("fatal failure must abort"),
        )


def test_schema_invalid_source_card_retains_quarter_and_still_refreshes(
    setup_pipeline, monkeypatch
):
    def invalid(card):
        return AnimeCandidate.model_validate({})

    monkeypatch.setattr("services.update_pipeline.parse_anime_item", invalid)
    calls = []
    result = run(
        setup_pipeline,
        [setup_pipeline[3]],
        lambda: pytest.fail("invalid source must not reach images"),
        lambda *args: calls.append(True) or set(),
    )
    assert calls and result["parse_failures"] == 1


def test_all_collection_precedes_images_and_quota_is_rechecked_for_each_uploading_quarter(
    setup_pipeline,
):
    paths, repo, old, candidate = setup_pipeline
    events = []

    def fetch(year, season):
        events.append("collect-" + season)
        return (
            f"https://acgsecrets.hk/bangumi/{year}{'10' if season == '秋' else '07'}/",
            [
                candidate.model_copy(
                    update={
                        "source_image_url": f"https://static.acgsecrets.hk/{season}.png"
                    }
                )
            ],
        )

    class Images:
        def assert_quota_available(self):
            events.append("quota")

        def store(self, *args):
            events.append("image")
            return old.anime_image_url

    run_update_pipeline(
        paths,
        repo,
        NOW,
        [("2026", "秋"), ("2026", "夏")],
        future=lambda *args: False,
        source=SimpleNamespace(fetch_quarter_html=fetch),
        image_factory=Images,
        refresh=lambda *args: events.append("episodes") or set(),
    )
    assert events[:2] == ["collect-秋", "collect-夏"]
    assert events.count("quota") == 2
    assert events[-1] == "episodes"
    assert repo.load_quarter("2026", "夏") is not None
