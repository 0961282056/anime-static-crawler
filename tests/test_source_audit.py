from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from models import TAIPEI_TZ, Anime, EpisodeMetadata, ExternalIds
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import ItemParseError, SourceFetchError
from services.secondary_source import (
    AniListClient,
    SecondarySourceError,
    compare_secondary,
)
from services.source_audit import (
    audit_quarter,
    manual_review_entry,
    optional_field_availability,
    provenance,
    run_source_audit,
    write_audit_report,
)

NOW = datetime(2026, 10, 16, 12, tzinfo=TAIPEI_TZ)


@pytest.fixture
def anime(anime_record_factory):
    return Anime(
        **anime_record_factory(1),
        original_name="作品 第2期",
        external_ids=ExternalIds(bangumi=10, anilist=20),
        episodes=EpisodeMetadata(
            total_episodes=12,
            aired_episodes=12,
            airing_status="finished",
            episode_count_status="confirmed",
            fetch_state="ok",
            checked_at=NOW - timedelta(days=10),
            attempted_at=NOW,
        ),
    )


@pytest.fixture
def media():
    return {
        "id": 20,
        "type": "ANIME",
        "format": "TV",
        "title": {"native": "作品 第2期"},
        "episodes": 12,
        "status": "FINISHED",
        "startDate": {"year": 2024, "month": 1, "day": 1},
        "endDate": {"year": 2024, "month": 3, "day": 20},
    }


def compare(anime, media, **kwargs):
    return compare_secondary(
        anime, media, subject_id=20, quarter="2024_冬", today=NOW.date(), **kwargs
    )


def card(anime, *, anilist=20):
    return f'''<div id="acgs-anime-list"><div class="acgs-anime-block CV-search" acgs-bangumi-anime-id="{anime.bangumi_id}" weektoday="一" weekairtime="1200">
    <h3 class="entity_localized_name">{anime.anime_name}</h3><span class="entity_original_name">{anime.original_name}</span>
    <div class="anime_cover_image" acgs-img-data-url="https://static.acgsecrets.hk/cover.jpg"></div>
    <div class="anime_link_group"><a href="https://bgm.tv/subject/10">BG</a><a href="https://anilist.co/anime/{anilist}">AL</a></div>
    </div></div>'''


def test_coverage_reports_additions_removals_changes_without_writing(anime):
    renamed = anime.model_copy(update={"anime_name": "另一作品"})
    new = anime.model_copy(update={"bangumi_id": "anime-2"})
    source = card(renamed) + card(new)
    removed = anime.model_copy(update={"bangumi_id": "anime-3"})
    result, _ = audit_quarter(SimpleNamespace(anime_list=[anime, removed]), source)
    assert {row["kind"] for row in result["differences"]} == {
        "changed",
        "missing_locally",
        "absent_from_source",
    }
    assert (
        result["differences"][0]["fields"]["anime_name"]["stored"] == anime.anime_name
    )
    assert anime.anime_name != renamed.anime_name


def test_duplicate_or_empty_source_does_not_imply_mass_deletion(anime):
    for document in (card(anime) + card(anime), "<html></html>"):
        with pytest.raises(ItemParseError):
            audit_quarter(SimpleNamespace(anime_list=[anime]), document)


def test_exact_identity_and_counts_corroborate_but_do_not_modify(anime, media):
    before = anime.model_dump_json()
    assert compare(anime, media)["result"] == "corroborated"
    assert anime.model_dump_json() == before


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("title", {"native": "作品 第1期"}, "original_title_not_exact"),
        ("format", "OVA", "format_requires_scope_review"),
        (
            "startDate",
            {"year": 2025, "month": 1, "day": 1},
            "starts_after_record_quarter",
        ),
        (
            "startDate",
            {"year": 2024, "month": None, "day": None},
            "start_date_unavailable",
        ),
        ("endDate", {"year": 2027, "month": 1, "day": 1}, "end_date_inconsistent"),
        ("episodes", True, "invalid_episode_total"),
        ("id", 21, "identity_contract_mismatch"),
        ("status", "UNRECOGNIZED", "status_unavailable"),
    ],
)
def test_ambiguous_identity_scope_or_invalid_facts_are_not_compared(
    anime, media, field, value, reason
):
    result = compare(anime, {**media, field: value})
    assert result["result"] == "needs_identity_or_scope_review"
    assert reason in result["reasons"]


