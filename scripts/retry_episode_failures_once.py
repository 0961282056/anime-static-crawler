"""Temporary scoped retry with public response evidence; never publishes data."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from models import TAIPEI_TZ
from services.bangumi_pages import parse_subject_page
from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_link_lookup import fetch_quarter_catalog, quarter_bounds
from services.episode_metadata import (
    BangumiClient,
    MetadataUnavailable,
    RequestBudgetExceeded,
    _date,
    _name,
    subject_identity_matches,
)


def main():
    repository = DataRepository(Path("dist/data"), DataQualityPolicy())
    datasets = {path: repository.load_path(path) for path in repository.validate_all()}
    targets = defaultdict(list)
    for path, dataset in datasets.items():
        for index, anime in enumerate(dataset.anime_list):
            if anime.episodes and anime.episodes.fetch_state == "stale":
                targets[anime.external_ids.bangumi].append((path, index, anime))
    assert sum(map(len, targets.values())) == 73, "Unexpected stale baseline"
    evidence_dir = Path("retry-evidence")
    evidence_dir.mkdir(exist_ok=True)
    report = []
    replacements = {}

    class EvidenceClient(BangumiClient):
        def __init__(self, sid):
            super().__init__(request_budget=24)
            self.deadline = time.monotonic() + 90
            self.sid = sid
            self.responses = []

        def _request(self, url, **kwargs):
            entry = {"url": url, "params": kwargs.get("params")}
            try:
                result = super()._request(url, **kwargs)
                entry["response"] = result
                return result
            except (MetadataUnavailable, RequestBudgetExceeded) as exc:
                entry["error"] = type(exc).__name__ + ": " + str(exc)
                raise
            finally:
                self.responses.append(entry)

    for sid, group in sorted(targets.items()):
        client = EvidenceClient(sid)
        now = datetime.now(TAIPEI_TZ)
        errors = {}
        info = None
        mode = None
        for label, fetch in [
            ("api", client._fetch_api),
            ("pages", client._fetch_pages),
        ]:
            try:
                info = fetch(group[0][2], now)
                mode = label
                break
            except (MetadataUnavailable, RequestBudgetExceeded) as exc:
                errors[label] = type(exc).__name__ + ": " + str(exc)
        observed_subjects = []
        observed = None
        for response in client.responses:
            value = response.get("response")
            try:
                if response["url"].endswith(f"/subjects/{sid}") and isinstance(
                    value, dict
                ):
                    observed = value
                elif response["url"].endswith(f"/subject/{sid}") and isinstance(
                    value, str
                ):
                    observed = parse_subject_page(value, sid)
                else:
                    continue
                if observed.get("id") == sid and observed.get("type") == 2:
                    observed_subjects.append(observed)
            except (ValueError, MetadataUnavailable):
                continue
        for path, index, anime in group:
            identity_ok = any(
                subject_identity_matches(anime, s) for s in observed_subjects
            )
            updated = info is not None and identity_ok
            if updated:
                replacements[(path, index)] = anime.model_copy(
                    update={"episodes": info}
                )
            report.append(
                {
                    "quarter": path.stem,
                    "card_id": anime.bangumi_id,
                    "anime_name": anime.anime_name,
                    "original_name": anime.original_name,
                    "subject_id": sid,
                    "attempted_at": now.isoformat(),
                    "outcome": info.fetch_state if updated else "stale",
                    "transport": mode,
                    "errors": errors,
                    "identity_verified": identity_ok,
                    "observed_name": observed.get("name") if observed else None,
                    "observed_date": observed.get("date") if observed else None,
                    "total_episodes": info.total_episodes if updated else None,
                    "aired_episodes": info.aired_episodes if updated else None,
                    "airing_status": info.airing_status if updated else None,
                    "note": info.note if updated else None,
                }
            )
        (evidence_dir / f"{sid}.json").write_text(
            json.dumps(client.responses, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (evidence_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            sid,
            group[0][2].anime_name,
            mode,
            info.fetch_state if info else errors,
            flush=True,
        )
        time.sleep(0.5)
    for path, dataset in datasets.items():
        records = [
            replacements.get((path, index), anime)
            for index, anime in enumerate(dataset.anime_list)
        ]
        if records != dataset.anime_list:
            year, season = path.stem.split("_")
            repository.write_quarter(
                year=year,
                season=season,
                records=records,
                source_url=dataset.source_url,
                source_count=dataset.quality.source_count,
                parse_failure_count=dataset.quality.parse_failure_count,
            )
    # Collect exact replacement evidence for two source links proved wrong.
    repairs = []
    for card, old_id, year, season, expected_name in [
        ("anime-371", 22654, "2018", "秋", "とある魔術の禁書目録Ⅲ"),
        ("anime-1750", 486347, "2025", "冬", "闇芝居 第14期"),
    ]:
        client = EvidenceClient(old_id)
        try:
            catalog = fetch_quarter_catalog(client, year=year, season=season)
            start, end = quarter_bounds(year, season)
            candidates = [
                item
                for item in catalog
                if item.get("type") == 2
                and _name(item.get("name", "")) == _name(expected_name)
                and _date(item.get("date")) is not None
                and start <= _date(item["date"]) < end
            ]
            assert len(candidates) == 1, "Replacement identity is not unique"
            candidate = candidates[0]
            target = next(
                anime
                for group in targets.values()
                for _, _, anime in group
                if anime.bangumi_id == card
            )
            linked = target.model_copy(
                update={
                    "original_name": candidate["name"],
                    "external_ids": target.external_ids.model_copy(
                        update={"bangumi": candidate["id"]}
                    ),
                }
            )
            info = client._fetch_api(linked, datetime.now(TAIPEI_TZ))
            repairs.append(
                {
                    "card_id": card,
                    "old_subject_id": old_id,
                    "subject": candidate,
                    "episodes": info.model_dump(mode="json"),
                    "method": "unique-exact-anime-title+first-air-quarter+full-subject-and-episodes",
                }
            )
        except (MetadataUnavailable, RequestBudgetExceeded, AssertionError) as exc:
            repairs.append(
                {"card_id": card, "old_subject_id": old_id, "error": str(exc)}
            )
        (evidence_dir / (card + "-replacement.json")).write_text(
            json.dumps(client.responses, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (evidence_dir / "repair-candidates.json").write_text(
        json.dumps(repairs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("reviewable outcomes", len(report), "updated", len(replacements), flush=True)


if __name__ == "__main__":
    main()
