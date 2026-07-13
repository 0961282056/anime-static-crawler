"""Crawl validated data and build the static Cloudflare Pages output."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sentry_sdk
from dotenv import load_dotenv
from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)

from config import Config
from models import TAIPEI_TZ
from services.atomic_io import atomic_write_text
from services.data_repository import DataQualityPolicy, DataRepository
from services.errors import SourceNotFoundError
from services.settings import CrawlerSettings, ProjectPaths

logger = logging.getLogger(__name__)
START_YEAR_ON_EMPTY = 2018
SEASONS = ("冬", "春", "夏", "秋")


@dataclass(frozen=True)
class CrawlSummary:
    processed_quarters: int
    changed_quarters: int
    total_records: int
    parse_failures: int


def configure_runtime() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if dsn:
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0")),
            environment=os.getenv("APP_ENVIRONMENT", "production"),
            release=os.getenv("BUILD_VERSION") or os.getenv("GITHUB_SHA"),
        )


def get_current_season(month: int) -> str:
    if 1 <= month <= 3:
        return "冬"
    if 4 <= month <= 6:
        return "春"
    if 7 <= month <= 9:
        return "夏"
    return "秋"


def is_future_quarter(year: int, season: str, now: datetime) -> bool:
    return year > now.year or (
        year == now.year and now.month < Config.SEASON_TO_MONTH[season]
    )


def target_quarters(now: datetime, *, full_crawl: bool) -> list[tuple[str, str]]:
    current_index = (now.month - 1) // 3
    absolute_current = now.year * 4 + current_index
    start = START_YEAR_ON_EMPTY * 4 if full_crawl else absolute_current - 4
    end = absolute_current + 1
    return [
        (str(quarter // 4), SEASONS[quarter % 4]) for quarter in range(start, end + 1)
    ]


def _safe_replace_directory(source: Path, destination: Path, output_dir: Path) -> None:
    resolved_destination = destination.resolve()
    resolved_output = output_dir.resolve()
    if (
        resolved_destination == resolved_output
        or not resolved_destination.is_relative_to(resolved_output)
    ):
        raise RuntimeError(
            f"Refusing to replace directory outside output root: {destination}"
        )
    backup = destination.with_name(f".{destination.name}.backup-{uuid.uuid4().hex}")
    if not backup.resolve().is_relative_to(resolved_output):
        raise RuntimeError(f"Unsafe static backup path: {backup}")

    had_destination = destination.exists()
    if had_destination:
        destination.rename(backup)
    try:
        source.rename(destination)
    except Exception:
        if had_destination and backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


def sync_static_assets(paths: ProjectPaths) -> None:
    if not paths.static_source_dir.is_dir():
        raise FileNotFoundError(
            f"Static source directory does not exist: {paths.static_source_dir}"
        )
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=".static-build-", dir=paths.output_dir)
    )
    try:
        temporary_static = temporary_root / "static"
        shutil.copytree(paths.static_source_dir, temporary_static)
        _safe_replace_directory(
            temporary_static,
            paths.static_output_dir,
            paths.output_dir,
        )
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)

    if paths.cloudflare_headers_file.exists():
        atomic_write_text(
            paths.output_dir / "_headers",
            paths.cloudflare_headers_file.read_text(encoding="utf-8"),
        )


def compute_build_version(paths: ProjectPaths) -> str:
    explicit = os.getenv("BUILD_VERSION", "").strip()
    if explicit:
        return explicit
    digest = hashlib.sha256()
    source_files = sorted(paths.static_source_dir.rglob("*")) + sorted(
        paths.templates_dir.rglob("*.html")
    )
    for path in source_files:
        if path.is_file():
            digest.update(path.relative_to(paths.root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def render_index(
    paths: ProjectPaths,
    repository: DataRepository,
    now: datetime,
) -> Path:
    available_data = repository.discover_available_data()
    if not available_data:
        raise RuntimeError("No valid quarterly data is available for the site build")

    sorted_years = sorted(available_data, key=int, reverse=True)
    default_year = str(now.year)
    default_season = get_current_season(now.month)
    if default_year not in available_data:
        default_year = sorted_years[0]
    if default_season not in available_data[default_year]:
        default_season = available_data[default_year][0]

    environment = Environment(
        loader=FileSystemLoader(paths.templates_dir),
        autoescape=select_autoescape(("html", "xml")),
        undefined=StrictUndefined,
    )
    template = environment.get_template("index.html")
    content = template.render(
        selected_year=default_year,
        selected_season=default_season,
        years=sorted_years,
        available_data=available_data,
        available_data_json=json.dumps(available_data, ensure_ascii=False),
        build_version=compute_build_version(paths),
        sentry_browser_dsn=os.getenv("SENTRY_BROWSER_DSN", "").strip(),
        app_environment=os.getenv("APP_ENVIRONMENT", "production"),
    )
    output_path = paths.output_dir / "index.html"
    atomic_write_text(output_path, content)
    return output_path


def crawl_quarters(
    paths: ProjectPaths,
    repository: DataRepository,
    now: datetime,
) -> CrawlSummary:
    from services.anime_service import AnimeCrawlerService

    crawler = AnimeCrawlerService.from_environment()
    has_existing_data = any(paths.data_dir.glob("*.json"))
    full_crawl = not has_existing_data
    processed_quarters = 0
    changed_quarters = 0
    total_records = 0
    total_parse_failures = 0

    for year, season in target_quarters(now, full_crawl=full_crawl):
        year_number = int(year)
        output_path = repository.quarter_path(year, season)
        historical = not is_future_quarter(year_number, season, now)
        if full_crawl and historical and output_path.exists():
            logger.info("Existing historical quarter retained: %s %s", year, season)
            continue

        try:
            result = crawler.fetch_quarter(year, season)
            write_result = repository.write_quarter(
                year=year,
                season=season,
                records=result.anime_list,
                source_url=result.source_url,
                source_count=result.source_count,
                parse_failure_count=result.parse_failure_count,
            )
            logger.info(
                "%s %s validated: %s records, %s parse failures, changed=%s",
                year,
                season,
                len(result.anime_list),
                result.parse_failure_count,
                write_result.changed,
            )
            processed_quarters += 1
            changed_quarters += int(write_result.changed)
            total_records += len(result.anime_list)
            total_parse_failures += result.parse_failure_count
        except SourceNotFoundError as exc:
            if is_future_quarter(year_number, season, now):
                logger.info("Future quarter not published yet: %s", exc)
                continue
            raise
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise

    return CrawlSummary(
        processed_quarters=processed_quarters,
        changed_quarters=changed_quarters,
        total_records=total_records,
        parse_failures=total_parse_failures,
    )


def write_crawl_summary_outputs(summary: CrawlSummary) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    values = {
        "processed_quarters": summary.processed_quarters,
        "changed_quarters": summary.changed_quarters,
        "record_count": summary.total_records,
        "parse_failures": summary.parse_failures,
    }
    with Path(output_path).open("a", encoding="utf-8", newline="\n") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")


def generate_static_files() -> None:
    load_dotenv()
    configure_runtime()
    paths = ProjectPaths.from_environment()
    build_only = os.getenv("BUILD_ONLY", "false").lower() == "true"
    settings = CrawlerSettings.from_environment() if not build_only else None
    policy = (
        DataQualityPolicy(
            minimum_count_ratio=settings.minimum_count_ratio,
            maximum_parse_failure_ratio=settings.maximum_parse_failure_ratio,
            maximum_fallback_id_ratio=settings.maximum_fallback_id_ratio,
        )
        if settings
        else DataQualityPolicy()
    )
    repository = DataRepository(paths.data_dir, policy)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TAIPEI_TZ)
    crawl_summary: CrawlSummary | None = None

    if build_only:
        logger.info("BUILD_ONLY enabled: validating data and building static output")
    else:
        crawl_summary = crawl_quarters(paths, repository, now)

    validated_paths = repository.validate_all()
    logger.info("Validated %s quarterly JSON files", len(validated_paths))
    sync_static_assets(paths)
    output_path = render_index(paths, repository, now)
    logger.info("Static site generated: %s", output_path)
    if crawl_summary is not None:
        write_crawl_summary_outputs(crawl_summary)


if __name__ == "__main__":
    generate_static_files()
