from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.frontend_bundle import (
    build_frontend_bundle,
    render_frontend_bundle,
    verify_frontend_bundle,
)


def setup_modules(root):
    source = root / "frontend"
    source.mkdir()
    (source / "settings.js").write_text("const value = 1;\n")
    (source / "app.js").write_text("function app() { return value; }\n")
    manifest = {
        "schema_version": 1,
        "modules": [
            {"name": "settings", "file": "settings.js", "requires": []},
            {"name": "app", "file": "app.js", "requires": ["settings"]},
        ],
    }
    (source / "modules.json").write_text(json.dumps(manifest))
    return source, manifest


def test_stale_source_fails_even_when_a_build_version_is_supplied(
    tmp_path, monkeypatch
):
    from generate_static import compute_build_version
    from services.settings import ProjectPaths

    source, _ = setup_modules(tmp_path)
    build_frontend_bundle(tmp_path)
    verify_frontend_bundle(tmp_path)
    assert (
        render_frontend_bundle(tmp_path) == (tmp_path / "static/js/main.js").read_text()
    )
    (source / "app.js").write_text("function app() { return 2; }\n")
    monkeypatch.setenv("BUILD_VERSION", "explicit")
    paths = ProjectPaths(
        tmp_path,
        tmp_path / "dist",
        tmp_path / "dist/data",
        tmp_path / "templates",
        tmp_path / "static",
        tmp_path / "dist/static",
        tmp_path / "cache.json",
        tmp_path / "_headers",
    )
    with pytest.raises(ValueError, match="bundle is stale"):
        compute_build_version(paths)
    build_frontend_bundle(tmp_path)
    assert compute_build_version(paths) == "explicit"


@pytest.mark.parametrize("change", ["order", "duplicate", "path", "unlisted"])
def test_invalid_module_graph_and_unlisted_source_are_rejected(tmp_path, change):
    source, manifest = setup_modules(tmp_path)
    if change == "order":
        manifest["modules"].reverse()
    elif change == "duplicate":
        manifest["modules"].append(manifest["modules"][0])
    elif change == "path":
        manifest["modules"][0]["file"] = "../secret.js"
    else:
        (source / "forgotten.js").write_text("throw new Error('forgotten')")
    (source / "modules.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="module"):
        render_frontend_bundle(tmp_path)


def test_checked_in_bundle_matches_its_reviewable_sources():
    root = Path(__file__).resolve().parents[1]
    assert (root / "frontend/modules.json").is_file()
    verify_frontend_bundle(root)
