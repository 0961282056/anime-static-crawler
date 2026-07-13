from __future__ import annotations

from pathlib import Path

import pytest

from services.errors import ItemParseError
from services.parser import extract_item_html, parse_anime_item

SOURCE_ID = 'acgs-bangumi-anime-id="anime-2200"'
NORMAL_TIME = '<div class="time_today main_time">7月5日起／每週日／23時0分</div>'
STORY = '<div class="anime_story">第一段<br>第二段</div>'


def _document(fixture_dir: Path) -> str:
    return (fixture_dir / "acgsecrets_202607_minimal.html").read_text(encoding="utf-8")


def _replace_once(document: str, old: str, new: str) -> str:
    assert document.count(old) == 1, f"fixture fragment is not unique: {old}"
    return document.replace(old, new, 1)


def _single_item(document: str) -> str:
    items = extract_item_html(document)
    assert len(items) == 1
    return items[0]


def test_parser_reads_current_detail_card_contract(fixture_dir: Path) -> None:
    candidate = parse_anime_item(_single_item(_document(fixture_dir)))

    assert candidate.bangumi_id == "anime-2200"
    assert candidate.anime_name == "測試動畫"
    assert candidate.source_image_url == (
        "https://static.acgsecrets.hk/img/test/full-cover.jpg"
    )
    assert candidate.premiere_date == "日"
    assert candidate.premiere_time == "23:00"
    assert candidate.story == "第一段 第二段"


def test_parser_preserves_japanese_deep_night_hour(fixture_dir: Path) -> None:
    deep_night = (
        '<div class="time_today main_time">7月4日起／每週六深夜／26時38分</div>'
    )
    document = _replace_once(_document(fixture_dir), NORMAL_TIME, deep_night)

    candidate = parse_anime_item(_single_item(document))

    assert candidate.premiere_date == "六"
    assert candidate.premiere_time == "26:38"


def test_parser_allows_missing_story(fixture_dir: Path) -> None:
    document = _replace_once(_document(fixture_dir), STORY, "")

    candidate = parse_anime_item(_single_item(document))

    assert candidate.story == "暫無簡介"


def test_parser_rejects_missing_source_id(fixture_dir: Path) -> None:
    document = _replace_once(_document(fixture_dir), SOURCE_ID, "")

    with pytest.raises(ItemParseError, match="missing acgs-bangumi-anime-id"):
        parse_anime_item(_single_item(document))


def test_parser_accepts_legacy_summary_card_id(fixture_dir: Path) -> None:
    document = _replace_once(
        _document(fixture_dir),
        SOURCE_ID,
        'acgs-bangumi-data-id="anime-2200"',
    )

    candidate = parse_anime_item(_single_item(document))

    assert candidate.bangumi_id == "anime-2200"


def test_parser_uses_explicit_missing_time_values(fixture_dir: Path) -> None:
    document = _replace_once(_document(fixture_dir), NORMAL_TIME, "")

    candidate = parse_anime_item(_single_item(document))

    assert candidate.premiere_date == "無首播日期"
    assert candidate.premiere_time == "無首播時間"


def test_extract_items_rejects_changed_card_structure() -> None:
    with pytest.raises(ItemParseError, match="No anime cards matched"):
        extract_item_html('<div id="acgs-anime-list"></div>')
