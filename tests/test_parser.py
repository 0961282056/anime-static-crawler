from __future__ import annotations

from pathlib import Path

import pytest

from services.errors import ItemParseError
from services.parser import extract_item_html, parse_anime_item

SOURCE_ID = 'acgs-bangumi-anime-id="anime-2200"'
NORMAL_TIME = '<div class="time_today main_time">7月5日起／每週日／23時0分</div>'
STORY = '<div class="anime_story">第一段<br>第二段</div>'
PICTURE_COVER_URL = (
    "https://static.acgsecrets.hk/img/af/af4edb783166c7ce696d987c718a56f9/"
    "f85c1898534579aba21d79c67497d4c15a189bc6125d00ec0b936ad59735b84d.jpg"
)


def _document(fixture_dir: Path) -> str:
    return (fixture_dir / "acgsecrets_202607_minimal.html").read_text(encoding="utf-8")


def _replace_once(document: str, old: str, new: str) -> str:
    assert document.count(old) == 1, f"fixture fragment is not unique: {old}"
    return document.replace(old, new, 1)


def _single_item(document: str) -> str:
    items = extract_item_html(document)
    assert len(items) == 1
    return items[0]


def test_malformed_cover_url_is_a_typed_parse_failure(fixture_dir):
    document = _document(fixture_dir).replace(
        "https://static.acgsecrets.hk/img/test/full-cover.jpg",
        "https://[invalid/cover.jpg",
    )
    with pytest.raises(ItemParseError, match="invalid cover URL"):
        parse_anime_item(_single_item(document))


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


def test_parser_reads_picture_cover_contract(fixture_dir: Path) -> None:
    document = (fixture_dir / "acgsecrets_202610_picture_minimal.html").read_text(
        encoding="utf-8"
    )

    candidate = parse_anime_item(_single_item(document))

    assert candidate.bangumi_id == "anime-2279"
    assert candidate.anime_name == "藥師少女的獨語 第三季 前半"
    assert candidate.source_image_url == PICTURE_COVER_URL
    assert candidate.premiere_date == "五"
    assert candidate.premiere_time == "23:00"


def test_parser_prefers_picture_original_url_over_thumbnail(fixture_dir: Path) -> None:
    document = (fixture_dir / "acgsecrets_202610_picture_minimal.html").read_text(
        encoding="utf-8"
    )
    document = _replace_once(
        document,
        f'src="{PICTURE_COVER_URL}"',
        'src="https://static.acgsecrets.hk/img/test/thumbnail.jpg"',
    )

    candidate = parse_anime_item(_single_item(document))

    assert candidate.source_image_url == PICTURE_COVER_URL


@pytest.mark.parametrize("attribute", ["src", "data-src", "acgs-img-data-url"])
def test_parser_falls_back_to_picture_image_url(
    fixture_dir: Path, attribute: str
) -> None:
    document = (fixture_dir / "acgsecrets_202610_picture_minimal.html").read_text(
        encoding="utf-8"
    )
    document = _replace_once(document, f'acgs-img-data-url="{PICTURE_COVER_URL}"', "")
    document = _replace_once(
        document, f'src="{PICTURE_COVER_URL}"', f'{attribute}="{PICTURE_COVER_URL}"'
    )

    candidate = parse_anime_item(_single_item(document))

    assert candidate.source_image_url == PICTURE_COVER_URL


def test_parser_rejects_picture_without_cover_url(fixture_dir: Path) -> None:
    document = (fixture_dir / "acgsecrets_202610_picture_minimal.html").read_text(
        encoding="utf-8"
    )
    document = _replace_once(document, f'acgs-img-data-url="{PICTURE_COVER_URL}"', "")
    document = _replace_once(document, f'src="{PICTURE_COVER_URL}"', "")

    with pytest.raises(ItemParseError, match="missing its cover URL"):
        parse_anime_item(_single_item(document))


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


@pytest.mark.parametrize("day", ["一至五", "不定期公開"])
def test_parser_preserves_explicit_non_single_weekday(fixture_dir, day):
    text = f"每週{day}" if day == "一至五" else day
    document = _replace_once(
        _document(fixture_dir),
        NORMAL_TIME,
        f'<div class="time_today main_time">{text}</div>',
    )
    candidate = parse_anime_item(_single_item(document))
    assert candidate.premiere_date == day
    assert candidate.premiere_time == "無首播時間"


def test_parser_does_not_truncate_an_unsupported_range_to_its_first_day(fixture_dir):
    document = _replace_once(
        _document(fixture_dir),
        NORMAL_TIME,
        '<div class="time_today main_time">每週一至六</div>',
    )
    with pytest.raises(ItemParseError, match="Unsupported multiple broadcast"):
        parse_anime_item(_single_item(document))


def test_reviewed_wrong_source_link_is_corrected_during_normal_parsing(fixture_dir):
    document = (
        _document(fixture_dir)
        .replace("anime-2200", "anime-371")
        .replace("測試動畫", "魔法禁書目錄III")
    )
    document = document.replace(
        STORY,
        '<div class="entity_original_name">とある魔術の禁書目録III</div><div class="anime_link_group"><a href="https://bgm.tv/subject/22654">Bangumi</a></div>'
        + STORY,
    )
    candidate = parse_anime_item(_single_item(document))
    assert candidate.external_ids.bangumi == 226540
    assert candidate.original_name == "とある魔術の禁書目録III"
    assert candidate.anime_name == "魔法禁書目錄III"
    assert candidate.story == "第一段 第二段"
    changed = document.replace("とある魔術の禁書目録III", "とある魔術の禁書目録 第2期")
    assert parse_anime_item(_single_item(changed)).external_ids.bangumi == 22654