def test_manual_scope_cannot_be_overridden_by_matching_title(anime, media):
    assert (
        "manually_reviewed_scope_takes_precedence"
        in compare(anime, media, reviewed_scope=True)["reasons"]
    )


def test_count_and_status_disagreement_and_unknown_are_separate(anime, media):
    result = compare(anime, {**media, "episodes": 16, "status": "RELEASING"})
    assert result["result"] == "difference"
    assert set(result["reasons"]) == {"total_disagreement", "status_disagreement"}
    result = compare(anime, {**media, "episodes": None})
    assert result["reasons"] == ["secondary_total_unknown"]
    assert compare(anime, None)["result"] == "unavailable"


def test_provenance_does_not_confuse_attempt_with_verification(anime):
    result = provenance(anime)
    assert result["last_verified_at"] == anime.episodes.checked_at.isoformat()
    assert result["last_attempt_at"] == NOW.isoformat()
    assert result["source_snapshot_at"] is None
    assert result["verification_method"] == "legacy_method_not_structured"


def test_missing_optional_field_distinguishes_absence_from_acquisition_failure(anime):
    missing = anime.model_copy(update={"story": "暫無簡介"})
    assert (
        optional_field_availability(missing, anime)["story"]
        == "available_at_source_not_imported"
    )
    assert optional_field_availability(missing, missing)["story"] == "absent_at_source"
    assert (
        optional_field_availability(missing, None)["story"]
        == "source_fetch_or_parse_failed"
    )
    assert (
        optional_field_availability(missing, None, "source_card_absent")["story"]
        == "source_card_absent"
    )


def test_snapshot_queue_uses_oldest_success_not_attempt(monkeypatch, anime):
    monkeypatch.setattr(
        "services.source_audit.reviewed_source",
        lambda _: {
            "mode": "snapshot",
            "metadata": {
                "source_urls": ["https://official.example/story"],
                "note": "TV版",
            },
        },
    )
    assert manual_review_entry(anime, NOW)["interval_days"] == 90
    assert manual_review_entry(anime, NOW)["due"] is False
    unfinished = anime.model_copy(
        update={
            "episodes": anime.episodes.model_copy(update={"airing_status": "unknown"})
        }
    )
    review = manual_review_entry(unfinished, NOW)
    assert review["due"] is True
    assert review["next_review_at"] == (NOW - timedelta(days=3)).isoformat()
    assert provenance(anime)["verification_method"] == "manual_official_review"


class Response:
    status_code = 200
    closed = False
    headers = {"content-type": "application/json"}
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_anilist_client_is_bounded_and_rejects_redirects(media):
    response = Response({"data": {"Page": {"media": [media]}}})
    session = Session(response)
    client = AniListClient(session, max_requests=1)
    assert client.fetch([20])[20] == media
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["json"]["variables"] == {"ids": [20]}
    assert response.closed
    with pytest.raises(SecondarySourceError, match="request_budget"):
        client.fetch([20])
    with pytest.raises(ValueError):
        client.fetch([True])
    response.status_code = 429
    with pytest.raises(SecondarySourceError, match="http_429"):
        AniListClient(session).fetch([20])


def test_secondary_time_budget_starts_when_phase_begins(monkeypatch, media):
    clock = [0.0]
    monkeypatch.setattr("services.secondary_source.time.monotonic", lambda: clock[0])
    session = Session(Response({"data": {"Page": {"media": [media]}}}))
    client = AniListClient(session)
    clock[0] = 1000.0  # Historical HTML checks may take several minutes first.
    assert client.fetch([20]) == {20: media}
    clock[0] += 181
    with pytest.raises(SecondarySourceError, match="request_budget"):
        client.fetch([20])


