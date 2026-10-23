from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from models import TAIPEI_TZ, Anime, EpisodeMetadata, ExternalIds
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import ItemParseError, SourceFetchError
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


def save(repository, anime, year="2024", season="冬"):
    repository.write_quarter(
        year=year,
        season=season,
        records=[anime],
        source_url=f"https://acgsecrets.hk/bangumi/{year}01/",
        source_count=1,
        parse_failure_count=0,
    )


def test_full_audit_records_source_links_without_importing_or_verifying_them(
    tmp_path, anime
):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    no_link = anime.model_copy(update={"external_ids": ExternalIds(bangumi=10)})
    save(repository, no_link)
    save(repository, no_link, year="2025")
    before = {p: p.read_bytes() for p in repository.validate_all()}
    source = SimpleNamespace(
        fetch_quarter_html=lambda year, season: (
            f"https://acgsecrets.hk/bangumi/{year}01/",
            card(anime),
        )
    )
    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=source,
    )
    assert report["secondary_source"]["state"] == "retired"
    assert report["summary"]["records"] == 2
    assert report["summary"]["unique_identities"] == 1
    assert report["summary"]["coverage_differences"] == 2
    assert len(report["identities"][0]["secondary"]) == 2
    assert report["identities"][0]["explicit_anilist_ids"] == [20]
    assert all(
        row["result"] == "not_attempted" for row in report["identities"][0]["secondary"]
    )
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

    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=Source(),
    )
    assert report["summary"]["errors"] == 1
    quarter = report["quarters"][0]
    assert quarter["checked_at"] is None
    assert "differences" not in quarter
    secondary = report["identities"][0]["secondary"][0]
    assert secondary["checked_at"] is None
    assert secondary["result"] == "not_attempted"
    assert secondary["reasons"] == ["anilist_api_retired"]
    assert secondary["attempted_at"] is None


def test_changed_identity_does_not_inherit_new_source_link(tmp_path, anime):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    save(repository, anime.model_copy(update={"external_ids": ExternalIds(bangumi=10)}))
    source = SimpleNamespace(
        fetch_quarter_html=lambda *_: (
            "https://acgsecrets.hk/bangumi/202401/",
            card(anime.model_copy(update={"original_name": "別作品"})),
        )
    )

    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=source,
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
    )
    assert (
        report["identities"][0]["secondary_unavailable_reason"]
        == "conflicting_explicit_ids"
    )


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
            "errors": [{"stage": "quarter", "reason": "SourceFetchError"}],
            "observed_at": NOW.isoformat(),
        },
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        manage.execute_command("source-audit")
    assert (output / "source-audit.json").is_file()
    monkeypatch.setenv("SOURCE_AUDIT_OUTPUT", str(project_paths.data_dir))
    with pytest.raises(ValueError, match="outside"):
        manage.execute_command("source-audit")


@pytest.mark.parametrize(
    ("variant", "state", "anilist_id"),
    [
        ("unchanged", "verified", 20),
        ("missing_anilist", "verified", None),
        ("missing_bangumi", "verified", 20),
        ("changed_original", "source_identity_changed", None),
        ("conflicting_bangumi", "source_identity_changed", None),
        ("absent_card", "source_card_absent", None),
        ("offline", "source_fetch_or_parse_failed", None),
    ],
)
def test_audit_distinguishes_fresh_explicit_links_from_stored_ids(
    tmp_path, anime, variant, state, anilist_id
):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    save(repository, anime)
    path = repository.validate_all()[0]
    before = path.read_bytes()
    document = card(anime)
    if variant == "missing_anilist":
        document = document.replace('<a href="https://anilist.co/anime/20">AL</a>', "")
    elif variant == "missing_bangumi":
        document = document.replace('<a href="https://bgm.tv/subject/10">BG</a>', "")
    elif variant == "changed_original":
        document = card(anime.model_copy(update={"original_name": "作品 第3期"}))
    elif variant == "conflicting_bangumi":
        document = document.replace(
            "https://bgm.tv/subject/10", "https://bgm.tv/subject/11"
        )
    elif variant == "absent_card":
        document = card(anime.model_copy(update={"bangumi_id": "anime-2"}))

    def source(*_):
        if variant == "offline":
            raise SourceFetchError("offline")
        return "https://acgsecrets.hk/bangumi/202401/", document

    result = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=SimpleNamespace(fetch_quarter_html=source),
    )
    observed = result["identities"][0]["primary_observations"][0][
        "source_link_observation"
    ]
    assert observed == {
        "state": state,
        "checked_at": NOW.isoformat() if state == "verified" else None,
        "anilist_id": anilist_id,
    }
    assert path.read_bytes() == before


