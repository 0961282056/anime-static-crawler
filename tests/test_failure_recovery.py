from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

import generate_static
import services.anime_service as service_module
import services.atomic_io as atomic_io
from services.anime_service import AnimeCrawlerService
from services.errors import ImageStoreError


def test_fsync_failure_preserves_previous_file_and_removes_temporary_file(
    tmp_path, monkeypatch
):
    destination = tmp_path / "data.json"
    destination.write_text("previous", encoding="utf-8")

    def fail_fsync(descriptor):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_io.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="disk full"):
        atomic_io.atomic_write_text(destination, "replacement")
    assert destination.read_text(encoding="utf-8") == "previous"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_cleanup_failure_does_not_hide_first_error(tmp_path, monkeypatch):
    destination = tmp_path / "data.json"
    destination.write_text("previous", encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("primary replace failed")

    def fail_unlink(path, **kwargs):
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError, match="primary replace failed") as caught:
        atomic_io.atomic_write_text(destination, "replacement")
    assert destination.read_text(encoding="utf-8") == "previous"
    assert any("cleanup denied" in note for note in caught.value.__notes__)


def test_failed_static_rollback_keeps_backup_and_reports_original_error(
    project_paths, monkeypatch
):
    output = project_paths.output_dir
    output.mkdir(parents=True)
    source = output / ".new-static"
    source.mkdir()
    destination = project_paths.static_output_dir
    destination.mkdir()
    (destination / "old.js").write_text("previous", encoding="utf-8")
    real_rename = generate_static._rename_directory_with_retry

    def fail_rename(source_path, target):
        if source_path == source:
            raise OSError("primary swap failed")
        if source_path.name.startswith(".static.backup-"):
            raise PermissionError("rollback denied")
        return real_rename(source_path, target)

    monkeypatch.setattr(generate_static, "_rename_directory_with_retry", fail_rename)
    with pytest.raises(OSError, match="primary swap failed") as caught:
        generate_static._safe_replace_directory(source, destination, output)
    backups = list(output.glob(".static.backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "old.js").read_text(encoding="utf-8") == "previous"
    assert any(
        str(backups[0]) in note and "Rollback also failed" in note
        for note in caught.value.__notes__
    )


def test_temporary_build_cleanup_failure_keeps_first_error(project_paths, monkeypatch):
    project_paths.static_source_dir.mkdir(parents=True)

    def fail_copy(*args, **kwargs):
        raise OSError("primary copy failed")

    def fail_cleanup(*args, **kwargs):
        raise PermissionError("temporary directory denied")

    monkeypatch.setattr(generate_static.shutil, "copytree", fail_copy)
    monkeypatch.setattr(generate_static.shutil, "rmtree", fail_cleanup)
    with pytest.raises(OSError, match="primary copy failed") as caught:
        generate_static.sync_static_assets(project_paths)
    assert any("cleanup also failed" in note for note in caught.value.__notes__)


def test_system_failure_cancels_queued_work_and_keeps_error_when_cache_save_fails(
    monkeypatch,
):
    futures = []
    failure = ImageStoreError("primary image offline")

    class Executor:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, command, item):
            future = Future()
            futures.append(future)
            if item == "failed":
                future.set_exception(failure)
            return future

    def fail_cache():
        raise PermissionError("cache access denied")

    crawler = AnimeCrawlerService(
        settings=SimpleNamespace(max_workers=1),
        source_client=SimpleNamespace(
            fetch_quarter_html=lambda *args: ("https://source.example/202607", "unused")
        ),
        image_store=SimpleNamespace(assert_quota_available=lambda: None),
        cache=SimpleNamespace(save_if_changed=fail_cache),
    )
    monkeypatch.setattr(service_module, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(
        service_module, "extract_item_html", lambda text: ["failed", "queued", "queued"]
    )
    with pytest.raises(ImageStoreError, match="primary image offline") as caught:
        crawler.fetch_quarter("2026", "夏")
    assert caught.value is failure
    assert all(future.cancelled() for future in futures[1:])
    assert caught.value.operation_context["item_index"] == 0
    assert any("cache access denied" in note for note in caught.value.__notes__)


def test_cache_save_failure_after_success_is_not_suppressed(monkeypatch):
    def fail_cache():
        raise PermissionError("cache write denied")

    crawler = AnimeCrawlerService(
        settings=SimpleNamespace(max_workers=1),
        source_client=SimpleNamespace(
            fetch_quarter_html=lambda *args: ("https://source.example", "unused")
        ),
        image_store=SimpleNamespace(assert_quota_available=lambda: None),
        cache=SimpleNamespace(save_if_changed=fail_cache),
    )
    monkeypatch.setattr(service_module, "extract_item_html", lambda text: [])
    with pytest.raises(PermissionError, match="cache write denied") as caught:
        crawler.fetch_quarter("2026", "夏")
    assert caught.value.operation_stage == "cache-save"
