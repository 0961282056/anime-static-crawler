from __future__ import annotations

from pathlib import Path

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
