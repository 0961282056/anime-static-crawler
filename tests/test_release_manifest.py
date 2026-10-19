import json

import pytest

from generate_static import compute_build_version
from services.release_manifest import data_inventory, validate_release, write_release


def prepare(paths):
    for folder in (
        paths.data_dir,
        paths.static_source_dir,
        paths.static_output_dir,
        paths.templates_dir,
    ):
        folder.mkdir(parents=True)
    (paths.data_dir / "2026_秋.json").write_text('{"anime_list": []}')
    (paths.static_source_dir / "main.js").write_text("app")
    (paths.static_output_dir / "main.js").write_text("app")
    (paths.output_dir / "index.html").write_text("home")


def test_data_only_change_updates_data_version_without_changing_application(
    project_paths,
):
    p = project_paths
    prepare(p)
    code = compute_build_version(p)
    first = write_release(p, {"RELEASE_COMMIT": "a" * 40}, code)
    validate_release(p)
    (p.data_dir / "2026_秋.json").write_text('{"anime_list": [1]}')
    assert compute_build_version(p) == code
    assert data_inventory(p)["data_version"] != first["data_version"]
    with pytest.raises(RuntimeError, match="inventory"):
        validate_release(p)
    second = write_release(p, {"RELEASE_COMMIT": "b" * 40}, code)
    assert second["quarter_versions"] != first["quarter_versions"]
    validate_release(p)


def test_manifest_is_deterministic_and_detects_asset_or_version_drift(project_paths):
    p = project_paths
    prepare(p)
    write_release(p, {"RELEASE_COMMIT": "a" * 40}, compute_build_version(p))
    original = (p.output_dir / "release.json").read_bytes()
    write_release(p, {"RELEASE_COMMIT": "a" * 40}, compute_build_version(p))
    assert (p.output_dir / "release.json").read_bytes() == original
    manifest = json.loads(original)
    manifest["data_version"] = "wrong"
    (p.output_dir / "release.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="data version"):
        validate_release(p)


def test_search_index_merges_only_exact_versions_and_keeps_latest_copy(
    project_paths, anime_record_factory
):
    from models import Anime
    from services.data_repository import DataQualityPolicy, DataRepository
    from services.search_index import validate_search_index, write_search_index

    p = project_paths
    repo = DataRepository(p.data_dir, DataQualityPolicy())
    first = Anime(
        **anime_record_factory(1, name="同一作品", story="過去的簡介"),
        original_name="Work 2",
    )
    latest = first.model_copy(update={"story": "現在のＳＴＯＲＹ"})
    other_version = Anime(
        **anime_record_factory(2, name="另一版本"), original_name="Work 3"
    )
    for season, records, month in [
        ("夏", [first], "07"),
        ("秋", [latest, other_version], "10"),
    ]:
        repo.write_quarter(
            year="2026",
            season=season,
            records=records,
            source_url=f"https://acgsecrets.hk/bangumi/2026{month}/",
            source_count=len(records),
            parse_failure_count=0,
        )
    result = write_search_index(p, repo)
    assert len(result["entries"]) == 2
    entry = result["entries"][0]
    assert entry["quarters"] == ["2026_秋", "2026_夏"]
    assert entry["story"] == "現在のstory"
    assert not any(
        "anime_image_url" in row or "episodes" in row for row in result["entries"]
    )
    validate_search_index(p, repo)
    result["entries"][0]["story"] = "wrong"
    (p.output_dir / "search-index.json").write_text(json.dumps(result))
    with pytest.raises(RuntimeError, match="Search index"):
        validate_search_index(p, repo)
