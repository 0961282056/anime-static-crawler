from __future__ import annotations

import json
import subprocess

import pytest

from services.quality_evidence import (
    audit_evidence,
    evidence_entry,
    measure,
    quality_trend,
    stored_evidence,
    transition,
    trend_svg,
    write_quality_evidence,
)


def documents(story="暫無簡介", *, card="anime-1", subject=10):
    return {
        "2026_秋": {
            "anime_list": [
                {
                    "bangumi_id": card,
                    "anime_name": "作品",
                    "original_name": "作品 第2期",
                    "external_ids": {"bangumi": subject},
                    "story": story,
                }
            ]
        }
    }


def test_trend_does_not_claim_removed_or_replaced_identity_is_resolved():
    original = measure(documents())
    fixed = measure(documents("官方原文"))
    assert transition(original, fixed)["issues"]["missing_story"] == {
        "resolved": 1,
        "introduced": 0,
        "unchanged": 0,
    }
    replaced = transition(original, measure(documents("官方原文", subject=20)))
    assert replaced["comparable_records"] == 0
    assert replaced["added_records"] == replaced["removed_records"] == 1
    assert replaced["issues"]["missing_story"]["resolved"] == 0


def test_empty_or_duplicate_historical_data_is_not_silently_zero():
    with pytest.raises(ValueError, match="No quarterly"):
        measure({})
    data = documents()
    data["2026_秋"]["anime_list"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        measure(data)


def test_retained_or_failed_evidence_cannot_receive_fresh_success_timestamp():
    with pytest.raises(ValueError, match="successful observation"):
        evidence_entry(
            identity=None,
            quarter="2026_秋",
            field="episodes",
            provider="bangumi",
            method="unrecorded",
            scope="unknown",
            state="failed",
            checked_at="2026-10-19T00:00:00Z",
        )


def git(root, *args):
    return (
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


def test_trend_rebuilds_committed_history_and_separates_uncommitted_changes(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    data = tmp_path / "dist" / "data"
    data.mkdir(parents=True)
    path = data / "2026_秋.json"
    path.write_text(json.dumps(documents()["2026_秋"]))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "missing story")
    path.write_text(json.dumps(documents("官方原文")["2026_秋"]))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "verified story")
    trend = quality_trend(tmp_path, documents(), limit=1)
    assert trend["history_truncated"] is True
    assert trend["shallow_history"] is False
    assert trend["snapshots"][0]["counts"]["missing_story"] == 0
    assert trend["working_tree"]["counts"]["missing_story"] == 1
    assert (
        trend["working_tree"]["change_since_last_data_commit"]["issues"][
            "missing_story"
        ]["introduced"]
        == 1
    )
    full = quality_trend(tmp_path, documents("官方原文"))
    assert full["snapshots"][1]["change"]["issues"]["missing_story"]["resolved"] == 1
    assert "Git" in trend_svg(full)
    with pytest.raises(ValueError, match="limit"):
        quality_trend(tmp_path, documents(), limit=0)


def test_stored_receipts_keep_old_times_and_unknown_methods(
    tmp_path, anime_record_factory
):
    from models import Anime, EpisodeMetadata, ExternalIds, QuarterDataset

    anime = Anime(
        **anime_record_factory(1),
        external_ids=ExternalIds(bangumi=10),
        episodes=EpisodeMetadata(fetch_state="stale"),
    )
    dataset = QuarterDataset(anime_list=[anime], generated_at="2026-10-19T00:00:00Z")
    path = tmp_path / "2026_秋.json"
    path.write_text(dataset.model_dump_json())
    before = path.read_bytes()
    _, receipts = stored_evidence(tmp_path)
    assert len(receipts) == 2
    assert all(row["checked_at"] is None for row in receipts)
    assert all(row["last_verified_at"] is None for row in receipts)
    assert receipts[0]["method"] == "legacy_method_not_structured"
    assert receipts[1]["method"] == "unrecorded"
    assert path.read_bytes() == before


def test_audit_receipts_keep_failed_attempt_distinct_from_retained_success():
    old = "2026-10-01T00:00:00Z"
    now = "2026-10-19T00:00:00Z"
    report = {
        "quarters": [
            {
                "quarter": "2026_秋",
                "attempted_at": now,
                "checked_at": None,
                "error": "SourceFetchError",
            }
        ],
        "identities": [
            {
                "card_id": "anime-1",
                "primary_observations": [
                    {
                        "quarter": "2026_秋",
                        "provenance": {
                            "provider": "bangumi",
                            "verification_method": "bangumi_api",
                            "scope": "linked_entry",
                            "source_urls": [],
                            "last_attempt_at": now,
                            "last_verified_at": old,
                            "source_snapshot_at": None,
                            "scope_explanation": "正篇",
                        },
                    }
                ],
                "secondary": [
                    {
                        "quarter": "2026_秋",
                        "attempted_at": now,
                        "checked_at": None,
                        "verification_method": "graphql_api",
                        "scope": "reported_total",
                        "reasons": ["http_403"],
                    }
                ],
            }
        ],
    }
    receipts = audit_evidence(report)
    assert [row["state"] for row in receipts] == ["failed", "retained", "failed"]
    assert receipts[1]["last_verified_at"] == old
    assert all(row["checked_at"] is None for row in receipts)
    assert len({row["evidence_id"] for row in receipts}) == 3


def test_reports_cannot_write_into_public_dist(tmp_path):
    with pytest.raises(ValueError, match="outside public"):
        write_quality_evidence(
            tmp_path, tmp_path / "data", tmp_path / "dist" / "evidence"
        )
