"""Local and CI validation commands."""

from __future__ import annotations

import argparse
import hashlib
import os
from datetime import datetime
from pathlib import Path

from services.data_repository import DataQualityPolicy, DataRepository
from services.diagnostics import operation, run_command
from services.notifier import (
    DiscordNotifier,
    build_selector_canary_failure_notification,
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
    from services.release_manifest import validate_release

    validate_release(paths)
    if (paths.output_dir / "search-index.json").exists():
        from services.search_index import validate_search_index

        validate_search_index(
            paths, DataRepository(paths.data_dir, DataQualityPolicy())
        )
    print(f"Verified deterministic static output: {len(source_hashes)} assets")


def quality_report(paths: ProjectPaths) -> None:
    from services.episode_refresh import refresh_statistics
    from services.episode_source_reviews import reviewed_source

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
    all_records = [
        record
        for path in repository.validate_all(allow_legacy=True)
        for record in repository.load_path(path).anime_list
    ]
    print(
        f"- Episode records: {sum(record.episodes is not None for record in all_records)}"
    )
    print(
        f"- Unlinked Bangumi subjects: {sum(record.external_ids.bangumi is None for record in all_records)}"
    )
    print(
        f"- Episode updates stale: {sum(record.episodes is not None and record.episodes.fetch_state == 'stale' for record in all_records)}"
    )
    print(
        f"- Episode conflicts: {sum(record.episodes is not None and record.episodes.fetch_state == 'conflict' for record in all_records)}"
    )
    episode_records = [record for record in all_records if record.episodes is not None]
    snapshots = [
        record
        for record in episode_records
        if (review := reviewed_source(record))
        and review["mode"] == "snapshot"
        and not review.get("official_adapter")
    ]
    now = datetime.now().astimezone()
    health = refresh_statistics(
        [
            (path.stem, record)
            for path in repository.validate_all(allow_legacy=True)
            for record in repository.load_path(path).anime_list
        ],
        now,
    )
    print(
        f"- Episode airing status unknown: {sum(record.episodes.airing_status == 'unknown' for record in episode_records)}"
    )
    print(
        f"- Episode total unknown: {sum(record.episodes.total_episodes is None for record in episode_records)}"
    )
    print(
        f"- Episode progress unknown: {sum(record.episodes.aired_episodes is None for record in episode_records)}"
    )
    print(
        f"- Episode refresh overdue (unique identities): {health['priority_overdue'] + health['background_overdue']}"
    )
    print(f"- Priority episode identities: {health['priority_identities']}")
    print(f"- Priority refresh overdue: {health['priority_overdue']}")
    print(f"- Background refresh overdue: {health['background_overdue']}")
    print(
        f"- Priority verification older than 48h: {health['priority_older_than_48h']}"
    )
    print(f"- Priority verification missing: {health['priority_unverified']}")
    print(
        f"- Oldest priority verification: {health['priority_oldest_checked_at'] or 'none'}"
    )
    print(
        f"- Oldest automatic-source verification: {health['oldest_checked_at'] or 'none'}"
    )
    print(f"- Manual episode snapshot records (not auto-refreshed): {len(snapshots)}")
    print(
        f"- Unfinished manual snapshots needing manual review: {sum(record.episodes.airing_status != 'finished' for record in snapshots)}"
    )
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


def selector_canary() -> None:
    from services.selector_canary import run_selector_canary

    result = run_selector_canary()
    message = (
        f"Selector canary passed: {result.year} {result.season}, "
        f"{result.card_count} cards, {result.source_url}"
    )
    print(message)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8", newline="\n") as summary:
            summary.write("## Source selector canary\n\n")
            summary.write(f"- Quarter: `{result.year} {result.season}`\n")
            summary.write(f"- Parsed cards: `{result.card_count}`\n")
            summary.write(f"- Source: {result.source_url}\n")
            summary.write("- Side effects: `none`\n")


def notify_selector_canary_failure() -> None:
    notification = build_selector_canary_failure_notification(
        run_url=os.getenv("RUN_URL", "").strip(),
        event_name=os.getenv("EVENT_NAME", "").strip(),
        run_attempt=os.getenv("RUN_ATTEMPT", "1").strip() or "1",
    )
    DiscordNotifier(
        os.getenv("DISCORD_WEBHOOK_URL"),
        required=True,
    ).send(notification)
    print("Discord selector canary failure notification sent")


def parse_args() -> argparse.Namespace:
    commands = {
        "validate-data": "離線讀取：驗證季度 JSON。",
        "verify-dist": "離線讀取：比對建置資產、搜尋索引與發布清單。",
        "validate-all": "離線讀取：依序執行 validate-data 與 verify-dist。",
        "quality-report": "離線讀取：列出資料缺漏、查證狀態與刷新到期情況。",
        "quality-evidence": (
            "離線＋本機寫入：讀取資料與 Git 歷史，寫入證據及品質趨勢報告；可追加工作摘要。"
        ),
        "build-frontend": "離線＋本機寫入：重新產生 static/js/main.js。",
        "source-audit": ("連線查證＋本機寫入：核對來源並寫入報告；可追加工作摘要。"),
        "notify-source-reviews": (
            "GitHub 寫入：依報告建立／更新複查 Issue、持久待辦及留言；可追加工作摘要。"
        ),
        "remind-source-reviews": (
            "GitHub 寫入：查詢到期待查，可能重開 Issue 並留言提醒；可追加工作摘要。"
        ),
        "notify-workflow": "Discord 通知：傳送工作流程摘要；可追加工作摘要。",
        "production-report": "離線＋本機寫入：整合正式站驗收與來源更新待辦，不連線查證。",
        "notify-production": "Discord 通知：傳送同一提交的正式站驗收與來源更新待辦。",
        "selector-canary": "連線查證：抓取當季頁面檢查解析；可追加工作摘要。",
        "notify-selector-canary-failure": "Discord 通知：傳送來源選擇器失敗通知。",
    }
    effects = "\n".join(
        f"  {command}\n    {description}" for command, description in commands.items()
    )
    parser = argparse.ArgumentParser(
        description="專案管理指令：資料驗證、報告、前端生成、來源查證與通知。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"各指令的影響：\n{effects}\n\n"
            "查證／報告指令不修改公開季度資料；本機報告與外部通知分開列明。\n"
            "工作摘要僅在設定 GITHUB_STEP_SUMMARY 時追加本機檔案。\n"
            "所有命令失敗診斷也可能追加設定的 GITHUB_OUTPUT／GITHUB_STEP_SUMMARY。\n"
            "--help 只顯示說明，不執行任何管理指令。"
        ),
    )
    parser.add_argument(
        "command",
        choices=commands,
        metavar="COMMAND",
        help="要執行的管理指令，名稱與影響見下方。",
    )
    return parser.parse_args()


