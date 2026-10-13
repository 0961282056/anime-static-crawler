from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_link_lookup import (
    exact_subject_id,
    fetch_quarter_catalog,
    quarter_bounds,
    resolve_missing_links,
)
from services.episode_metadata import (
    API_ORIGIN,
    BangumiClient,
    MetadataUnavailable,
    RequestBudgetExceeded,
)
from tests.test_episode_metadata import NOW, FakeResponse, FakeSession, anime, subject


def test_exact_match_needs_unique_original_title_type_and_quarter(anime_record_factory):
    record = anime(anime_record_factory, external_ids={})
    bounds = quarter_bounds("2026", "春")
    page = {"data": [subject()], "total": 1}
    assert exact_subject_id(record, page, *bounds) == 580133
    for mutation in [
        {"type": 1},
        {"date": "2025-04-06"},
        {"name": "LIAR GAME 第2期", "name_cn": "另一本"},
        {"date": "未定"},
    ]:
        assert (
            exact_subject_id(
                record, {"data": [{**subject(), **mutation}], "total": 1}, *bounds
            )
            is None
        )
    assert (
        exact_subject_id(
            record, {"data": [subject(), {**subject(), "id": 999}], "total": 2}, *bounds
        )
        is None
    )
    assert (
        exact_subject_id(
            record.model_copy(update={"original_name": None}), page, *bounds
        )
        is None
    )


@pytest.mark.parametrize(
    "page",
    [
        {"data": [], "total": 1},
        {"data": [], "total": -1},
        {"data": ["invalid"], "total": 1},
    ],
)
def test_partial_or_invalid_search_does_not_pick_first_result(
    page, anime_record_factory
):
    with pytest.raises(MetadataUnavailable):
        exact_subject_id(
            anime(anime_record_factory), page, *quarter_bounds("2026", "春")
        )


def setup_repository(tmp_path, factory):
    repository = DataRepository(tmp_path, DataQualityPolicy())
    repository.write_quarter(
        year="2026",
        season="春",
        records=[anime(factory, external_ids={})],
        source_url="https://acgsecrets.hk/bangumi/202604/",
        source_count=1,
        parse_failure_count=0,
    )
    return repository


def test_lookup_revalidates_full_subject_and_records_evidence(
    tmp_path, anime_record_factory
):
    repository = setup_repository(tmp_path, anime_record_factory)
    original = repository.load_quarter("2026", "春").anime_list[0]
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return {"data": [subject()], "total": 1}

    client = SimpleNamespace(
        _request=request, _get=lambda path: subject(), unavailable=False
    )
    evidence = resolve_missing_links(repository, client, NOW, year="2026", season="春")
    updated = repository.load_quarter("2026", "春").anime_list[0]
    assert updated.external_ids.bangumi == 580133
    assert updated.anime_image_url == original.anime_image_url
    assert updated.anime_name == original.anime_name
    assert updated.episodes is None
    assert evidence[0]["air_date"] == "2026-04-06"
    assert calls[0][1]["payload"]["filter"] == {
        "type": [2],
        "air_date": [">=2026-04-01", "<2026-07-01"],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"type": 1},
        {"date": "2026-07-01"},
        {"name": "Other title", "name_cn": "其他"},
        {"id": 123},
    ],
)
def test_wrong_full_subject_never_writes_a_link(
    tmp_path, anime_record_factory, mutation
):
    repository = setup_repository(tmp_path, anime_record_factory)
    before = repository.load_quarter("2026", "春")
    client = SimpleNamespace(
        _request=lambda *a, **kw: {"data": [subject()], "total": 1},
        _get=lambda path: {**subject(), **mutation},
        unavailable=False,
    )
    assert not resolve_missing_links(repository, client, NOW, year="2026", season="春")
    assert repository.load_quarter("2026", "春") == before


def test_lookup_budget_exhaustion_preserves_records(tmp_path, anime_record_factory):
    repository = setup_repository(tmp_path, anime_record_factory)
    before = repository.load_quarter("2026", "春")

    def request(*args, **kwargs):
        raise RequestBudgetExceeded

    client = SimpleNamespace(_request=request, unavailable=False)
    assert not resolve_missing_links(repository, client, NOW, year="2026", season="春")
    assert repository.load_quarter("2026", "春") == before


def test_search_post_uses_the_same_bounded_transport():
    response = FakeResponse({"data": [], "total": 0})

    class Session(FakeSession):
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return next(self.responses)

    session = Session([response])
    client = BangumiClient(session=session, request_budget=1, interval_seconds=0)
    assert client._request(
        API_ORIGIN + "/search/subjects", payload={"keyword": "original"}
    ) == {"data": [], "total": 0}
    assert session.calls[0][1]["json"] == {"keyword": "original"}
    assert not session.calls[0][1]["allow_redirects"]
    assert response.closed
    assert client.remaining == 0
    with pytest.raises(RequestBudgetExceeded):
        client._request(API_ORIGIN + "/search/subjects", payload={"keyword": "next"})


def test_complete_monthly_catalog_uses_documented_fifty_row_pages():
    second = {**subject(), "id": 123}
    responses = iter(
        [
            {"data": [subject()], "total": 2},
            {"data": [second], "total": 2},
            {"data": [], "total": 0},
            {"data": [], "total": 0},
        ]
    )
    calls = []

    def get(path, **params):
        calls.append(params)
        return next(responses)

    catalog = fetch_quarter_catalog(SimpleNamespace(_get=get), year="2026", season="春")
    assert {item["id"] for item in catalog} == {580133, 123}
    assert [call["month"] for call in calls] == [4, 4, 5, 6]
    assert calls[1]["offset"] == 1
    assert all(call["limit"] == 50 and call["type"] == 2 for call in calls)


@pytest.mark.parametrize(
    "pages",
    [
        [{"data": [], "total": 1}],
        [{"data": [subject()], "total": 2}, {"data": [], "total": 3}],
        [{"data": [subject()], "total": 0}],
        [
            {"data": [subject()], "total": 1},
            {"data": [{**subject(), "name": "conflict"}], "total": 1},
        ],
    ],
)
def test_incomplete_catalog_is_never_used_for_identity_matching(pages):
    responses = iter(pages)
    with pytest.raises(MetadataUnavailable):
        fetch_quarter_catalog(
            SimpleNamespace(_get=lambda *a, **kw: next(responses)),
            year="2026",
            season="春",
        )


def test_catalog_lookup_avoids_one_search_request_per_anime(
    tmp_path, anime_record_factory
):
    repository = setup_repository(tmp_path, anime_record_factory)
    client = SimpleNamespace(_get=lambda path: subject(), unavailable=False)
    assert resolve_missing_links(
        repository, client, NOW, year="2026", season="春", catalog=[subject()]
    )
    assert (
        repository.load_quarter("2026", "春").anime_list[0].external_ids.bangumi
        == 580133
    )
