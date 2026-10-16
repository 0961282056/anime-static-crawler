"""Bounded, read-only AniList observations; never updates published facts."""

from __future__ import annotations

import time
import unicodedata
from datetime import date
from typing import Any

import requests

from models import Anime

QUERY = """
query ($ids: [Int]) {
  Page(page: 1, perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id type format title { native } episodes status
      startDate { year month day }
      endDate { year month day }
      siteUrl
    }
  }
}
"""


class SecondarySourceError(RuntimeError):
    """A verification attempt failed, without invalidating existing facts."""


class AniListClient:
    def __init__(self, session: Any = None, *, max_requests: int = 30) -> None:
        self.session = session or requests.Session()
        self.max_requests = max_requests
        self.requests_used = 0
        self.started: float | None = None
        self.last_request: float | None = None

    def fetch(self, ids: list[int]) -> dict[int, dict]:
        if not ids or len(ids) > 40 or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError("AniList requires 1–40 positive explicit IDs")
        if self.started is None:
            self.started = time.monotonic()
        if (
            self.requests_used >= self.max_requests
            or time.monotonic() - self.started >= 180
        ):
            raise SecondarySourceError("request_budget_exhausted")
        if self.last_request is not None:
            time.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
        self.requests_used += 1
        self.last_request = time.monotonic()
        try:
            response = self.session.post(
                "https://graphql.anilist.co",
                json={"query": QUERY, "variables": {"ids": ids}},
                headers={
                    "User-Agent": "anime-static-crawler/source-audit",
                    "Accept": "application/json",
                },
                timeout=(5, 20),
                allow_redirects=False,
            )
            try:
                if response.status_code != 200:
                    raise SecondarySourceError(f"http_{response.status_code}")
                payload = response.json()
            finally:
                response.close()
        except (requests.RequestException, ValueError) as exc:
            raise SecondarySourceError("transport_or_json_error") from exc
        if not isinstance(payload, dict) or payload.get("errors"):
            raise SecondarySourceError("graphql_error")
        try:
            media = payload["data"]["Page"]["media"]
            if not isinstance(media, list):
                raise TypeError
            result = {}
            for item in media:
                if not isinstance(item, dict):
                    raise TypeError
                key = item.get("id")
                if type(key) is not int or key not in ids or key in result:
                    raise ValueError
                if item.get("type") != "ANIME":
                    raise ValueError
                result[key] = item
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise SecondarySourceError("invalid_response_contract") from exc


def _title(value: str) -> str:
    # Deliberately preserve season numbers, subtitles, and punctuation.
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _date(value: Any) -> date | None:
    if not isinstance(value, dict):
        return None
    try:
        parts = [value[k] for k in ("year", "month", "day")]
        if any(type(part) is not int for part in parts):
            return None
        return date(*parts)
    except (KeyError, TypeError, ValueError):
        return None


def compare_secondary(
    anime: Anime,
    media: dict | None,
    *,
    subject_id: int,
    quarter: str,
    today: date,
    reviewed_scope: bool = False,
) -> dict:
    """A link locates a candidate; matching title alone never proves count scope."""
    result: dict = {
        "provider": "anilist",
        "subject_id": subject_id,
        "source_url": f"https://anilist.co/anime/{subject_id}",
        "result": "unavailable",
        "reasons": [],
        "observation": None,
        "published_data_changed": False,
    }
    if media is None:
        result["reasons"] = ["subject_not_returned"]
        return result
    title = media.get("title")
    native = title.get("native") if isinstance(title, dict) else None
    total = media.get("episodes")
    status = media.get("status")
    start, end = _date(media.get("startDate")), _date(media.get("endDate"))
    result["observation"] = {
        "native_title": native,
        "format": media.get("format"),
        "total_episodes": total,
        "status": status,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
    }
    reasons = result["reasons"]
    if media.get("id") != subject_id or media.get("type") != "ANIME":
        reasons.append("identity_contract_mismatch")
    if (
        not isinstance(native, str)
        or not anime.original_name
        or _title(native) != _title(anime.original_name)
    ):
        reasons.append("original_title_not_exact")
    if media.get("format") not in ("TV", "TV_SHORT"):
        reasons.append("format_requires_scope_review")
    if reviewed_scope:
        reasons.append("manually_reviewed_scope_takes_precedence")
    # Continuing titles may start earlier than the quarter, never later.
    quarter_end = date(int(quarter[:4]), ("冬春夏秋".index(quarter[-1]) + 1) * 3, 1)
    quarter_end = date(
        quarter_end.year + (quarter_end.month == 12), quarter_end.month % 12 + 1, 1
    )
    if start is None:
        reasons.append("start_date_unavailable")
    elif start >= quarter_end:
        reasons.append("starts_after_record_quarter")
    if end and ((status == "FINISHED" and end > today) or (start and end < start)):
        reasons.append("end_date_inconsistent")
    if start and start > today and status in ("FINISHED", "RELEASING"):
        reasons.append("start_date_inconsistent_with_status")
    if total is not None and (type(total) is not int or total <= 0):
        reasons.append("invalid_episode_total")
    if status not in (
        "FINISHED",
        "RELEASING",
        "NOT_YET_RELEASED",
        "CANCELLED",
        "HIATUS",
    ):
        reasons.append("status_unavailable")
    if reasons:
        result["result"] = "needs_identity_or_scope_review"
        return result
    info = anime.episodes
    if total is None:
        reasons.append("secondary_total_unknown")
    elif info is None or info.total_episodes is None:
        reasons.append("primary_total_unknown")
    elif info.total_episodes != total:
        reasons.append("total_disagreement")
    primary_status = info.airing_status if info else "unknown"
    mapped_status = {
        "FINISHED": "finished",
        "RELEASING": "airing",
        "NOT_YET_RELEASED": "not_yet_aired",
        "CANCELLED": "cancelled",
        "HIATUS": "paused",
    }[status]
    if primary_status == "unknown":
        reasons.append("primary_status_unknown")
    elif primary_status != mapped_status:
        reasons.append("status_disagreement")
    result["result"] = "difference" if reasons else "corroborated"
    return result
