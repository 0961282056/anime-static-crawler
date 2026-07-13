from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cloudinary_cleaner import (
    load_manifest,
    manifest_payload,
    require_protected_execution_context,
    validate_trusted_manifest_time,
    verify_remote_main_unchanged,
)
from services.errors import RetentionError
from services.retention import RetentionPlan


def _plan() -> RetentionPlan:
    return RetentionPlan(
        created_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
        minimum_age_days=30,
        referenced=frozenset({"anime_covers/referenced"}),
        cloud_resources=frozenset({"anime_covers/referenced", "anime_covers/old"}),
        delete_candidates=("anime_covers/old",),
    )


def test_manifest_round_trip_and_digest(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    payload = manifest_payload(_plan())
    path.write_text(json.dumps(payload), encoding="utf-8")

    created_at, minimum_age_days, candidates = load_manifest(path)

    assert created_at == _plan().created_at
    assert minimum_age_days == 30
    assert candidates == {"anime_covers/old"}


def test_manifest_tampering_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    payload = manifest_payload(_plan())
    payload["minimum_age_days"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RetentionError, match="manifest_sha256"):
        load_manifest(path)


def test_local_retention_execution_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "GITHUB_ACTIONS",
        "GITHUB_REF",
        "GITHUB_SHA",
        "RETENTION_EXECUTION_CONTEXT",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RetentionError, match="protected main-branch"):
        require_protected_execution_context()


def test_protected_retention_execution_context_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv(
        "RETENTION_EXECUTION_CONTEXT",
        "protected-github-environment",
    )

    def fake_run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        output = "abc123\n" if args[1:3] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr("cloudinary_cleaner.subprocess.run", fake_run)

    require_protected_execution_context()


def test_manifest_time_is_bound_to_aged_github_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_created_at = datetime.now(UTC) - timedelta(days=31)
    monkeypatch.setenv(
        "RETENTION_TRUSTED_CREATED_AT",
        run_created_at.isoformat(),
    )

    validate_trusted_manifest_time(run_created_at + timedelta(minutes=5))


def test_recent_trusted_github_run_cannot_fake_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_created_at = datetime.now(UTC) - timedelta(days=1)
    monkeypatch.setenv(
        "RETENTION_TRUSTED_CREATED_AT",
        run_created_at.isoformat(),
    )

    with pytest.raises(RetentionError, match="younger than"):
        validate_trusted_manifest_time(run_created_at + timedelta(minutes=5))


def test_remote_main_change_stops_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repository")
    monkeypatch.setenv("GITHUB_SHA", "approved-sha")
    monkeypatch.setenv("GH_TOKEN", "masked-token")
    monkeypatch.setattr(
        "cloudinary_cleaner.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="newer-sha\n",
            stderr="",
        ),
    )

    with pytest.raises(RetentionError, match="Remote main changed"):
        verify_remote_main_unchanged()
