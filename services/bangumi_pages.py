"""Parse Bangumi's public pages into the same evidence used by the API.

Selectors are deliberately strict: a login/challenge page, unknown category,
or paginated episode list must not become a seemingly complete snapshot.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


def _page(html: str, subject_id: int) -> tuple[BeautifulSoup, str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.select_one("#headerSubject h1 a[href]")
    anime_tab = soup.select_one("#navMenuNeue a.focus.anime[href='/anime']")
    if (
        title is None
        or title.get("href") != f"/subject/{subject_id}"
        or anime_tab is None
        or not title.get_text(strip=True)
    ):
        raise ValueError("Page is not the expected anime subject")
    return soup, title.get_text(strip=True), str(title.get("title", ""))


def parse_subject_page(html: str, subject_id: int) -> dict[str, Any]:
    soup, name, name_cn = _page(html, subject_id)
    infobox = soup.select_one("#infobox")
    if infobox is None:
        raise ValueError("Subject page is missing its infobox")
    fields = []
    for row in infobox.select("li"):
        label = row.find("span", class_="tip", recursive=False)
        if label is None:
            continue
        prefix = label.get_text(" ", strip=True)
        text = row.get_text(" ", strip=True)
        fields.append(
            {"key": prefix.rstrip(":： "), "value": text[len(prefix) :].strip()}
        )
    counts = [
        int(item["value"])
        for item in fields
        if item["key"] in {"话数", "話數"}
        and re.fullmatch(r"[1-9][0-9]*", item["value"])
    ]
    starts = {
        item["value"]
        for item in fields
        if item["key"] in {"放送开始", "放送開始", "播放开始", "播放開始"}
    }
    if len(starts) > 1:
        raise ValueError("Subject page has conflicting start dates")
    return {
        "id": subject_id,
        "type": 2,
        "name": name,
        "name_cn": name_cn,
        "eps": counts[0] if counts else None,
        "date": next(iter(starts), None),
        "infobox": fields,
    }


def parse_episode_page(html: str, subject_id: int) -> list[dict[str, Any]]:
    soup, _, _ = _page(html, subject_id)
    column = soup.select_one("#columnInSubjectA")
    listing = column.select_one(".line_detail ul.line_list") if column else None
    if listing is None or column.select(".page_inner a[href]"):
        raise ValueError("Episode page is missing a complete, unpaginated list")
    entries = []
    category = None
    found_main = False
    for row in listing.find_all("li", recursive=False):
        if "cat" in row.get("class", []):
            category = row.get_text(strip=True)
            found_main |= category in {"本篇", "正篇"}
            continue
        if category is None:
            raise ValueError("Episode category is missing")
        if category not in {"本篇", "正篇"}:
            continue
        link = row.select_one("h6 a[href]")
        identity = (
            re.fullmatch(r"/ep/([1-9][0-9]*)", str(link.get("href", "")))
            if link
            else None
        )
        number = (
            re.match(r"(\d+(?:\.\d+)?)\.", link.get_text(strip=True)) if link else None
        )
        if identity is None or number is None:
            raise ValueError("Main episode identity or number is invalid")
        airdate = re.search(
            r"首播\s*[:：]\s*(\d{4}-\d{2}-\d{2})", row.get_text(" ", strip=True)
        )
        entries.append(
            {
                "id": int(identity.group(1)),
                "subject_id": subject_id,
                "type": 0,
                "sort": float(number.group(1)),
                "airdate": airdate.group(1) if airdate else None,
            }
        )
    if not found_main:
        raise ValueError("Episode page has no recognized main category")
    return entries