def test_future_planned_end_is_not_a_source_error(anime, media):
    upcoming = anime.model_copy(update={"episodes": EpisodeMetadata()})
    result = compare(
        upcoming,
        {
            **media,
            "status": "NOT_YET_RELEASED",
            "endDate": {"year": 2027, "month": 3, "day": 1},
        },
    )
    assert "end_date_inconsistent" not in result["reasons"]


@pytest.mark.parametrize(
    "payload",
    [
        {"errors": [{"message": "failure"}], "data": {"Page": {"media": []}}},
        {"data": None},
        {"data": {"Page": {"media": [None]}}},
        {"data": {"Page": {"media": [{"id": 999, "type": "ANIME"}]}}},
        {"data": {"Page": {"media": [{"id": 20, "type": "MANGA"}]}}},
        {"data": {"Page": {"media": [{"id": 20, "type": "ANIME"}] * 2}}},
    ],
)
def test_partial_graphql_and_foreign_or_duplicate_ids_are_failures(payload):
    with pytest.raises(SecondarySourceError):
        AniListClient(Session(Response(payload))).fetch([20])


def save(repository, anime, year="2024", season="冬"):
    repository.write_quarter(
        year=year,
        season=season,
        records=[anime],
        source_url=f"https://acgsecrets.hk/bangumi/{year}01/",
        source_count=1,
        parse_failure_count=0,
    )


def test_full_audit_uses_explicit_missing_link_and_preserves_all_files(
    tmp_path, anime, media
):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    no_link = anime.model_copy(update={"external_ids": ExternalIds(bangumi=10)})
    save(repository, no_link)
    save(repository, no_link, year="2025")
    before = {p: p.read_bytes() for p in repository.validate_all()}
    calls = []

    class Provider:
        def fetch(self, ids):
            calls.append(ids)
            return {20: media}

    source = SimpleNamespace(
        fetch_quarter_html=lambda year, season: (
            f"https://acgsecrets.hk/bangumi/{year}01/",
            card(anime),
        )
    )
    report = run_source_audit(
        repository.data_dir, now=NOW, source_client=source, secondary_client=Provider()
    )
    assert calls == [[20]]
    assert report["summary"]["records"] == 2
    assert report["summary"]["unique_identities"] == 1
    assert report["summary"]["coverage_differences"] == 2
    assert len(report["identities"][0]["secondary"]) == 2
    assert before == {p: p.read_bytes() for p in before}
    write_audit_report(report, tmp_path / "report")
    assert (
        json.loads((tmp_path / "report/source-audit.json").read_text())[
            "published_data_changed"
        ]
        is False
    )


def test_optional_availability_does_not_require_source_to_repeat_resolved_bg_id(
    tmp_path, anime
):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    missing_story = anime.model_copy(
        update={"story": "暫無簡介", "external_ids": ExternalIds(bangumi=999)}
    )
    save(repository, missing_story)
    source = SimpleNamespace(
        fetch_quarter_html=lambda *_: (
            "https://acgsecrets.hk/bangumi/202401/",
            card(anime),
        )
    )
    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=source,
        secondary_client=SimpleNamespace(fetch=lambda _: {}),
    )
    assert (
        report["identities"][0]["primary_observations"][0][
            "optional_field_availability"
        ]["story"]
        == "absent_at_source"
    )
    assert (
        report["identities"][0]["secondary"] == []
    )  # Secondary ID inheritance remains stricter.


