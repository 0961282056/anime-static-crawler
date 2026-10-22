"""A build reuses validated models, and refuses a changing data directory."""

from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

import generate_static
import services.data_repository as data_repository_module
from models import TAIPEI_TZ
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import DataContractError
from services.release_manifest import (
    digest,
    served_files,
    validate_release,
    write_release,
)
from services.search_index import build_search_index

NOW = datetime(2026, 10, 22, 12, tzinfo=TAIPEI_TZ)


def prepare(paths, monkeypatch, anime_record_factory):
    shutil.copytree(Path(__file__).parents[1] / "templates", paths.templates_dir)
    paths.static_source_dir.mkdir(parents=True)
    (paths.static_source_dir / "main.js").write_text("console.log('fixture');\n")
    monkeypatch.setenv("BUILD_ONLY", "true")
    monkeypatch.setenv("BUILD_VERSION", "snapshot-tests")
    monkeypatch.setenv("RELEASE_COMMIT", "b" * 40)
    monkeypatch.setattr(generate_static, "load_dotenv", lambda: None)
    monkeypatch.setattr(generate_static, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        generate_static.ProjectPaths,
        "from_environment",
        classmethod(lambda cls: paths),
    )
    monkeypatch.setattr(
        generate_static,
        "datetime",
        type("Clock", (), {"now": staticmethod(lambda timezone: NOW)}),
    )
    repo = DataRepository(paths.data_dir, DataQualityPolicy())
    repo.write_quarter(
        year="2026",
        season="秋",
        records=[anime_record_factory(1, name="Alpha")],
        source_url="https://acgsecrets.hk/bangumi/202610/",
        source_count=1,
        parse_failure_count=0,
        generated_at=NOW,
    )
    return repo


def test_build_parses_each_quarter_once_and_preserves_standalone_output(
    project_paths, monkeypatch, anime_record_factory
):
    repo = prepare(project_paths, monkeypatch, anime_record_factory)
    for season, month in [("春", "04"), ("夏", "07")]:
        repo.write_quarter(
            year="2026",
            season=season,
            records=[anime_record_factory(1, name="Alpha")],
            source_url=f"https://acgsecrets.hk/bangumi/2026{month}/",
            source_count=1,
            parse_failure_count=0,
            generated_at=NOW,
        )
    parses = Counter()
    decode = data_repository_module._decode_dataset

    def count(path, content):
        parses[path.name] += 1
        return decode(path, content)

    monkeypatch.setattr(data_repository_module, "_decode_dataset", count)
    generate_static.generate_static_files()
    assert parses == {"2026_春.json": 1, "2026_夏.json": 1, "2026_秋.json": 1}
    built = json.loads((project_paths.output_dir / "search-index.json").read_text())
    assert built == build_search_index(project_paths, repo)
    homepage = (project_paths.output_dir / "index.html").read_bytes()
    generate_static.render_index(project_paths, repo, NOW)
    assert (project_paths.output_dir / "index.html").read_bytes() == homepage
    validate_release(project_paths)


@pytest.mark.parametrize("mutation", ["add", "delete", "same_size_and_mtime"])
def test_mid_build_drift_fails_without_replacing_the_release(
    project_paths, monkeypatch, anime_record_factory, mutation
):
    repo = prepare(project_paths, monkeypatch, anime_record_factory)
    generate_static.generate_static_files()
    marker = project_paths.output_dir / "release.json"
    before = marker.read_bytes()
    path = repo.quarter_path("2026", "秋")
    sync_assets = generate_static.sync_static_assets

    def change_data(paths):
        sync_assets(paths)
        if mutation == "add":
            (paths.data_dir / "2027_冬.json").write_bytes(path.read_bytes())
        elif mutation == "delete":
            path.unlink()
        else:
            stamp = path.stat()
            content = path.read_bytes()
            changed = content.replace(b"Alpha", b"Bravo")
            assert changed != content and len(changed) == len(content)
            path.write_bytes(changed)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    monkeypatch.setattr(generate_static, "sync_static_assets", change_data)
    with pytest.raises(DataContractError, match="changed during the build"):
        generate_static.generate_static_files()
    assert marker.read_bytes() == before


def test_snapshot_and_direct_apis_keep_quality_validation_and_refresh_behavior(
    project_paths, monkeypatch, anime_record_factory
):
    repo = prepare(project_paths, monkeypatch, anime_record_factory)
    snapshot = repo.validated_snapshot()
    assert snapshot.data_inventory() == repo.data_inventory()
    with pytest.raises(DataContractError, match="cannot write"):
        snapshot.write_quarter()
    path = repo.quarter_path("2026", "秋")
    raw = json.loads(path.read_text())
    raw["anime_list"][0]["anime_name"] = "Bravo"
    path.write_text(json.dumps(raw))
    assert (
        build_search_index(project_paths, repo)["entries"][0]["identity"][1] == "Bravo"
    )
    assert repo.validated_snapshot().load_path(path).anime_list[0].anime_name == "Bravo"
    with pytest.raises(DataContractError, match="changed during the build"):
        snapshot.validate_all()
    raw["quality"]["missing_story_count"] = 1
    path.write_text(json.dumps(raw))
    for operation in [
        repo.validated_snapshot,
        repo.validate_all,
        lambda: build_search_index(project_paths, repo),
    ]:
        with pytest.raises(DataContractError, match="quality summary"):
            operation()
    with pytest.raises(DataContractError, match="changed during the build"):
        snapshot.validate_all()


@pytest.mark.parametrize("mutation", ["hash", "extra_quarter", "extra_json", "missing"])
def test_release_cannot_bind_transient_mixed_inventory_even_if_files_are_restored(
    project_paths, monkeypatch, anime_record_factory, mutation
):
    import services.release_manifest as release_manifest

    repo = prepare(project_paths, monkeypatch, anime_record_factory)
    generate_static.generate_static_files()
    snapshot = repo.validated_snapshot()
    before = (project_paths.output_dir / "release.json").read_bytes()
    original_served_files = release_manifest.served_files

    def inconsistent_inventory(paths):
        path = repo.quarter_path("2026", "秋")
        original = path.read_bytes()
        if mutation.startswith("extra"):
            extra = paths.data_dir / (
                "2027_冬.json" if mutation == "extra_quarter" else "metadata.json"
            )
            extra.write_bytes(original)
            try:
                return original_served_files(paths)
            finally:
                extra.unlink()
        if mutation == "missing":
            path.unlink()
            try:
                return original_served_files(paths)
            finally:
                path.write_bytes(original)
        files = original_served_files(paths)
        files["data/2026_秋.json"]["sha256"] = "a" * 64
        return files

    monkeypatch.setattr(release_manifest, "served_files", inconsistent_inventory)
    with pytest.raises(RuntimeError, match="changed while creating the release"):
        write_release(
            project_paths,
            {"RELEASE_COMMIT": "c" * 40},
            "snapshot-tests",
            data_snapshot=snapshot,
        )
    assert (project_paths.output_dir / "release.json").read_bytes() == before


def test_served_file_digest_and_size_describe_the_same_read(project_paths, monkeypatch):
    target = project_paths.output_dir / "index.html"
    original = b"original page"
    target.parent.mkdir(parents=True)
    target.write_bytes(original)
    read_bytes = Path.read_bytes

    def replace_after_read(path):
        content = read_bytes(path)
        if path == target:
            path.write_bytes(b"a different page with a different length")
        return content

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    assert served_files(project_paths)["index.html"] == {
        "sha256": digest(original),
        "bytes": len(original),
    }
