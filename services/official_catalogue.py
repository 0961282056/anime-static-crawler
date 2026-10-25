"""Bounded complete NHK catalogue verification for one reviewed TV identity."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from models import Anime, EpisodeMetadata
from services.response_limits import bounded_response

LIST_URL = "https://www.web.nhk/tv/an/zenitendo/pl/series-tep-4NX2MRW758/list"
TOKYO_TZ = ZoneInfo("Asia/Tokyo")
EP_PATTERN = re.compile(
    r"https://www\.web\.nhk/tv/an/zenitendo/pl/series-tep-4NX2MRW758/ep/[A-Z0-9]+\Z"
)


class OfficialSourceUnavailable(RuntimeError):
    """Incomplete evidence must never replace the last verified catalogue."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "official_contract_error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def parse_catalogue_page(document: str) -> tuple[int, list[dict]]:
    soup = BeautifulSoup(document, "lxml")
    totals = {
        int(value)
        for value in re.findall(
            r"すべて\s*[（(]\s*([0-9]+)\s*[）)]", soup.get_text(" ", strip=True)
        )
    }
    if len(totals) != 1 or not 0 < next(iter(totals)) <= 400:
        raise OfficialSourceUnavailable("Official catalogue total missing or ambiguous")
    rows = []
    for anchor in soup.select("a[href]"):
        url = anchor["href"]
        if not EP_PATTERN.fullmatch(url):
            continue
        stamp = anchor.select_one("time[datetime]")
        titles = [
            p.get_text(" ", strip=True)
            for p in anchor.select("p")
            if p.get_text(" ", strip=True).startswith("「")
        ]
        if stamp is None or len(titles) != 1 or not titles[0].endswith("」"):
            raise OfficialSourceUnavailable(
                "Official episode title/date contract changed"
            )
        try:
            parsed = datetime.fromisoformat(stamp["datetime"])
            if parsed.tzinfo is None:
                raise ValueError("Timezone missing")
            day = parsed.astimezone(TOKYO_TZ).date()
        except (ValueError, KeyError) as exc:
            raise OfficialSourceUnavailable(
                "Invalid official first-air timestamp"
            ) from exc
        if day.year < 2020 or not titles[0][1:-1].strip():
            raise OfficialSourceUnavailable("Invalid official episode evidence")
        rows.append({"url": url, "title": titles[0][1:-1], "date": day})
    if not rows or len(rows) > 10:
        raise OfficialSourceUnavailable("Official catalogue page has unexpected size")
    return next(iter(totals)), rows


class NHKCatalogueClient:
    def __init__(
        self,
        session: Any = None,
        *,
        request_budget: int = 40,
        time_budget: int = 120,
        baseline: list[dict] | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.remaining = request_budget
        self.time_budget = time_budget
        self.started: float | None = None
        self.last_request: float | None = None
        self.baseline = baseline

    def _get(self, url: str) -> str:
        if self.started is None:
            self.started = time.monotonic()
        if self.remaining <= 0 or time.monotonic() - self.started >= self.time_budget:
            raise OfficialSourceUnavailable(
                "Official catalogue request/time budget exhausted",
                code="official_budget_exhausted",
            )
        if self.last_request is not None:
            time.sleep(max(0, 0.5 - (time.monotonic() - self.last_request)))
        self.remaining -= 1
        self.last_request = time.monotonic()
        try:
            response = self.session.get(
                url,
                timeout=(5, 15),
                allow_redirects=False,
                stream=True,
                hooks={"response": bounded_response(2 * 1024 * 1024)},
                headers={"User-Agent": "anime-static-crawler/official-catalogue"},
            )
            try:
                if response.status_code != 200:
                    raise OfficialSourceUnavailable(
                        f"Official catalogue HTTP {response.status_code}",
                        code="official_http_error",
                        http_status=response.status_code,
                    )
                response.encoding = "utf-8"
                return response.text
            finally:
                response.close()
        except requests.RequestException as exc:
            raise OfficialSourceUnavailable(
                "Official catalogue transport failure",
                code="official_timeout"
                if isinstance(exc, requests.Timeout)
                else "official_transport_error",
            ) from exc

    def fetch(self, anime: Anime, now: datetime) -> EpisodeMetadata:
        if (anime.bangumi_id, anime.original_name, anime.external_ids.bangumi) != (
            "anime-663",
            "ふしぎ駄菓子屋 銭天堂",
            315772,
        ):
            raise OfficialSourceUnavailable("Official adapter identity mismatch")
        total, entries = parse_catalogue_page(self._get(LIST_URL))
        pages = (total + 9) // 10
        if len(entries) != min(total, 10):
            raise OfficialSourceUnavailable("Incomplete first catalogue page")
        for page in range(2, pages + 1):
            observed, items = parse_catalogue_page(self._get(f"{LIST_URL}?page={page}"))
            if observed != total or len(items) != min(10, total - 10 * (page - 1)):
                raise OfficialSourceUnavailable(
                    "Catalogue changed or pagination ended early"
                )
            entries.extend(items)
        if len(entries) != total or len({e["url"] for e in entries}) != total:
            raise OfficialSourceUnavailable(
                "Catalogue repeated or omitted an episode URL"
            )
        baseline = self.baseline
        if baseline is None:
            path = (
                Path(__file__).resolve().parents[1]
                / "docs/remaining-nine-2026-10-15/nhk-complete-episode-catalogue.json"
            )
            baseline = json.loads(path.read_text(encoding="utf-8"))["episodes"]
        by_url = {entry["url"]: entry["date"].isoformat() for entry in entries}
        if any(
            by_url.get(entry["source_url"]) != entry["first_air_date"]
            for entry in baseline
        ):
            raise OfficialSourceUnavailable(
                "Previously verified official episodes disappeared or changed dates"
            )
        # Count programmes by official URL, not distinct titles. Future and
        # same-day listings are not proof of completed first broadcast.
        aired = [
            entry
            for entry in entries
            if entry["date"] < now.astimezone(TOKYO_TZ).date()
        ]
        previous = anime.episodes.aired_episodes if anime.episodes else None
        if previous is not None and len(aired) < previous:
            raise OfficialSourceUnavailable(
                "Official progress regressed; retaining prior snapshot"
            )
        return EpisodeMetadata(
            aired_episodes=len(aired),
            total_episodes=None,
            airing_status="unknown",
            episode_count_status="unknown",
            fetch_state="ok",
            source_urls=[LIST_URL, f"{LIST_URL}?page={pages}"]
            if pages > 1
            else [LIST_URL],
            checked_at=now,
            attempted_at=now,
            source_kind="official_catalogue",
            note=f"已自動核對 NHK 全部 {pages} 頁、{total} 個不同節目 URL；依首播日期確認已播 {len(aired)} 個，排除當日及未來節目。總數與終了未公告，不推測完結；每七天複查，失敗時保留舊資料。",
        )
