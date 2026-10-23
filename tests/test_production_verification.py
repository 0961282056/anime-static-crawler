import json

import pytest

from generate_static import compute_build_version
from services.production_verification import (
    REPOSITORY_API,
    SITE,
    is_superseded,
    release_commit,
    verify_once,
    wait_for_production,
)

SHA = "a" * 40
NEW = "b" * 40


@pytest.fixture
def reader(project_paths):
    p = project_paths
    p.data_dir.mkdir(parents=True)
    p.static_source_dir.mkdir()
    p.static_output_dir.mkdir()
    p.templates_dir.mkdir()
    (p.data_dir / "2026_秋.json").write_text('{"anime_list": [{"story": "原文"}]}')
    (p.static_source_dir / "main.js").write_text("app")
    (p.static_output_dir / "main.js").write_text("app")

    def read(url):
        path = url.removeprefix(SITE).split("?")[0]
        if path == "release.json":
            return json.dumps({"schema_version": 1, "commit": SHA}).encode()
        if not path:
            return (
                f'<div data-build-version="{compute_build_version(p)}"></div>'.encode()
            )
        from urllib.parse import unquote

        return (p.output_dir / unquote(path)).read_bytes()

    return read


def test_exact_public_release_checks_all_data_and_assets(project_paths, reader):
    result = verify_once(project_paths, SHA, reader)
    assert result["status"] == "verified"
    assert (result["quarters"], result["assets"], result["records"]) == (1, 1, 1)


def test_mixed_asset_release_is_rejected(project_paths, reader):
    def mixed(url):
        return b"wrong" if "/static/" in url else reader(url)

    with pytest.raises(RuntimeError, match="differs"):
        verify_once(project_paths, SHA, mixed)


def test_deployment_switch_during_verification_is_rejected(project_paths, reader):
    def switched(url):
        return (
            json.dumps({"schema_version": 1, "commit": NEW}).encode()
            if "final=1" in url
            else reader(url)
        )

    with pytest.raises(RuntimeError, match="changed while verifying"):
        verify_once(project_paths, SHA, switched)


@pytest.mark.parametrize("on_main", [True, False])
def test_newer_deployment_requires_main_ancestry(on_main):
    def read(url):
        if url.endswith("/commits/main"):
            return json.dumps({"sha": NEW if on_main else "c" * 40})
        if url.endswith(f"{SHA}...{NEW}"):
            return json.dumps({"status": "ahead", "merge_base_commit": {"sha": SHA}})
        return json.dumps(
            {"status": "diverged", "merge_base_commit": {"sha": "d" * 40}}
        )

    assert is_superseded(SHA, NEW, read) is on_main


def test_old_deployment_retries_and_timeout_leaves_evidence(
    project_paths, reader, tmp_path
):
    now = [0]

    def fail(url):
        raise RuntimeError("not deployed yet")

    def advance(seconds):
        now[0] += seconds

    out = tmp_path / "result.json"
    with pytest.raises(RuntimeError, match="timed out"):
        wait_for_production(
            project_paths,
            SHA,
            out,
            timeout=30,
            read=fail,
            clock=lambda: now[0],
            wait=advance,
        )
    assert json.loads(out.read_text())["status"] == "failed"
    result = wait_for_production(project_paths, SHA, out, read=reader)
    assert result["status"] == "verified"


