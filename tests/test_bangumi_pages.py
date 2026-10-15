from __future__ import annotations

from datetime import timedelta

import pytest
import requests
from bs4 import BeautifulSoup

from models import EpisodeMetadata
from services.bangumi_pages import parse_episode_page, parse_subject_page
from services.episode_metadata import (
    BangumiClient,
    EpisodeEnricher,
    MetadataUnavailable,
    RequestBudgetExceeded,
    parse_episode_metadata,
)
from tests.test_episode_metadata import NOW, FakeResponse, FakeSession, anime


@pytest.fixture
def pages(fixture_dir):
    return tuple(
        (fixture_dir / f"bangumi_liar_game_{name}.html").read_text()
        for name in ("subject", "episodes")
    )


class HTMLResponse(FakeResponse):
    def __init__(self, html, status=200):
        super().__init__({}, status)
        self.text = html
        self.content = html.encode()


def test_retrieved_page_structure_matches_api_evidence(pages, anime_record_factory):
    subject_html, episode_html = pages
    subject = parse_subject_page(subject_html, 580133)
    entries = parse_episode_page(episode_html, 580133)
    assert subject["name"] == "LIAR GAME"
    assert subject["name_cn"] == "欺诈游戏"
    assert subject["eps"] == 26
    assert len(entries) == 26
    info = parse_episode_metadata(anime(anime_record_factory), subject, entries, NOW)
    assert (info.aired_episodes, info.total_episodes) == (26, 26)
    assert info.airing_status == "finished"


def test_page_progress_uses_dates_and_excludes_specials(pages, anime_record_factory):
    subject_html, episode_html = pages
    subject = parse_subject_page(subject_html.replace(">26</li>", ">12</li>"), 580133)
    subject["infobox"] = [x for x in subject["infobox"] if x["key"] != "播放结束"]
    soup = BeautifulSoup(episode_html, "lxml")
    rows = soup.select("ul.line_list > li:not(.cat)")
    for row in rows[12:]:
        row.decompose()
    for row in rows[8:12]:
        row.select_one("small").string = "首播:2026-10-20"
    listing = soup.select_one("ul.line_list")
    special = BeautifulSoup(
        '<li class="cat">特别篇</li><li><h6><a href="/ep/9999">1.SP</a></h6><small>首播:2026-01-01</small></li>',
        "lxml",
    )
    for row in special.select("li"):
        listing.append(row)
    entries = parse_episode_page(str(soup), 580133)
    info = parse_episode_metadata(anime(anime_record_factory), subject, entries, NOW)
    assert (info.aired_episodes, info.total_episodes) == (8, 12)
    assert info.airing_status == "airing"


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("mutation", ["identity", "media", "header"])
def test_pages_reject_wrong_subject_media_and_layout(pages, index, mutation):
    html = pages[index]
    if mutation == "identity":
        html = html.replace('/subject/580133"', '/subject/999"')
    elif mutation == "media":
        html = html.replace("focus chl anime", "focus chl real")
    else:
        html = html.replace('id="headerSubject"', 'id="other"')
    parser = parse_subject_page if index == 0 else parse_episode_page
    with pytest.raises(ValueError):
        parser(html, 580133)


@pytest.mark.parametrize("mutation", ["category", "number", "pagination", "list"])
def test_partial_or_unrecognized_episode_pages_fail_closed(pages, mutation):
    html = pages[1]
    if mutation == "category":
        html = html.replace('<li class="cat">本篇</li>', "")
    elif mutation == "number":
        html = html.replace("1.伝説", "?.伝説")
    elif mutation == "pagination":
        html = html.replace(
            '<div id="columnInSubjectA">',
            '<div id="columnInSubjectA"><div class="page_inner"><a href="?page=2">2</a></div>',
        )
    else:
        html = html.replace('class="line_list"', 'class="changed"')
    with pytest.raises(ValueError):
        parse_episode_page(html, 580133)


def test_missing_page_dates_do_not_imply_completion(pages, anime_record_factory):
    subject = parse_subject_page(pages[0], 580133)
    entries = parse_episode_page(
        pages[1].replace("首播:2026-09-28", "首播:未定"), 580133
    )
    info = parse_episode_metadata(anime(anime_record_factory), subject, entries, NOW)
    assert info.aired_episodes is None
    assert info.airing_status == "unknown"


