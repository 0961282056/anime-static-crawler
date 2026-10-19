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
