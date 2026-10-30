from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).parents[1]
ALPINE_CSP_SHA256 = "6ae52782db18a68b5d26d357f8000d0c934b7d72ec99263d2bd351f07956a35a"


def test_csp_does_not_allow_unsafe_eval() -> None:
    headers = (ROOT / "_headers").read_text(encoding="utf-8")

    assert "Content-Security-Policy:" in headers
    assert "'unsafe-eval'" not in headers


def test_template_uses_registered_local_alpine_csp_build() -> None:
    base_template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    index_template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    main_script = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "static/vendor/alpine-csp-3.15.12.min.js" in base_template
    assert "cdn.jsdelivr.net/npm/alpinejs" not in base_template
    assert 'x-data="animeApp"' in index_template
    assert "Alpine.data('animeApp', animeApp)" in main_script


def test_vendored_alpine_csp_build_matches_reviewed_hash() -> None:
    vendor_file = ROOT / "static" / "vendor" / "alpine-csp-3.15.12.min.js"

    assert hashlib.sha256(vendor_file.read_bytes()).hexdigest() == ALPINE_CSP_SHA256


def test_alpine_storage_guard_is_the_only_change_from_upstream() -> None:
    vendor_file = ROOT / "static" / "vendor" / "alpine-csp-3.15.12.min.js"
    upstream_loop = (
        "Object.getOwnPropertyNames(globalThis).forEach(t=>{"
        't==="styleMedia"||t==="sharedStorage"||cn.add(globalThis[t])});'
    )
    guarded_loop = (
        "Object.getOwnPropertyNames(globalThis).forEach(t=>{"
        'if(t==="styleMedia"||t==="sharedStorage")return;'
        "try{cn.add(globalThis[t])}catch{}});"
    )
    patched = vendor_file.read_bytes()
    assert patched.count(guarded_loop.encode()) == 1
    restored = patched.replace(guarded_loop.encode(), upstream_loop.encode())
    assert hashlib.sha256(restored).hexdigest() == (
        "566167134bb2347110904e2ced6e816d2e8d837200c158f98b72372b3bb0b9a6"
    )