def test_slow_deployment_stays_within_anonymous_api_budget(project_paths, tmp_path):
    now = [0]
    old = "c" * 40
    api_calls = []

    def read(url):
        if url.startswith(SITE):
            return json.dumps(
                {"schema_version": 1, "commit": old if now[0] < 450 else NEW}
            )
        api_calls.append(url)
        assert len(api_calls) <= 60, (
            "Verification exhausted its own anonymous API budget"
        )
        if url.endswith(f"{SHA}...{old}"):
            return json.dumps({"status": "behind", "merge_base_commit": {"sha": old}})
        if url.endswith(f"{SHA}...{NEW}"):
            return json.dumps({"status": "ahead", "merge_base_commit": {"sha": SHA}})
        assert url == f"{REPOSITORY_API}/commits/main"
        return json.dumps({"sha": NEW})

    result = wait_for_production(
        project_paths,
        SHA,
        tmp_path / "receipt.json",
        read=read,
        clock=lambda: now[0],
        wait=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert result["status"] == "superseded" and result["attempts"] == 31
    assert len(api_calls) == 3


def test_main_membership_is_rechecked_after_a_foreign_preview(project_paths, tmp_path):
    now = [0]
    other = "c" * 40
    main_calls = []

    def read(url):
        if url.startswith(SITE):
            return json.dumps({"schema_version": 1, "commit": NEW})
        if url.endswith(f"{SHA}...{NEW}"):
            return json.dumps({"status": "ahead", "merge_base_commit": {"sha": SHA}})
        if url.endswith("/commits/main"):
            main_calls.append(now[0])
            return json.dumps({"sha": other if now[0] == 0 else NEW})
        assert url.endswith(f"{NEW}...{other}")
        return json.dumps({"status": "diverged", "merge_base_commit": {"sha": SHA}})

    result = wait_for_production(
        project_paths,
        SHA,
        tmp_path / "receipt.json",
        read=read,
        clock=lambda: now[0],
        wait=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert main_calls == [0, 15]
    assert result["status"] == "superseded" and result["attempts"] == 2


@pytest.mark.parametrize("failure", ["transport", "incomplete"])
def test_comparison_failures_are_not_cached(project_paths, tmp_path, failure):
    now = [0]
    comparisons = []

    def read(url):
        if url.startswith(SITE):
            return json.dumps({"schema_version": 1, "commit": NEW})
        if url.endswith("/commits/main"):
            return json.dumps({"sha": NEW})
        comparisons.append(url)
        if len(comparisons) == 1:
            if failure == "transport":
                raise RuntimeError("Public verification HTTP 403")
            return json.dumps({"status": "ahead"})
        return json.dumps({"status": "ahead", "merge_base_commit": {"sha": SHA}})

    result = wait_for_production(
        project_paths,
        SHA,
        tmp_path / "receipt.json",
        read=read,
        clock=lambda: now[0],
        wait=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert len(comparisons) == 2
    assert result["status"] == "superseded" and result["attempts"] == 2


def test_release_sha_validation_and_fixture_fallback(project_paths):
    assert release_commit(project_paths, {"CF_PAGES_COMMIT_SHA": SHA}) == SHA
    with pytest.raises(ValueError):
        release_commit(project_paths, {"RELEASE_COMMIT": "../bad"})
    assert release_commit(project_paths, {}) == "unknown"


def test_generated_release_manifest_is_compared_with_all_published_files(
    project_paths, reader
):
    from services.release_manifest import write_release

    home = f'<div data-build-version="{compute_build_version(project_paths)}"></div>'
    (project_paths.output_dir / "index.html").write_text(home)
    manifest = write_release(
        project_paths, {"RELEASE_COMMIT": SHA}, compute_build_version(project_paths)
    )

    def read(url):
        assert "/index.html" not in url, (
            "Cloudflare canonical homepage must be fetched directly"
        )
        if "/release.json" in url:
            return json.dumps(manifest).encode()
        return reader(url)

    result = verify_once(project_paths, SHA, read)
    assert result["status"] == "verified"
    assert "index.html" in result["files"]
    assert result["quarters"] == 1


def test_only_known_cloudflare_analytics_injection_is_removed():
    from services.production_verification import canonical_home

    block = (
        "<!-- Cloudflare Pages Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{\"token\":\""
        + "a" * 32
        + "\"}'></script><!-- Cloudflare Pages Analytics -->"
    ).encode()
    expected = b"<html><body>real content</body></html>"
    injected = expected.replace(b"</body>", block + b"</body>")
    assert canonical_home(injected) == expected
    assert (
        canonical_home(
            injected.replace(b"<script defer", b'<script onload="evil()" defer')
        )
        != expected
    )
    assert canonical_home(injected.replace(b"beacon.min.js", b"other.js")) != expected
    assert canonical_home(injected.replace(b'"token"', b'"extra"')) != expected
    assert canonical_home(injected.replace(block, block + block)) != expected
