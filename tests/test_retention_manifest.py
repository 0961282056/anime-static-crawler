from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import cloudinary_cleaner as cleaner
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
from services.settings import ProjectPaths

CLOUD_NAME = "production-cloud"


def _plan() -> RetentionPlan:
    return RetentionPlan(
        created_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
        minimum_age_days=30,
        referenced=frozenset({"anime_covers/referenced"}),
        cloud_resources=frozenset({"anime_covers/referenced", "anime_covers/old"}),
        delete_candidates=("anime_covers/old",),
    )


def _cli_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "minimum_age_days": 30,
        "manifest_output": None,
        "prepare_execution": False,
        "execute_prepared": False,
        "execute": False,
        "manifest_input": None,
        "execution_output": None,
        "execution_input": None,
        "confirm": "DELETE-REVIEWED-CLOUDINARY-CANDIDATES",
        "grace_days": 30,
        "max_delete": 50,
        "max_fraction": 0.02,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _project_paths(tmp_path: Path) -> ProjectPaths:
    output_dir = tmp_path / "dist"
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True)
    return ProjectPaths(
        root=tmp_path,
        output_dir=output_dir,
        data_dir=data_dir,
        templates_dir=tmp_path / "templates",
        static_source_dir=tmp_path / "static",
        static_output_dir=output_dir / "static",
        cache_file=tmp_path / "cloudinary_cache.json",
        cloudflare_headers_file=tmp_path / "_headers",
    )


class _RetentionServiceSpy:
    def __init__(self) -> None:
        self.plan_result = _plan()
        self.prepared = PreparedDeletion(
            minimum_age_days=30,
            inventory_count=100,
            delete_candidates=("anime_covers/old",),
        )
        self.plan_calls: list[int] = []
        self.prepare_calls = 0
        self.execute_calls = 0

    def plan(self, *, minimum_age_days: int) -> RetentionPlan:
        self.plan_calls.append(minimum_age_days)
        return self.plan_result

    def prepare_deletion(self, *args: object, **kwargs: object) -> PreparedDeletion:
        self.prepare_calls += 1
        assert kwargs["pre_prepare_check"] is cleaner.verify_remote_main_unchanged
        return self.prepared

    def invalidate_prepared_cache(self, prepared: PreparedDeletion) -> int:
        assert prepared == self.prepared
        return 1

    def execute_prepared(self, *args: object, **kwargs: object) -> frozenset[str]:
        self.execute_calls += 1
        assert args[1] == self.prepared
        assert kwargs["pre_delete_check"] is cleaner.verify_remote_main_unchanged
        return frozenset(self.prepared.delete_candidates)


def _patch_cli_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: argparse.Namespace,
    service: _RetentionServiceSpy,
) -> ProjectPaths:
    paths = _project_paths(tmp_path)
    monkeypatch.setattr(cleaner, "load_dotenv", lambda: None)
    monkeypatch.setattr(cleaner, "parse_args", lambda: args)
    monkeypatch.setattr(
        cleaner.ProjectPaths,
        "from_environment",
        classmethod(lambda cls: paths),
    )
    monkeypatch.setattr(
        cleaner,
        "required_cloudinary_credentials",
        lambda: {
            "CLOUDINARY_CLOUD_NAME": CLOUD_NAME,
            "CLOUDINARY_API_KEY": "masked",
            "CLOUDINARY_API_SECRET": "masked",
        },
    )
    monkeypatch.setattr(cleaner, "CloudinaryRetentionService", lambda *args: service)
    return paths


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


def test_main_dry_run_writes_a_bound_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_output = tmp_path / "retention-plan.json"
    service = _RetentionServiceSpy()
    args = _cli_args(manifest_output=manifest_output)
    _patch_cli_dependencies(monkeypatch, tmp_path, args, service)

    assert main() == 0

    payload = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert payload["cloud_name"] == CLOUD_NAME
    assert payload["delete_candidates"] == ["anime_covers/old"]
    assert payload["manifest_sha256"] == manifest_digest(payload)
    assert service.plan_calls == [30]


def test_main_prepare_writes_a_cache_bound_execution_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_input = tmp_path / "retention-plan.json"
    execution_output = tmp_path / "retention-execution.json"
    service = _RetentionServiceSpy()
    args = _cli_args(
        prepare_execution=True,
        manifest_input=manifest_input,
        execution_output=execution_output,
    )
    paths = _patch_cli_dependencies(monkeypatch, tmp_path, args, service)
    reviewed = cleaner.ReviewedManifest(
        created_at=datetime.now(UTC) - timedelta(days=31),
        cloud_name=CLOUD_NAME,
        minimum_age_days=30,
        candidates=frozenset({"anime_covers/old"}),
        sha256="b" * 64,
    )
    monkeypatch.setattr(cleaner, "read_manifest", lambda path: reviewed)
    monkeypatch.setattr(cleaner, "validate_trusted_manifest_time", lambda value: None)
    monkeypatch.setattr(
        cleaner,
        "require_protected_execution_context",
        lambda: "a" * 40,
    )

    assert main() == 0

    receipt = load_execution_receipt(execution_output)
    assert receipt.base_sha == "a" * 40
    assert receipt.manifest_sha256 == reviewed.sha256
    assert receipt.cache_sha256 == file_sha256(paths.cache_file)
    assert service.prepare_calls == 1
    assert service.execute_calls == 0


def test_main_execute_verifies_receipt_before_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_input = tmp_path / "retention-plan.json"
    execution_input = tmp_path / "retention-execution.json"
    service = _RetentionServiceSpy()
    args = _cli_args(
        execute_prepared=True,
        manifest_input=manifest_input,
        execution_input=execution_input,
    )
    paths = _patch_cli_dependencies(monkeypatch, tmp_path, args, service)
    paths.cache_file.write_text("{}\n", encoding="utf-8")
    reviewed = cleaner.ReviewedManifest(
        created_at=datetime.now(UTC) - timedelta(days=31),
        cloud_name=CLOUD_NAME,
        minimum_age_days=30,
        candidates=frozenset({"anime_covers/old"}),
        sha256="b" * 64,
    )
    receipt = cleaner.ExecutionReceipt(
        prepared_at=datetime.now(UTC) - timedelta(days=30),
        base_sha="a" * 40,
        cloud_name=CLOUD_NAME,
        manifest_sha256=reviewed.sha256,
        minimum_age_days=30,
        inventory_count=100,
        delete_candidates=("anime_covers/old",),
        cache_sha256=file_sha256(paths.cache_file),
        receipt_sha256="c" * 64,
    )
    monkeypatch.setattr(cleaner, "read_manifest", lambda path: reviewed)
    monkeypatch.setattr(cleaner, "load_execution_receipt", lambda path: receipt)
    monkeypatch.setattr(cleaner, "validate_trusted_manifest_time", lambda value: None)
    monkeypatch.setattr(
        cleaner,
        "require_protected_execution_context",
        lambda: "a" * 40,
    )

    assert main() == 0

    assert service.plan_calls == [30]
    assert service.prepare_calls == 0
    assert service.execute_calls == 1