@pytest.mark.parametrize("legacy_flag", [None, "false", "true", "invalid"])
def test_retired_audit_cannot_be_enabled_by_legacy_environment(
    monkeypatch, tmp_path, anime, legacy_flag
):
    from services.review_notifications import review_cases

    if legacy_flag is None:
        monkeypatch.delenv("SOURCE_AUDIT_ANILIST_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SOURCE_AUDIT_ANILIST_ENABLED", legacy_flag)
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    save(repository, anime)
    before = {p: p.read_bytes() for p in repository.validate_all()}

    def forbidden(*args, **kwargs):
        raise AssertionError("An injected primary source needs no HTTP sessions")

    monkeypatch.setattr("services.source_audit.requests.Session", forbidden)
    source_calls = []

    def source(year, season):
        source_calls.append((year, season))
        return "https://acgsecrets.hk/bangumi/202401/", card(anime)

    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=SimpleNamespace(fetch_quarter_html=source),
    )
    assert source_calls == [("2024", "冬")]
    assert report["quarters"][0]["checked_at"] == NOW.isoformat()
    assert report["secondary_source"] == {
        "provider": "anilist",
        "enabled": False,
        "state": "retired",
        "reason": "anilist_api_retired",
    }
    summary = report["summary"]
    assert summary["anilist_api_enabled"] is False
    assert summary["anilist_api_state"] == "retired"
    assert summary["anilist_subjects_eligible"] == 1
    assert summary["anilist_subjects_requested"] == 0
    assert summary["anilist_subjects_attempted"] == 0
    assert summary["anilist_subjects_observed"] == 0
    assert summary["errors"] == 0
    comparison = report["identities"][0]["secondary"][0]
    assert comparison["result"] == "not_attempted"
    assert comparison["reasons"] == ["anilist_api_retired"]
    assert comparison["attempted_at"] is comparison["checked_at"] is None
    assert comparison["observation"] is None
    assert review_cases(report) == []
    assert before == {p: p.read_bytes() for p in before}
    content = write_audit_report(report, tmp_path / "report")
    assert "API 核對已退役" in content
    assert "本次未嘗試" in content
    receipts = json.loads((tmp_path / "report/evidence.json").read_text())["records"]
    receipt = next(r for r in receipts if r["provider"] == "anilist")
    assert receipt["state"] == "not_attempted"
    assert receipt["reason"] == ["anilist_api_retired"]
    assert receipt["source_urls"] == []
    assert receipt["attempted_at"] is receipt["checked_at"] is None


