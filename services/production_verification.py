"""Read-only, bounded verification of the canonical public deployment."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

from services.atomic_io import atomic_write_json
from services.settings import ProjectPaths

SITE = "https://anime-static-crawler.pages.dev/"
REPOSITORY_API = "https://api.github.com/repos/0961282056/anime-static-crawler"
COMMIT = re.compile(r"[0-9a-f]{40}")


def release_commit(paths: ProjectPaths, environment: dict) -> str:
    value = environment.get("RELEASE_COMMIT") or environment.get("CF_PAGES_COMMIT_SHA")
    if value:
        if not COMMIT.fullmatch(value):
            raise ValueError("Release commit must be a full lowercase git SHA")
        return value
    try:
        return subprocess.check_output(
            ["git", "-C", str(paths.root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"  # Temporary fixture trees have no git metadata.


class PublicReader:
    def __init__(self):
        self.local = threading.local()
        self.deadline = float("inf")

    def __call__(self, url: str) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Production verification request budget exhausted")
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
        with self.local.session.get(
            url,
            timeout=(min(5, remaining), min(15, remaining)),
            allow_redirects=False,
            stream=True,
            headers={"Cache-Control": "no-cache"},
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Public verification HTTP {response.status_code}")
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if time.monotonic() >= self.deadline:
                    raise RuntimeError(
                        "Production verification request budget exhausted"
                    )
                content.extend(chunk)
                if len(content) > 10 * 1024 * 1024:
                    raise RuntimeError("Public verification response exceeds 10 MiB")
            return bytes(content)


def expected_files(paths: ProjectPaths) -> dict[str, bytes]:
    marker = paths.output_dir / "release.json"
    if marker.is_file():
        inventory = json.loads(marker.read_text()).get("files")
        if inventory:
            return {path: (paths.output_dir / path).read_bytes() for path in inventory}
    return {
        path.relative_to(paths.output_dir).as_posix(): path.read_bytes()
        for root in (paths.data_dir, paths.static_output_dir)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def is_superseded(expected: str, deployed: str, read) -> bool:
    """A foreign preview/branch commit cannot masquerade as a newer main."""
    main = json.loads(read(f"{REPOSITORY_API}/commits/main"))["sha"]
    if not COMMIT.fullmatch(main):
        return False
    first = json.loads(read(f"{REPOSITORY_API}/compare/{expected}...{deployed}"))
    if (
        first.get("status") != "ahead"
        or first.get("merge_base_commit", {}).get("sha") != expected
    ):
        return False
    if main == deployed:
        return True
    second = json.loads(read(f"{REPOSITORY_API}/compare/{deployed}...{main}"))
    return (
        second.get("status") == "ahead"
        and second.get("merge_base_commit", {}).get("sha") == deployed
    )


def verify_once(paths: ProjectPaths, expected: str, read, *, attempt: int = 0) -> dict:
    marker = json.loads(
        read(f"{SITE}release.json?expected={expected}&attempt={attempt}")
    )
    deployed = marker.get("commit", "")
    if marker.get("schema_version") != 1 or not COMMIT.fullmatch(deployed):
        raise RuntimeError("Canonical release marker is invalid or not yet available")
    if deployed != expected:
        if is_superseded(expected, deployed, read):
            return {
                "status": "superseded",
                "expected_commit": expected,
                "deployed_commit": deployed,
            }
        raise RuntimeError(
            "Canonical deployment has not reached the expected main commit"
        )
    local_marker = paths.output_dir / "release.json"
    if local_marker.exists() and json.loads(local_marker.read_text()) != marker:
        raise RuntimeError("Canonical release inventory differs from expected build")
    files = expected_files(paths)
    if not any(path.startswith("data/") for path in files):
        raise RuntimeError("No expected quarterly data is available")

    def check(item):
        path, content = item
        actual = read(f"{SITE}{quote(path)}?release={expected}&attempt={attempt}")
        if hashlib.sha256(actual).digest() != hashlib.sha256(content).digest():
            raise RuntimeError(f"Canonical content differs: {path}")
        return path

    with ThreadPoolExecutor(max_workers=4) as pool:
        verified = list(pool.map(check, files.items()))
    home = read(f"{SITE}?release={expected}&attempt={attempt}").decode("utf-8")
    from generate_static import compute_build_version

    if f'data-build-version="{compute_build_version(paths)}"' not in home:
        raise RuntimeError(
            "Canonical homepage belongs to a different application build"
        )
    # Re-read after the full comparison so a concurrent deployment is not
    # reported as a verified mixture of two releases.
    final = json.loads(
        read(f"{SITE}release.json?expected={expected}&attempt={attempt}&final=1")
    )
    if final != marker:
        raise RuntimeError("Canonical deployment changed while verifying")
    return {
        "status": "verified",
        "expected_commit": expected,
        "deployed_commit": deployed,
        "quarters": sum(path.startswith("data/") for path in verified),
        "assets": sum(path.startswith("static/") for path in verified),
        "records": sum(
            len(json.loads(content)["anime_list"])
            for path, content in files.items()
            if path.startswith("data/")
        ),
        "files": verified,
    }


def wait_for_production(
    paths: ProjectPaths,
    expected: str,
    output: Path,
    *,
    timeout: int = 600,
    read=None,
    clock=time.monotonic,
    wait=time.sleep,
) -> dict:
    if not COMMIT.fullmatch(expected):
        raise ValueError("Expected commit must be a full lowercase git SHA")
    reader = read or PublicReader()
    deadline = clock() + timeout
    if isinstance(reader, PublicReader):
        reader.deadline = deadline
    attempt = 0
    error = "Canonical deployment is not available"
    while clock() < deadline:
        attempt += 1
        try:
            result = verify_once(paths, expected, reader, attempt=attempt)
        except (
            RuntimeError,
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            error = str(exc)
            atomic_write_json(
                output,
                {
                    "status": "waiting",
                    "expected_commit": expected,
                    "attempts": attempt,
                    "detail": error,
                },
            )
            wait(min(15, max(0, deadline - clock())))
            continue
        result["attempts"] = attempt
        atomic_write_json(output, result)
        return result
    result = {
        "status": "failed",
        "expected_commit": expected,
        "attempts": attempt,
        "detail": error,
    }
    atomic_write_json(output, result)
    raise RuntimeError(f"Production verification timed out: {error}")
