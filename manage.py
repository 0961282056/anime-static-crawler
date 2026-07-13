"""Local and CI validation commands."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from services.data_repository import DataQualityPolicy, DataRepository
from services.notifier import (
    DiscordNotifier,
    build_workflow_notification,
    workflow_outcome_from_environment,
)
from services.settings import ProjectPaths


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise RuntimeError(f"Directory does not exist: {root}")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def validate_data(paths: ProjectPaths) -> None:
    repository = DataRepository(paths.data_dir, DataQualityPolicy())
    validated = repository.validate_all()
    if not validated:
        raise RuntimeError(f"No quarterly JSON files found in {paths.data_dir}")
    available = repository.discover_available_data()
    record_count = sum(len(repository.load_path(path).anime_list) for path in validated)
    print(
        f"Validated {len(validated)} quarterly files, "
        f"{record_count} records, {len(available)} years"
    )


def verify_dist(paths: ProjectPaths) -> None:
    source_hashes = _tree_hashes(paths.static_source_dir)
    output_hashes = _tree_hashes(paths.static_output_dir)
    if source_hashes != output_hashes:
        missing = sorted(set(source_hashes) - set(output_hashes))
        unexpected = sorted(set(output_hashes) - set(source_hashes))
        changed = sorted(
            path
            for path in set(source_hashes) & set(output_hashes)
            if source_hashes[path] != output_hashes[path]
        )
        raise RuntimeError(
            "dist/static is not an exact build of static. "
            f"missing={missing}, unexpected={unexpected}, changed={changed}"
        )
    if not (paths.output_dir / "index.html").is_file():
        raise RuntimeError("dist/index.html is missing")
    if paths.cloudflare_headers_file.is_file():
        built_headers = paths.output_dir / "_headers"
        expected_headers = paths.cloudflare_headers_file.read_text(
            encoding="utf-8"
        ).encode("utf-8")
        if not built_headers.is_file() or (
            built_headers.read_bytes() != expected_headers
        ):
            raise RuntimeError("dist/_headers does not match the source _headers")
    print(f"Verified deterministic static output: {len(source_hashes)} assets")


def quality_report(paths: ProjectPaths) -> None:
    repository = DataRepository(paths.data_dir, DataQualityPolicy())
    rows: list[tuple[str, int, int, int, int, str]] = []
    for path in repository.validate_all(allow_legacy=True):
        dataset = repository.load_path(path)
        records = dataset.anime_list
        rows.append(
            (
                path.stem,
                len(records),
                sum(record.bangumi_id == "未知ID" for record in records),
                sum(record.story == "暫無簡介" for record in records),
                sum(record.premiere_date == "無首播日期" for record in records),
                dataset.generated_at.isoformat(),
            )
        )
    print("## Anime data quality")
    print()
    print(f"- Quarterly files: {len(rows)}")
    print(f"- Total records: {sum(row[1] for row in rows)}")
    print(f"- Legacy unknown IDs: {sum(row[2] for row in rows)}")
    print(f"- Missing stories: {sum(row[3] for row in rows)}")
    print(f"- Missing broadcast dates: {sum(row[4] for row in rows)}")
    print()
    print("| Quarter | Records | Unknown ID | Missing story | Missing date | Updated |")
    print("|---|---:|---:|---:|---:|---|")
    for quarter, count, unknown, stories, dates, updated in rows[-8:]:
        print(f"| {quarter} | {count} | {unknown} | {stories} | {dates} | {updated} |")


def notify_workflow() -> None:
    outcome = workflow_outcome_from_environment(os.environ)
    notification = build_workflow_notification(outcome)
    DiscordNotifier(
        os.getenv("DISCORD_WEBHOOK_URL"),
        required=True,
    ).send(notification)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8", newline="\n") as summary:
            summary.write("## Discord workflow notification\n\n")
            summary.write(f"- Status: `{notification.status}`\n")
            summary.write(f"- Data changed: `{str(notification.changed).lower()}`\n")
            summary.write(f"- Records: `{notification.count}`\n")
            summary.write(f"- Parse failures: `{notification.parse_failures}`\n")
            summary.write(f"- Detail: {notification.message}\n")
    print(f"Discord workflow notification sent: {notification.status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project validation commands")
    parser.add_argument(
        "command",
        choices=(
            "validate-data",
            "verify-dist",
            "validate-all",
            "quality-report",
            "notify-workflow",
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "notify-workflow":
        notify_workflow()
        return 0

    paths = ProjectPaths.from_environment()
    if args.command in {"validate-data", "validate-all"}:
        validate_data(paths)
    if args.command in {"verify-dist", "validate-all"}:
        verify_dist(paths)
    if args.command == "quality-report":
        quality_report(paths)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
