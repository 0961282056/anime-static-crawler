"""Deterministic application/data versions and the public deployment inventory."""

from __future__ import annotations

import hashlib
import json

from services.atomic_io import atomic_write_json
from services.production_verification import release_commit


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def data_inventory(paths) -> dict:
    quarters = {}
    for path in sorted(paths.data_dir.glob("*.json")):
        quarters[path.stem] = digest(path.read_bytes())
    return inventory_from_versions(quarters)


def inventory_from_versions(quarters: dict[str, str]) -> dict:
    return {
        "data_version": digest(
            json.dumps(quarters, sort_keys=True, separators=(",", ":")).encode()
        )[:16],
        "quarter_versions": quarters,
    }


def served_files(paths) -> dict:
    files = [
        p
        for root in (paths.data_dir, paths.static_output_dir)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]
    for name in ("index.html", "search-index.json"):
        if (path := paths.output_dir / name).is_file():
            files.append(path)
    inventory = {}
    for path in sorted(files):
        content = path.read_bytes()
        inventory[path.relative_to(paths.output_dir).as_posix()] = {
            "sha256": digest(content),
            "bytes": len(content),
        }
    return inventory


def write_release(paths, environment, build_version, *, data_snapshot=None):
    inventory = (
        data_snapshot.data_inventory() if data_snapshot else data_inventory(paths)
    )
    manifest = {
        "schema_version": 1,
        "commit": release_commit(paths, environment),
        "build_version": build_version,
        **inventory,
        "files": served_files(paths),
    }
    if data_snapshot:
        data_snapshot.assert_unchanged()
        expected = {
            f"data/{quarter}.json": version
            for quarter, version in inventory["quarter_versions"].items()
        }
        observed = {
            name: details.get("sha256")
            for name, details in manifest["files"].items()
            if name.startswith("data/")
            and name.endswith(".json")
            and "/" not in name[len("data/") :]
        }
        if observed != expected:
            raise RuntimeError("Quarterly data changed while creating the release")
    atomic_write_json(paths.output_dir / "release.json", manifest)
    return manifest


def validate_release(paths):
    path = paths.output_dir / "release.json"
    if not path.exists():
        return
    manifest = json.loads(path.read_text())
    from generate_static import compute_build_version

    if (
        manifest.get("schema_version") != 1
        or manifest.get("files") != served_files(paths)
        or manifest.get("build_version") != compute_build_version(paths)
    ):
        raise RuntimeError("Release inventory does not match the generated output")
    for key, value in data_inventory(paths).items():
        if manifest.get(key) != value:
            raise RuntimeError("Release data version does not match the quarterly JSON")
