from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cloudinary_cleaner import (
    execution_receipt_payload,
    file_sha256,
    load_execution_receipt,
    load_manifest,
    main,
    manifest_digest,
    manifest_payload,
    parse_args,
    read_manifest,
    require_protected_execution_context,
    validate_execution_receipt_bindings,
    validate_manifest_cloud_binding,
    validate_trusted_manifest_time,
    verify_prepared_base_transition,
    verify_remote_main_unchanged,
)
from services.errors import RetentionError
from services.retention import PreparedDeletion, RetentionPlan

CLOUD_NAME = "production-cloud"


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
    payload = manifest_payload(_plan(), cloud_name=CLOUD_NAME)
    path.write_text(json.dumps(payload), encoding="utf-8")

    created_at, minimum_age_days, candidates = load_manifest(path)
    reviewed = read_manifest(path)

    assert created_at == _plan().created_at
    assert reviewed.cloud_name == CLOUD_NAME
    assert minimum_age_days == 30
    assert candidates == {"anime_covers/old"}


def test_manifest_tampering_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    payload = manifest_payload(_plan(), cloud_name=CLOUD_NAME)
    payload["minimum_age_days"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RetentionError, match="manifest_sha256"):
        load_manifest(path)


def test_manifest_cloud_name_tampering_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    payload = manifest_payload(_plan(), cloud_name=CLOUD_NAME)
    payload["cloud_name"] = "other-cloud"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RetentionError, match="manifest_sha256"):
        read_manifest(path)


def test_legacy_manifest_schema_without_cloud_binding_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    payload = manifest_payload(_plan(), cloud_name=CLOUD_NAME)
    payload["schema_version"] = 2
    del payload["cloud_name"]
    payload["manifest_sha256"] = manifest_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RetentionError, match="unsupported manifest schema_version"):
        read_manifest(path)


def test_manifest_cloud_binding_rejects_a_different_configured_cloud(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(manifest_payload(_plan(), cloud_name=CLOUD_NAME)),
        encoding="utf-8",
    )
    reviewed = read_manifest(path)

    with pytest.raises(RetentionError, match="product environment"):
        validate_manifest_cloud_binding(reviewed, "other-cloud")


def test_local_retention_execution_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "GITHUB_ACTIONS",
        "GITHUB_REF",
        "RETENTION_EXPECTED_MAIN_SHA",
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
    expected_sha = "a" * 40
    monkeypatch.setenv("RETENTION_EXPECTED_MAIN_SHA", expected_sha)
    monkeypatch.setenv(
        "RETENTION_EXECUTION_CONTEXT",
        "protected-github-environment",
    )

    def fake_run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        output = f"{expected_sha}\n" if args[1:3] == ["rev-parse", "HEAD"] else ""
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
    monkeypatch.setenv("RETENTION_EXPECTED_MAIN_SHA", "a" * 40)
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


def test_prepared_base_transition_rejects_non_cache_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        output = "cloudinary_cache.json\0README.md\0" if args[1] == "diff" else ""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr("cloudinary_cleaner.subprocess.run", fake_run)

    with pytest.raises(RetentionError, match="Only cloudinary_cache.json"):
        verify_prepared_base_transition("a" * 40, "b" * 40)


def test_execution_receipt_round_trip_and_self_hash(tmp_path: Path) -> None:
    path = tmp_path / "execution.json"
    prepared = PreparedDeletion(
        minimum_age_days=30,
        inventory_count=100,
        delete_candidates=("anime_covers/old",),
    )
    payload = execution_receipt_payload(
        prepared,
        base_sha="a" * 40,
        cloud_name="production-cloud",
        manifest_sha256="b" * 64,
        cache_sha256="c" * 64,
        prepared_at=datetime(2026, 7, 13, 8, 30, tzinfo=UTC),
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = load_execution_receipt(path)

    assert receipt.base_sha == "a" * 40
    assert receipt.cloud_name == "production-cloud"
    assert receipt.manifest_sha256 == "b" * 64
    assert receipt.cache_sha256 == "c" * 64
    assert receipt.delete_candidates == ("anime_covers/old",)


def test_execution_receipt_tampering_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "execution.json"
    payload = execution_receipt_payload(
        PreparedDeletion(30, 100, ("anime_covers/old",)),
        base_sha="a" * 40,
        cloud_name="production-cloud",
        manifest_sha256="b" * 64,
        cache_sha256="c" * 64,
    )
    payload["cloud_name"] = "attacker-cloud"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RetentionError, match="receipt_sha256"):
        load_execution_receipt(path)


def test_execution_receipt_rejects_a_different_merged_cache(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload(_plan(), cloud_name=CLOUD_NAME)),
        encoding="utf-8",
    )
    reviewed_manifest = read_manifest(manifest_path)
    cache_path = tmp_path / "cloudinary_cache.json"
    cache_path.write_text("{}\n", encoding="utf-8")
    receipt_path = tmp_path / "execution.json"
    receipt_path.write_text(
        json.dumps(
            execution_receipt_payload(
                PreparedDeletion(30, 100, ("anime_covers/old",)),
                base_sha="a" * 40,
                cloud_name="production-cloud",
                manifest_sha256=reviewed_manifest.sha256,
                cache_sha256=file_sha256(cache_path),
            )
        ),
        encoding="utf-8",
    )
    receipt = load_execution_receipt(receipt_path)
    cache_path.write_text('{"unexpected":"url"}\n', encoding="utf-8")

    with pytest.raises(RetentionError, match="cache does not match"):
        validate_execution_receipt_bindings(
            receipt,
            reviewed_manifest,
            cloud_name="production-cloud",
            cache_path=cache_path,
        )


def test_execution_receipt_rejects_a_different_manifest_cloud(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload(_plan(), cloud_name=CLOUD_NAME)),
        encoding="utf-8",
    )
    reviewed_manifest = read_manifest(manifest_path)
    cache_path = tmp_path / "cloudinary_cache.json"
    cache_path.write_text("{}\n", encoding="utf-8")
    receipt_path = tmp_path / "execution.json"
    receipt_path.write_text(
        json.dumps(
            execution_receipt_payload(
                PreparedDeletion(30, 100, ("anime_covers/old",)),
                base_sha="a" * 40,
                cloud_name="other-cloud",
                manifest_sha256=reviewed_manifest.sha256,
                cache_sha256=file_sha256(cache_path),
            )
        ),
        encoding="utf-8",
    )
    receipt = load_execution_receipt(receipt_path)

    with pytest.raises(RetentionError, match="reviewed retention manifest"):
        validate_execution_receipt_bindings(
            receipt,
            reviewed_manifest,
            cloud_name="other-cloud",
            cache_path=cache_path,
        )


def test_prepare_and_execute_modes_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["cloudinary_cleaner.py", "--prepare-execution", "--execute-prepared"],
    )

    with pytest.raises(SystemExit):
        parse_args()


def test_legacy_execute_cli_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["cloudinary_cleaner.py", "--execute"])

    with pytest.raises(RetentionError, match="--execute is disabled"):
        main()