def test_api_failure_uses_independent_page_circuit_and_shared_budget(
    pages, anime_record_factory
):
    responses = [HTMLResponse(value) for value in pages * 2]
    session = FakeSession([requests.Timeout(), *responses])
    client = BangumiClient(session=session, interval_seconds=0, request_budget=5)
    record = anime(anime_record_factory)
    for _ in range(2):
        info = client.fetch(record, NOW)
        assert info.airing_status == "finished"
        assert "頁面備援" in info.note
    assert client.unavailable and not client.pages_unavailable
    assert len(session.calls) == 5
    assert sum(url.startswith("https://api.bgm.tv") for url, _ in session.calls) == 1
    assert all(
        url.startswith("https://bangumi.tv/subject/580133")
        for url, _ in session.calls[1:]
    )
    assert client.remaining == 0
    assert all(response.closed for response in responses)
    with pytest.raises(RequestBudgetExceeded):
        client.fetch(record, NOW)


def test_successful_api_does_not_request_html(anime_record_factory):
    from tests.test_episode_metadata import episode, subject

    session = FakeSession(
        [FakeResponse(subject()), FakeResponse({"data": [episode(1)], "total": 1})]
    )
    client = BangumiClient(session=session, interval_seconds=0)
    client.fetch(anime(anime_record_factory), NOW)
    assert len(session.calls) == 2
    assert not client.pages_unavailable


def test_shared_deadline_prevents_unbudgeted_fallback(anime_record_factory):
    session = FakeSession([])
    client = BangumiClient(session=session, interval_seconds=0)
    client.deadline = 0
    with pytest.raises(RequestBudgetExceeded):
        client.fetch(anime(anime_record_factory), NOW)
    assert not session.calls


@pytest.mark.parametrize(
    "response",
    [
        HTMLResponse("redirect", 302),
        HTMLResponse("challenge"),
        HTMLResponse("x" * (2 * 1024 * 1024 + 1)),
    ],
)
def test_page_failure_retains_previous_snapshot(response, anime_record_factory):
    previous = EpisodeMetadata(
        total_episodes=12,
        aired_episodes=8,
        episode_count_status="planned",
        checked_at=NOW - timedelta(days=2),
        attempted_at=NOW - timedelta(days=2),
        fetch_state="ok",
    )
    session = FakeSession([requests.Timeout(), response])
    client = BangumiClient(session=session, interval_seconds=0)
    updated = (
        EpisodeEnricher(client)
        .enrich(anime(anime_record_factory, episodes=previous), NOW)
        .episodes
    )
    assert (updated.aired_episodes, updated.total_episodes) == (8, 12)
    assert updated.checked_at == previous.checked_at
    assert updated.attempted_at == NOW
    assert updated.fetch_state == "stale"
    assert response.closed
    assert all(not kwargs["allow_redirects"] for _, kwargs in session.calls)


def test_page_failure_stops_subsequent_network_requests(anime_record_factory):
    session = FakeSession([requests.Timeout(), HTMLResponse("unavailable", 503)])
    client = BangumiClient(session=session, interval_seconds=0)
    for _ in range(2):
        with pytest.raises(MetadataUnavailable):
            client.fetch(anime(anime_record_factory), NOW)
    assert len(session.calls) == 2


def test_fallback_does_not_bypass_budget_or_title_validation(
    pages, anime_record_factory
):
    session = FakeSession([requests.Timeout()])
    client = BangumiClient(session=session, interval_seconds=0, request_budget=1)
    with pytest.raises(RequestBudgetExceeded):
        client.fetch(anime(anime_record_factory), NOW)
    assert len(session.calls) == 1
    wrong_title = pages[0].replace(">LIAR GAME<", ">LIAR GAME 2<")
    session = FakeSession([requests.Timeout(), HTMLResponse(wrong_title)])
    with pytest.raises(MetadataUnavailable, match="title differs"):
        BangumiClient(session=session, interval_seconds=0).fetch(
            anime(anime_record_factory), NOW
        )
    assert len(session.calls) == 2


def test_conflicting_page_count_keeps_last_valid_values(pages, anime_record_factory):
    previous = EpisodeMetadata(
        total_episodes=26,
        aired_episodes=26,
        airing_status="finished",
        episode_count_status="confirmed",
        checked_at=NOW - timedelta(days=91),
        attempted_at=NOW - timedelta(days=91),
        fetch_state="ok",
    )
    response = HTMLResponse(pages[0].replace(">26</li>", ">12</li>"))
    session = FakeSession([requests.Timeout(), response, HTMLResponse(pages[1])])
    updated = (
        EpisodeEnricher(BangumiClient(session=session, interval_seconds=0))
        .enrich(anime(anime_record_factory, episodes=previous), NOW)
        .episodes
    )
    assert (updated.aired_episodes, updated.total_episodes) == (26, 26)
    assert updated.checked_at == previous.checked_at
    assert updated.fetch_state == "conflict"
    assert updated.airing_status == "unknown"
