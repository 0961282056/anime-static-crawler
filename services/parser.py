"""Pure HTML parsing with no network or Cloudinary side effects."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from models import AnimeCandidate
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
        week_match = re.search(r"每週([一二三四五六日天])", text)
        if week_match:
            premiere_date = "日" if week_match.group(1) == "天" else week_match.group(1)
        time_match = re.search(r"(\d{1,2})時(\d{1,2})分", text)
        if time_match:
            premiere_time = (
                f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}"
            )

    if premiere_date == "無首播日期":
        raw_weekday = str(item.get("weektoday", "")).strip()
        if raw_weekday in {"一", "二", "三", "四", "五", "六", "日", "天"}:
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

    image_element = item.select_one("div.anime_cover_image img")
    raw_image_url = ""
    if image_element:
        raw_image_url = str(
            image_element.get("acgs-img-data-url")
            or image_element.get("src")
            or image_element.get("data-src")
            or ""
        ).strip()
    if not raw_image_url:
        raise ItemParseError(f"{anime_name}: anime card is missing its cover URL")
    source_image_url = urljoin(SOURCE_ORIGIN, raw_image_url)

    story_element = item.select_one("div.anime_story")
    story = story_element.get_text(" ", strip=True) if story_element else "暫無簡介"
    premiere_date, premiere_time = _broadcast_details(item)

    return AnimeCandidate(
        bangumi_id=_stable_id(item, anime_name),
        anime_name=anime_name,
        source_image_url=source_image_url,
        premiere_date=premiere_date,
        premiere_time=premiere_time,
        story=story or "暫無簡介",
    )
