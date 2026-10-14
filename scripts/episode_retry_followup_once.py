"""Temporary public evidence collection for a wrong ID and a redirected ID."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from models import TAIPEI_TZ
from services.data_repository import DataQualityPolicy, DataRepository
from services.episode_metadata import (
    BangumiClient,
    MetadataUnavailable,
    RequestBudgetExceeded,
)


def main():
    repo = DataRepository(Path("dist/data"), DataQualityPolicy())
    records = [a for p in repo.validate_all() for a in repo.load_path(p).anime_list]
    results = []
    for card, old, proposed in [
        ("anime-1750", 486347, 526223),
        ("anime-359", 197168, None),
    ]:
        original = next(a for a in records if a.bangumi_id == card)
        result = {
            "card_id": card,
            "old_subject_id": old,
            "attempted_at": datetime.now(TAIPEI_TZ).isoformat(),
        }
        client = BangumiClient(request_budget=24)
        try:
            if proposed is None:
                response = requests.get(
                    f"https://api.bgm.tv/v0/subjects/{old}",
                    timeout=(5, 15),
                    allow_redirects=False,
                )
                try:
                    result["redirect_status"] = response.status_code
                    result["redirect_location"] = response.headers.get("Location")
                    parsed = urlsplit(result["redirect_location"] or "")
                    if (
                        parsed.scheme not in ("", "https")
                        or parsed.netloc not in ("", "api.bgm.tv")
                        or parsed.query
                        or parsed.fragment
                    ):
                        raise MetadataUnavailable("Untrusted subject redirect")
                    match = re.fullmatch(r"/v0/subjects/([1-9][0-9]*)", parsed.path)
                    if response.status_code not in (301, 302, 307, 308) or not match:
                        raise MetadataUnavailable(
                            "Redirect is not an explicit API subject migration"
                        )
                    proposed = int(match.group(1))
                finally:
                    response.close()
            linked = original.model_copy(
                update={
                    "external_ids": original.external_ids.model_copy(
                        update={"bangumi": proposed}
                    )
                }
            )
            result["subject_id"] = proposed
            result["subject"] = client._get(f"/subjects/{proposed}")
            result["episodes"] = client._fetch_api(
                linked, datetime.now(TAIPEI_TZ)
            ).model_dump(mode="json")
        except (
            MetadataUnavailable,
            RequestBudgetExceeded,
            requests.RequestException,
        ) as exc:
            result["error"] = type(exc).__name__ + ": " + str(exc)
        results.append(result)
    output = Path("retry-followup-evidence")
    output.mkdir(exist_ok=True)
    (output / "followup.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        [
            (
                r["card_id"],
                r.get("subject_id"),
                r.get("error"),
                r.get("episodes", {}).get("fetch_state"),
            )
            for r in results
        ]
    )


if __name__ == "__main__":
    main()