def execute_command(command: str) -> None:
    if command == "production-report":
        from services.production_delivery import write_delivery_report

        report = write_delivery_report(
            ProjectPaths.from_environment(),
            os.environ["EXPECTED_COMMIT"],
            os.environ["VERIFY_RESULT"],
        )
        print(f"Production delivery report: {report['status']}")
        if report["status"] == "failed":
            raise ValueError("Production delivery is incomplete; see delivery.json")
        return
    if command == "notify-production":
        from services.production_delivery import (
            build_production_notification,
            read_report,
        )

        paths = ProjectPaths.from_environment()
        notification = build_production_notification(
            read_report(paths.root / "audit-output/delivery.json"),
            os.environ["EXPECTED_COMMIT"],
            os.environ["RUN_URL"],
        )
        DiscordNotifier(os.getenv("DISCORD_WEBHOOK_URL"), required=True).send(
            notification
        )
        print(f"Discord production notification sent: {notification.status}")
        return
    if command == "build-frontend":
        from services.frontend_bundle import build_frontend_bundle

        build_frontend_bundle(ProjectPaths.from_environment().root)
        print("Built static/js/main.js from frontend/modules.json")
        return
    if command == "remind-source-reviews":
        from services.review_queue import remind_from_environment

        result = remind_from_environment()
        print(result)
        if path := os.getenv("GITHUB_STEP_SUMMARY", "").strip():
            with Path(path).open("a", encoding="utf-8") as summary:
                summary.write(f"## 待查日期提醒\n\n{result}\n")
        return
    if command == "notify-source-reviews":
        from services.review_notifications import publish_from_environment

        result = publish_from_environment(
            os.getenv("SOURCE_REVIEW_REPORT", "audit-output/source-audit.json")
        )
        print(result)
        summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(f"## 人工複查通知\n\n{result}\n")
        return
    if command == "source-audit":
        from services.errors import SourceFetchError
        from services.source_audit import run_source_audit, write_audit_report

        paths = ProjectPaths.from_environment()
        output = Path(os.getenv("SOURCE_AUDIT_OUTPUT", "audit-output"))
        # Audit artifacts must never overwrite public quarterly data.
        if output.resolve().is_relative_to(paths.output_dir.resolve()):
            raise ValueError(
                "SOURCE_AUDIT_OUTPUT must be outside the public output directory"
            )
        report = run_source_audit(paths.data_dir)
        content = write_audit_report(report, output)
        print(content)
        summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(content)
        if report["errors"]:
            raise SourceFetchError(
                "Source audit incomplete; see saved report for failed sources"
            )
        return
    if command == "quality-evidence":
        from services.quality_evidence import write_quality_evidence

        paths = ProjectPaths.from_environment()
        output = Path(os.getenv("QUALITY_EVIDENCE_OUTPUT", "audit-output/quality"))
        if output.resolve().is_relative_to(paths.output_dir.resolve()):
            raise ValueError("QUALITY_EVIDENCE_OUTPUT must be outside public output")
        content = write_quality_evidence(
            paths.root,
            paths.data_dir,
            output,
            history_limit=int(os.getenv("QUALITY_HISTORY_LIMIT", "120")),
        )
        print(content)
        summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(content)
        return
    if command == "notify-workflow":
        notify_workflow()
        return
    if command == "selector-canary":
        selector_canary()
        return
    if command == "notify-selector-canary-failure":
        notify_selector_canary_failure()
        return

    with operation("configuration"):
        paths = ProjectPaths.from_environment()
    if command in {"validate-data", "validate-all"}:
        validate_data(paths)
    if command in {"verify-dist", "validate-all"}:
        verify_dist(paths)
    if command == "quality-report":
        quality_report(paths)


def main() -> int:
    args = parse_args()
    return run_command(lambda: execute_command(args.command), args.command)


if __name__ == "__main__":
    raise SystemExit(main())