def test_failures_are_partial_report_not_false_coverage_or_freshness(tmp_path, anime):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    save(repository, anime)

    class Source:
        def fetch_quarter_html(self, year, season):
            raise SourceFetchError("offline")

    class Provider:
        def fetch(self, ids):
            raise SecondarySourceError("http_429")

    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=Source(),
        secondary_client=Provider(),
    )
    assert report["summary"]["errors"] == 2
    quarter = report["quarters"][0]
    assert quarter["checked_at"] is None
    assert "differences" not in quarter
    secondary = report["identities"][0]["secondary"][0]
    assert secondary["checked_at"] is None
    assert secondary["result"] == "fetch_failed"


def test_changed_identity_does_not_inherit_new_source_link(tmp_path, anime):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    save(repository, anime.model_copy(update={"external_ids": ExternalIds(bangumi=10)}))
    source = SimpleNamespace(
        fetch_quarter_html=lambda *_: (
            "https://acgsecrets.hk/bangumi/202401/",
            card(anime.model_copy(update={"original_name": "別作品"})),
        )
    )

    class Provider:
        def fetch(self, ids):
            raise AssertionError("Changed identity must not gain a secondary link")

    report = run_source_audit(
        repository.data_dir, now=NOW, source_client=source, secondary_client=Provider()
    )
    assert (
        report["identities"][0]["secondary_unavailable_reason"]
        == "no_explicit_anilist_link"
    )


def test_conflicting_links_across_quarters_are_not_arbitrarily_selected(
    tmp_path, anime
):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    save(repository, anime)
    second = anime.model_copy(
        update={"external_ids": ExternalIds(bangumi=10, anilist=21)}
    )
    save(repository, second, year="2025")
    source = SimpleNamespace(
        fetch_quarter_html=lambda year, *_: (
            f"https://acgsecrets.hk/bangumi/{year}01/",
            card(anime, anilist=20 if year == "2024" else 21),
        )
    )
    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=source,
        secondary_client=SimpleNamespace(fetch=lambda _: {}),
    )
    assert (
        report["identities"][0]["secondary_unavailable_reason"]
        == "conflicting_explicit_ids"
    )


def test_unattempted_batches_do_not_get_attempt_timestamps(tmp_path, anime):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    records = [
        anime.model_copy(
            update={
                "bangumi_id": f"anime-{index}",
                "anime_name": f"作品{index}",
                "external_ids": ExternalIds(bangumi=10, anilist=100 + index),
            }
        )
        for index in range(41)
    ]
    repository.write_quarter(
        year="2024",
        season="冬",
        records=records,
        source_url="https://acgsecrets.hk/bangumi/202401/",
        source_count=41,
        parse_failure_count=0,
    )

    class Source:
        def fetch_quarter_html(self, *_):
            raise SourceFetchError("offline")

    calls = []

    class Provider:
        def fetch(self, ids):
            calls.append(ids)
            raise SecondarySourceError("http_429")

    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=Source(),
        secondary_client=Provider(),
    )
    assert len(calls) == 1
    assert report["summary"]["anilist_subjects_attempted"] == 40
    last = report["identities"][-1]["secondary"][0]
    assert last["result"] == "not_attempted"
    assert last["attempted_at"] is None
    assert last["checked_at"] is None


def test_audit_cli_writes_partial_report_before_failing(
    monkeypatch, project_paths, tmp_path
):
    import manage

    monkeypatch.setattr(manage.ProjectPaths, "from_environment", lambda: project_paths)
    output = tmp_path / "audit"
    monkeypatch.setenv("SOURCE_AUDIT_OUTPUT", str(output))
    monkeypatch.setattr(
        "services.source_audit.run_source_audit",
        lambda _: {
            "summary": {},
            "identities": [],
            "quarters": [],
            "errors": [{"stage": "anilist", "reason": "http_403"}],
            "observed_at": NOW.isoformat(),
        },
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        manage.execute_command("source-audit")
    assert (output / "source-audit.json").is_file()
    monkeypatch.setenv("SOURCE_AUDIT_OUTPUT", str(project_paths.data_dir))
    with pytest.raises(ValueError, match="outside"):
        manage.execute_command("source-audit")