@pytest.mark.parametrize("previously_enabled", [False, True])
def test_historical_api_reports_remain_readable(tmp_path, anime, previously_enabled):
    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    save(repository, anime)
    report = run_source_audit(
        repository.data_dir,
        now=NOW,
        source_client=SimpleNamespace(
            fetch_quarter_html=lambda *_: (
                "https://acgsecrets.hk/bangumi/202401/",
                card(anime),
            )
        ),
    )
    report["secondary_source"] = {
        "provider": "anilist",
        "enabled": previously_enabled,
        "state": "enabled" if previously_enabled else "disabled",
        "reason": None if previously_enabled else "disabled_by_configuration",
    }
    comparison = report["identities"][0]["secondary"][0]
    comparison.update(
        result="corroborated" if previously_enabled else "not_attempted",
        reasons=[] if previously_enabled else ["disabled_by_configuration"],
        observation={"total_episodes": 12} if previously_enabled else None,
        verification_method="graphql_api" if previously_enabled else "not_attempted",
        attempted_at=NOW.isoformat() if previously_enabled else None,
        checked_at=NOW.isoformat() if previously_enabled else None,
    )
    # Exercise the JSON boundary used by artifacts, without retaining a client.
    content = write_audit_report(json.loads(json.dumps(report)), tmp_path / "report")
    if not previously_enabled:
        assert "已依設定停用" in content
    receipts = json.loads((tmp_path / "report/evidence.json").read_text())["records"]
    receipt = next(row for row in receipts if row["provider"] == "anilist")
    assert receipt["state"] == ("observed" if previously_enabled else "not_attempted")
    assert receipt["source_urls"] == ["https://graphql.anilist.co"]
    assert receipt["checked_at"] == (NOW.isoformat() if previously_enabled else None)


@pytest.mark.parametrize("offline", [False, True])
def test_retired_audit_cli_still_fails_on_primary_errors(
    monkeypatch, tmp_path, anime, project_paths, offline
):
    import manage

    repository = DataRepository(project_paths.data_dir, DataQualityPolicy())
    save(repository, anime)
    before = {p: p.read_bytes() for p in repository.validate_all()}
    monkeypatch.setattr(manage.ProjectPaths, "from_environment", lambda: project_paths)
    monkeypatch.setenv("SOURCE_AUDIT_OUTPUT", str(tmp_path / "report"))
    monkeypatch.setenv("SOURCE_AUDIT_ANILIST_ENABLED", "true")

    def source(*args):
        if offline:
            raise SourceFetchError("offline")
        return "https://acgsecrets.hk/bangumi/202401/", card(anime)

    monkeypatch.setattr(
        "services.source_audit.SourceClient",
        lambda *a, **kw: SimpleNamespace(fetch_quarter_html=source),
    )
    if offline:
        with pytest.raises(SourceFetchError, match="incomplete"):
            manage.execute_command("source-audit")
    else:
        manage.execute_command("source-audit")
    report = json.loads((tmp_path / "report/source-audit.json").read_text())
    assert report["summary"]["errors"] == int(offline)
    assert all(error["stage"] == "quarter" for error in report["errors"])
    assert report["summary"]["anilist_subjects_attempted"] == 0
    assert report["secondary_source"]["state"] == "retired"
    if offline:
        from services.review_notifications import review_cases

        assert {case["kind"] for case in review_cases(report)} == {"source_unavailable"}
    assert before == {p: p.read_bytes() for p in before}


def test_retired_audit_keeps_identity_conflicts_and_snapshot_reviews(
    monkeypatch, tmp_path, anime
):
    from services.review_notifications import review_cases

    repository = DataRepository(tmp_path / "data", DataQualityPolicy())
    older = anime.model_copy(
        update={
            "episodes": anime.episodes.model_copy(
                update={"checked_at": NOW - timedelta(days=100)}
            )
        }
    )
    save(repository, older)
    save(
        repository,
        older.model_copy(update={"external_ids": ExternalIds(bangumi=10, anilist=21)}),
        year="2025",
    )
    monkeypatch.setattr(
        "services.source_audit.reviewed_source",
        lambda _: {
            "mode": "snapshot",
            "metadata": {"source_urls": ["https://example.test/official"]},
        },
    )
    source = SimpleNamespace(
        fetch_quarter_html=lambda year, _: (
            f"https://acgsecrets.hk/bangumi/{year}01/",
            card(anime, anilist=20 if year == "2024" else 21),
        )
    )
    report = run_source_audit(repository.data_dir, now=NOW, source_client=source)
    assert report["identities"][0]["explicit_anilist_ids"] == [20, 21]
    assert report["summary"]["manual_reviews_due"] == 1
    assert {case["kind"] for case in review_cases(report)} == {
        "identity_conflict",
        "snapshot_due",
    }
