"""Crawling and image uploads require an explicit operation at every entry."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dotenv import load_dotenv

import generate_static
import services.update_pipeline as pipeline
from models import TAIPEI_TZ
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import ConfigurationError


@pytest.fixture
def build_entry(project_paths, monkeypatch, anime_record_factory):
    shutil.copytree(
        Path(__file__).parents[1] / "templates", project_paths.templates_dir
    )
    project_paths.static_source_dir.mkdir(parents=True)
    (project_paths.static_source_dir / "main.js").write_text("// fixture\n")
    repo = DataRepository(project_paths.data_dir, DataQualityPolicy())
    repo.write_quarter(
        year="2026",
        season="秋",
        records=[anime_record_factory(1)],
        source_url="https://acgsecrets.hk/bangumi/202610/",
        source_count=1,
        parse_failure_count=0,
        generated_at=datetime(2026, 10, 23, 12, tzinfo=TAIPEI_TZ),
    )
    monkeypatch.delenv("BUILD_ONLY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("BUILD_VERSION", "entry-tests")
    monkeypatch.setenv("RELEASE_COMMIT", "c" * 40)
    monkeypatch.setattr(generate_static, "load_dotenv", lambda: None)
    monkeypatch.setattr(generate_static, "configure_runtime", lambda: None)
    paths = Mock(return_value=project_paths)
    settings = Mock(side_effect=AssertionError("build must not load crawler settings"))
    crawl = Mock(side_effect=AssertionError("crawl requires an explicit operation"))
    source = Mock(side_effect=AssertionError("tests must not contact sources"))
    images = Mock(side_effect=AssertionError("tests must not contact Cloudinary"))
    monkeypatch.setattr(generate_static.ProjectPaths, "from_environment", paths)
    monkeypatch.setattr(generate_static.CrawlerSettings, "from_environment", settings)
    monkeypatch.setattr(generate_static, "crawl_quarters", crawl)
    monkeypatch.setattr(pipeline, "SourceClient", source)
    monkeypatch.setattr(pipeline, "CloudinaryImageStore", images)
    return SimpleNamespace(
        project=project_paths,
        paths=paths,
        settings=settings,
        crawl=crawl,
        source=source,
        images=images,
    )


@pytest.mark.parametrize("entry", ["api", "cli"])
@pytest.mark.parametrize("guard", [None, "false", "true"])
def test_default_entry_builds_without_loading_crawler_configuration(
    build_entry, monkeypatch, entry, guard
):
    if guard is not None:
        monkeypatch.setenv("BUILD_ONLY", guard)
    if entry == "api":
        generate_static.generate_static_files()
    else:
        assert generate_static.main([]) == 0
    assert (build_entry.project.output_dir / "release.json").is_file()
    build_entry.settings.assert_not_called()
    build_entry.crawl.assert_not_called()
    build_entry.source.assert_not_called()
    build_entry.images.assert_not_called()


def test_legacy_dotenv_false_cannot_enable_crawling(build_entry, monkeypatch, tmp_path):
    legacy = tmp_path / ".env"
    legacy.write_text("BUILD_ONLY=false\n")
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(generate_static, "load_dotenv", lambda: load_dotenv(legacy))
    assert generate_static.main([]) == 0
    assert os.environ["BUILD_ONLY"] == "false"
    build_entry.settings.assert_not_called()
    build_entry.crawl.assert_not_called()
    build_entry.source.assert_not_called()
    build_entry.images.assert_not_called()


@pytest.mark.parametrize("entry", ["api", "cli"])
@pytest.mark.parametrize("guard", [None, "false"])
def test_explicit_crawl_reaches_only_the_mocked_orchestrator(
    build_entry, monkeypatch, entry, guard
):
    if guard is not None:
        monkeypatch.setenv("BUILD_ONLY", guard)
    build_entry.settings.side_effect = None
    build_entry.settings.return_value = SimpleNamespace(
        minimum_count_ratio=0.7,
        maximum_parse_failure_ratio=0,
        maximum_fallback_id_ratio=0,
    )
    build_entry.crawl.side_effect = None
    build_entry.crawl.return_value = generate_static.CrawlSummary(1, 0, 1, 0)
    if entry == "api":
        generate_static.generate_static_files(crawl=True)
    else:
        assert generate_static.main(["--crawl"]) == 0
    build_entry.settings.assert_called_once_with()
    build_entry.crawl.assert_called_once()
    assert (build_entry.project.output_dir / "release.json").is_file()
    build_entry.source.assert_not_called()
    build_entry.images.assert_not_called()


@pytest.mark.parametrize("guard", ["true", " TRUE "])
def test_build_guard_rejects_crawl_before_paths_settings_or_external_operations(
    build_entry, monkeypatch, capsys, guard
):
    monkeypatch.setenv("BUILD_ONLY", guard)
    assert generate_static.main(["--crawl"]) == 1
    assert "BUILD_ONLY=true prohibits --crawl" in capsys.readouterr().err
    build_entry.paths.assert_not_called()
    build_entry.settings.assert_not_called()
    build_entry.crawl.assert_not_called()
    build_entry.source.assert_not_called()
    build_entry.images.assert_not_called()


@pytest.mark.parametrize("crawl", [False, True])
@pytest.mark.parametrize("guard", ["", "0", "1", "yes", "flase"])
def test_invalid_guard_fails_before_any_build_or_crawl(
    build_entry, monkeypatch, crawl, guard
):
    monkeypatch.setenv("BUILD_ONLY", guard)
    with pytest.raises(ConfigurationError, match="BUILD_ONLY must be true or false"):
        generate_static.generate_static_files(crawl=crawl)
    build_entry.paths.assert_not_called()
    build_entry.settings.assert_not_called()
    build_entry.crawl.assert_not_called()
    build_entry.source.assert_not_called()
    build_entry.images.assert_not_called()


@pytest.mark.parametrize("crawl", ["false", 1, None])
def test_api_rejects_non_boolean_operation(build_entry, crawl):
    with pytest.raises(ConfigurationError, match="crawl must be a boolean"):
        generate_static.generate_static_files(crawl=crawl)
    build_entry.paths.assert_not_called()
    build_entry.settings.assert_not_called()
    build_entry.crawl.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "status"),
    [(["--help"], 0), (["--cra"], 2), (["--unknown"], 2)],
)
def test_cli_help_and_invalid_options_do_not_enter_the_build(
    monkeypatch, arguments, status
):
    build = Mock(side_effect=AssertionError("must not enter the build"))
    monkeypatch.setattr(generate_static, "generate_static_files", build)
    with pytest.raises(SystemExit) as result:
        generate_static.main(arguments)
    assert result.value.code == status
    build.assert_not_called()
