from __future__ import annotations

import json
from pathlib import Path

from services.episode_metadata import (
    BangumiClient,
    MetadataUnavailable,
    RequestBudgetExceeded,
)

out = Path("remaining-evidence")
out.mkdir(exist_ok=True)
for sid in [418599, 582915, 321387, 584847, 425998, 547888, 568244, 559674]:
    client = BangumiClient(request_budget=30)
    evidence = {"subject_id": sid, "episode_pages": []}
    try:
        evidence["subject"] = client._get(f"/subjects/{sid}")
        count = 0
        for _ in range(20):
            page = client._get(
                "/episodes", subject_id=sid, type=0, limit=200, offset=count
            )
            evidence["episode_pages"].append(page)
            items = page.get("data", [])
            count += len(items)
            if count >= page["total"]:
                break
            if not items:
                raise MetadataUnavailable("Incomplete pagination")
        else:
            raise MetadataUnavailable("Pagination cap reached")
    except (MetadataUnavailable, RequestBudgetExceeded) as exc:
        evidence["error"] = str(exc)
    (out / f"{sid}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        sid,
        evidence.get("error"),
        sum(len(p.get("data", [])) for p in evidence["episode_pages"]),
        flush=True,
    )
