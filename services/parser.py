"""Pure HTML parsing with no network or Cloudinary side effects."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from models import AnimeCandidate, ExternalIds
from services.errors import ItemParseError

SOURCE_ORIGIN = "https://acgsecrets.hk"
CARD_SELECTOR = "div#acgs-anime-list div.acgs-anime-block.CV-search"


def extract_item_html(document_html: str) -> list[str]:
    soup = BeautifulSoup(document_html, "lxml")
    cards = soup.select(CARD_SELECTOR)
    if not cards:
        raise ItemParseError(
            f"No anime cards matched the expected selector: {CARD_SELECTOR}"
        )
    return [str(card) for card in cards]


def _stable_id(item: Tag, anime_name: str) -> str:
    attribute_candidates = (
        "acgs-bangumi-anime-id",
        "acgs-bangumi-data-id",
    )
    for attribute in attribute_candidates:
        value = str(item.get(attribute, "")).strip()
        if value:
            return value

    raise ItemParseError(f"{anime_name}: card is missing acgs-bangumi-anime-id")


def _broadcast_details(item: Tag) -> tuple[str, str]:
    premiere_date = "無首播日期"
    premiere_time = "無首播時間"

    time_element = item.select_one("div.time_today.main_time")
    if time_element:
        text = time_element.get_text(" ", strip=True)
        week_match = re.search(
            r"每週([一二三四五六日天](?:[至到～、及與-](?:週)?[一二三四五六日天])*)",
            text,
        )
        if week_match:
            raw_day = week_match.group(1).replace("週", "")
            if raw_day in {"一至五", "一到五", "一～五", "一-五"}:
                raw_day = "一至五"
            if len(raw_day) > 1 and raw_day != "一至五":
                raise ItemParseError("Unsupported multiple broadcast weekdays")
            premiere_date = "日" if raw_day == "天" else raw_day
        elif "不定期公開" in text:
            premiere_date = "不定期公開"
        time_match = re.search(r"(\d{1,2})時(\d{1,2})分", text)
        if time_match:
            premiere_time = (
                f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}"
            )

    if premiere_date == "無首播日期":
        raw_weekday = str(item.get("weektoday", "")).strip()
        if raw_weekday in {
            "一",
            "二",
            "三",
            "四",
            "五",
            "六",
            "日",
            "天",
            "一至五",
            "不定期公開",
        }:
            premiere_date = "日" if raw_weekday == "天" else raw_weekday

    if premiere_time == "無首播時間":
        raw_time = re.sub(r"\D", "", str(item.get("weekairtime", "")))
        if len(raw_time) >= 4:
            hour, minute = int(raw_time[-4:-2]), int(raw_time[-2:])
            if 0 <= hour <= 29 and 0 <= minute <= 59:
                premiere_time = f"{hour:02d}:{minute:02d}"

    return premiere_date, premiere_time


def parse_anime_item(item_html: str) -> AnimeCandidate:
    item = BeautifulSoup(item_html, "lxml").find("div", class_="CV-search")
    if not isinstance(item, Tag):
        raise ItemParseError("Anime card root element is missing")

    name_element = item.select_one("h3.entity_localized_name")
    anime_name = name_element.get_text(" ", strip=True) if name_element else ""
    if not anime_name:
        raise ItemParseError("Anime card is missing its localized name")

    cover_element = item.select_one(".anime_cover_image")
    image_element = cover_element.select_one("img") if cover_element else None
    raw_image_url = (
        str(cover_element.get("acgs-img-data-url") or "").strip()
        if cover_element
        else ""
    )
    if not raw_image_url and image_element:
        raw_image_url = str(
            image_element.get("acgs-img-data-url")
            or image_element.get("src")
            or image_element.get("data-src")
            or ""
        ).strip()
    if not raw_image_url:
        raise ItemParseError(f"{anime_name}: anime card is missing its cover URL")
    try:
        source_image_url = urljoin(SOURCE_ORIGIN, raw_image_url)
    except ValueError as exc:
        raise ItemParseError(
            f"{anime_name}: anime card has an invalid cover URL"
        ) from exc

    story_element = item.select_one("div.anime_story")
    story = story_element.get_text(" ", strip=True) if story_element else "暫無簡介"
    premiere_date, premiere_time = _broadcast_details(item)
    original = item.select_one(".entity_original_name")
    ids: dict[str, int] = {}
    for link in item.select(".anime_link_group a[href]"):
        try:
            parsed = urlsplit(str(link.get("href", "")))
        except ValueError as exc:
            raise ItemParseError(
                f"{anime_name}: anime card has an invalid link URL"
            ) from exc
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            continue
        provider = None
        if parsed.netloc in {"bgm.tv", "bangumi.tv", "chii.in"}:
            match = re.fullmatch(r"/subject/([1-9][0-9]*)/?", parsed.path)
            provider = "bangumi"
        elif parsed.netloc == "anilist.co":
            match = re.fullmatch(r"/anime/([1-9][0-9]*)/?", parsed.path)
            provider = "anilist"
        else:
            continue
        if match and provider:
            try:
                value = int(match.group(1))
            except ValueError as exc:
                raise ItemParseError(
                    f"{anime_name}: anime card has an invalid {provider} ID"
                ) from exc
            if provider in ids and ids[provider] != value:
                raise ItemParseError(
                    f"{anime_name}: conflicting {provider} source links"
                )
            ids[provider] = value

    # The reviewed correction is tied to the unchanged source card and names.
    from services.episode_metadata import reviewed_source_subject_id

    card_id = _stable_id(item, anime_name)
    original_name = original.get_text(" ", strip=True) if original else None
    bangumi = reviewed_source_subject_id(
        card_id, anime_name, original_name, ids.get("bangumi")
    )
    if bangumi is not None:
        ids["bangumi"] = bangumi
    return AnimeCandidate(
        bangumi_id=card_id,
        anime_name=anime_name,
        source_image_url=source_image_url,
        premiere_date=premiere_date,
        premiere_time=premiere_time,
        story=story or "暫無簡介",
        original_name=original_name,
        external_ids=ExternalIds(**ids),
    )
