from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from models import TAIPEI_TZ
from services.errors import SelectorCanaryError
from services.parser import extract_item_html
from services.selector_canary import run_selector_canary
from services.settings import CrawlerSettings


class FakeSourceClient:
    def __init__(self, document_html: str) -> None:
        self.document_html = document_html
        self.calls: list[tuple[str, str]] = []

    def fetch_quarter_html(self, year: str, season: str) -> tuple[str, str]:
        self.calls.append((year, season))
        return f"https://acgsecrets.hk/bangumi/{year}07/", self.document_html


def _settings() -> CrawlerSettings:
    return CrawlerSettings(
        source_base_url="https://acgsecrets.hk/bangumi",
        source_user_agent="selector-canary-tests/1.0",
        max_workers=1,
        request_timeout_seconds=15,
        image_timeout_seconds=15,
        image_max_bytes=10 * 1024 * 1024,
        image_max_pixels=40_000_000,
        image_allowed_hosts=("static.acgsecrets.hk",),
        minimum_count_ratio=0.7,
        maximum_parse_failure_ratio=0.0,
        maximum_fallback_id_ratio=0.0,
        cloudinary_quota_limit_percent=90.0,
    )


def _document(fixture_dir: Path) -> str:
    return (fixture_dir / "acgsecrets_202607_minimal.html").read_text(encoding="utf-8")


def test_canary_parses_the_live_contract_without_storage_dependencies(
    fixture_dir: Path,
) -> None:
    source_client = FakeSourceClient(_document(fixture_dir))

    result = run_selector_canary(
        now=datetime(2026, 7, 14, 9, 15, tzinfo=TAIPEI_TZ),
        settings=_settings(),
        source_client=source_client,
    )

    assert result.year == "2026"
    assert result.season == "夏"
    assert result.card_count == 1
    assert result.source_url == "https://acgsecrets.hk/bangumi/202607/"
    assert source_client.calls == [("2026", "夏")]


def test_canary_fails_when_the_card_selector_matches_nothing() -> None:
    source_client = FakeSourceClient('<div id="acgs-anime-list"></div>')

    with pytest.raises(SelectorCanaryError, match="matched no anime cards"):
        run_selector_canary(
            now=datetime(2026, 7, 14, 9, 15, tzinfo=TAIPEI_TZ),
            settings=_settings(),
            source_client=source_client,
        )


def test_canary_fails_when_any_live_card_breaks_the_parser_contract(
    fixture_dir: Path,
) -> None:
    document = _document(fixture_dir).replace(
        'acgs-bangumi-anime-id="anime-2200"',
        "",
    )
    source_client = FakeSourceClient(document)

    with pytest.raises(SelectorCanaryError, match="failed at card 0"):
        run_selector_canary(
            now=datetime(2026, 7, 14, 9, 15, tzinfo=TAIPEI_TZ),
            settings=_settings(),
            source_client=source_client,
        )


def test_canary_rejects_duplicate_source_ids(fixture_dir: Path) -> None:
    item_html = extract_item_html(_document(fixture_dir))[0]
    duplicate_document = f'<div id="acgs-anime-list">{item_html}{item_html}</div>'
    source_client = FakeSourceClient(duplicate_document)

    with pytest.raises(SelectorCanaryError, match="duplicate bangumi_id"):
        run_selector_canary(
            now=datetime(2026, 7, 14, 9, 15, tzinfo=TAIPEI_TZ),
            settings=_settings(),
            source_client=source_client,
        )
