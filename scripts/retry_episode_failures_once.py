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
from services.episode_metadata import (
    BangumiClient,
    MetadataUnavailable,
    RequestBudgetExceeded,
    _name,
    subject_names,
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
        names = set()
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
                    names |= subject_names(observed)
            except (ValueError, MetadataUnavailable):
                continue
        for path, index, anime in group:
            identity_ok = bool(
                anime.original_name and _name(anime.original_name) in names
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
    print("reviewable outcomes", len(report), "updated", len(replacements), flush=True)


if __name__ == "__main__":
    main()
