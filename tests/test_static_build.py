from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import generate_static
from generate_static import _safe_replace_directory, sync_static_assets
from manage import verify_dist
from services.settings import ProjectPaths


def _write_source_assets(paths: ProjectPaths) -> None:
    (paths.static_source_dir / "js").mkdir(parents=True)
    (paths.static_source_dir / "css").mkdir(parents=True)
    (paths.static_source_dir / "js" / "main.js").write_text(
        "console.log('source');\n",
        encoding="utf-8",
    )
    (paths.static_source_dir / "css" / "style.css").write_text(
        "body { color: black; }\n",
        encoding="utf-8",
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_static_sync_makes_dist_an_exact_copy_and_copies_headers(
    project_paths: ProjectPaths,
) -> None:
    _write_source_assets(project_paths)
    project_paths.static_output_dir.mkdir(parents=True)
    (project_paths.static_output_dir / "stale.js").write_text(
        "stale\n",
        encoding="utf-8",
    )
    project_paths.cloudflare_headers_file.write_bytes(
        b"/*\r\n  X-Content-Type-Options: nosniff\r\n",
    )

    sync_static_assets(project_paths)

    assert _tree(project_paths.static_output_dir) == _tree(
        project_paths.static_source_dir
    )
    assert not (project_paths.static_output_dir / "stale.js").exists()
    assert (project_paths.output_dir / "_headers").read_bytes() == (
        b"/*\n  X-Content-Type-Options: nosniff\n"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL inheritance integration")
def test_windows_static_staging_and_repeat_build_inherit_output_acl(project_paths):
    _write_source_assets(project_paths)
    project_paths.output_dir.mkdir(parents=True)
    user_sid = subprocess.check_output(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        text=True,
    ).strip()
    # A pytest temp tree may itself be private. Give only this test's output
    # root an inheritable current-user ACE, as a normal project dist has.
    subprocess.run(
        [
            "icacls",
            str(project_paths.output_dir),
            "/inheritancelevel:e",
            "/grant",
            f"*{user_sid}:(OI)(CI)(F)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    script = """
    $ErrorActionPreference = 'Stop'
    $targetAcl = [System.IO.Directory]::GetAccessControl($env:STATIC_ACL_TEST_PATH)
    $expectedSid = $env:STATIC_ACL_TEST_SID
    $foundRule = @($targetAcl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
      Where-Object { $_.IdentityReference.Value -eq $expectedSid -and $_.IsInherited -and $_.AccessControlType -eq 'Allow' })
    [pscustomobject]@{ protected = $targetAcl.AreAccessRulesProtected; inheritedUser = $foundRule.Count -gt 0 } | ConvertTo-Json -Compress
    """
    staging = generate_static._create_static_staging_root(project_paths.output_dir)
    try:
        for _ in range(2):
            sync_static_assets(project_paths)
            for directory in (
                staging,
                project_paths.static_output_dir,
                project_paths.static_output_dir / "js",
            ):
                result = subprocess.check_output(
                    ["powershell.exe", "-NoProfile", "-Command", script],
                    env={
                        **os.environ,
                        "STATIC_ACL_TEST_PATH": str(directory),
                        "STATIC_ACL_TEST_SID": user_sid,
                    },
                    text=True,
                )
                acl = json.loads(result)
                assert acl["protected"] is False
                assert acl["inheritedUser"] is True
        assert _tree(project_paths.static_output_dir) == _tree(
            project_paths.static_source_dir
        )
        assert not list(project_paths.output_dir.glob(".static.backup-*"))
    finally:
        staging.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows staging branch")
def test_windows_staging_retries_name_collision_without_reusing_directory(
    tmp_path, monkeypatch
):
    existing = tmp_path / ".static-build-existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("preserve", encoding="utf-8")
    names = iter(["existing", "new"])
    monkeypatch.setattr(
        generate_static.uuid, "uuid4", lambda: SimpleNamespace(hex=next(names))
    )
    staging = generate_static._create_static_staging_root(tmp_path)
    assert staging == tmp_path / ".static-build-new"
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "preserve"


def test_verify_dist_rejects_asset_drift(
    project_paths: ProjectPaths,
) -> None:
    _write_source_assets(project_paths)
    sync_static_assets(project_paths)
    (project_paths.output_dir / "index.html").write_text(
        "<!doctype html>\n",
        encoding="utf-8",
    )
    verify_dist(project_paths)

    (project_paths.static_output_dir / "js" / "main.js").write_text(
        "console.log('drift');\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="not an exact build"):
        verify_dist(project_paths)


def test_static_copy_error_propagates_and_keeps_existing_output(
    project_paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_source_assets(project_paths)
    project_paths.static_output_dir.mkdir(parents=True)
    existing = project_paths.static_output_dir / "existing.js"
    existing.write_text("existing\n", encoding="utf-8")

    def fail_copytree(source: Path, destination: Path) -> None:
        raise OSError("simulated static copy failure")

    monkeypatch.setattr(generate_static.shutil, "copytree", fail_copytree)

    with pytest.raises(OSError, match="simulated static copy failure"):
        sync_static_assets(project_paths)

    assert existing.read_text(encoding="utf-8") == "existing\n"
    assert not list(project_paths.output_dir.glob(".static-build-*"))


def test_static_swap_rolls_back_existing_output_when_rename_fails(
    project_paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_paths.output_dir.mkdir(parents=True)
    source = project_paths.output_dir / ".static-build-test" / "static"
    source.mkdir(parents=True)
    (source / "new.js").write_text("new\n", encoding="utf-8")
    destination = project_paths.static_output_dir
    destination.mkdir()
    existing = destination / "existing.js"
    existing.write_text("existing\n", encoding="utf-8")
    real_rename = Path.rename

    def fail_source_rename(path: Path, target: Path) -> Path:
        if path == source:
            raise OSError("simulated rename failure")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_source_rename)

    with pytest.raises(OSError, match="simulated rename failure"):
        _safe_replace_directory(
            source,
            destination,
            project_paths.output_dir,
        )

    assert existing.read_text(encoding="utf-8") == "existing\n"
    assert not list(project_paths.output_dir.glob(".static.backup-*"))


def test_static_swap_retries_transient_permission_errors(
    project_paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_paths.output_dir.mkdir(parents=True)
    source = project_paths.output_dir / ".static-build-test" / "static"
    source.mkdir(parents=True)
    (source / "new.js").write_text("new\n", encoding="utf-8")
    destination = project_paths.static_output_dir
    destination.mkdir()
    (destination / "existing.js").write_text("existing\n", encoding="utf-8")
    real_rename = Path.rename
    destination_attempts = 0
    delays: list[float] = []

    def transient_destination_lock(path: Path, target: Path) -> Path:
        nonlocal destination_attempts
        if path == destination and destination_attempts < 2:
            destination_attempts += 1
            raise PermissionError(5, "simulated Windows directory lock")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", transient_destination_lock)
    monkeypatch.setattr(generate_static.time, "sleep", delays.append)

    _safe_replace_directory(source, destination, project_paths.output_dir)

    assert destination_attempts == 2
    assert delays == [0.1, 0.2]
    assert (destination / "new.js").read_text(encoding="utf-8") == "new\n"
    assert not list(project_paths.output_dir.glob(".static.backup-*"))


def test_static_swap_fails_closed_after_permission_retries_are_exhausted(
    project_paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_paths.output_dir.mkdir(parents=True)
    source = project_paths.output_dir / ".static-build-test" / "static"
    source.mkdir(parents=True)
    (source / "new.js").write_text("new\n", encoding="utf-8")
    destination = project_paths.static_output_dir
    destination.mkdir()
    existing = destination / "existing.js"
    existing.write_text("existing\n", encoding="utf-8")
    real_rename = Path.rename
    delays: list[float] = []

    def persistent_destination_lock(path: Path, target: Path) -> Path:
        if path == destination:
            raise PermissionError(5, "simulated persistent Windows directory lock")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", persistent_destination_lock)
    monkeypatch.setattr(generate_static.time, "sleep", delays.append)

    with pytest.raises(PermissionError, match="persistent Windows directory lock"):
        _safe_replace_directory(source, destination, project_paths.output_dir)

    assert delays == [0.1, 0.2, 0.4, 0.8]
    assert existing.read_text(encoding="utf-8") == "existing\n"
    assert (source / "new.js").read_text(encoding="utf-8") == "new\n"
    assert not list(project_paths.output_dir.glob(".static.backup-*"))


def test_verify_dist_rejects_missing_index_and_header_drift(
    project_paths: ProjectPaths,
) -> None:
    _write_source_assets(project_paths)
    project_paths.cloudflare_headers_file.write_bytes(b"/*\n  X-Test: yes\n")
    sync_static_assets(project_paths)

    with pytest.raises(RuntimeError, match="index.html is missing"):
        verify_dist(project_paths)

    (project_paths.output_dir / "index.html").write_text(
        "<!doctype html>\n",
        encoding="utf-8",
    )
    (project_paths.output_dir / "_headers").write_bytes(b"wrong\n")

    with pytest.raises(RuntimeError, match="_headers does not match"):
        verify_dist(project_paths)


def test_verify_dist_requires_canonical_lf_headers(
    project_paths: ProjectPaths,
) -> None:
    _write_source_assets(project_paths)
    project_paths.cloudflare_headers_file.write_bytes(b"/*\r\n  X-Test: yes\r\n")
    sync_static_assets(project_paths)
    (project_paths.output_dir / "index.html").write_text(
        "<!doctype html>\n",
        encoding="utf-8",
    )

    built_headers = project_paths.output_dir / "_headers"
    assert built_headers.read_bytes() == b"/*\n  X-Test: yes\n"
    verify_dist(project_paths)

    built_headers.write_bytes(b"/*\r\n  X-Test: yes\r\n")
    with pytest.raises(RuntimeError, match="_headers does not match"):
        verify_dist(project_paths)
