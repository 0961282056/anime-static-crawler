"""Readable, redacted failure reports shared by local commands and CI."""

from __future__ import annotations

import html
import logging
import os
import re
import sys
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from services.errors import (
    ConfigurationError,
    DataContractError,
    ImageStoreError,
    ItemParseError,
    NotificationError,
    QuotaExceededError,
    SelectorCanaryError,
    SourceFetchError,
    SourceNotFoundError,
)

logger = logging.getLogger(__name__)
STAGE_LABELS = {
    "configuration": "讀取執行設定",
    "crawl": "爬取季度資料",
    "source": "取得來源網頁",
    "images": "檢查或儲存封面",
    "parse": "解析動畫資料",
    "data-write": "驗證與寫入季度資料",
    "cache-save": "儲存圖片快取",
    "data-validation": "驗證所有季度資料",
    "static-assets": "更新網站靜態檔案",
    "render": "產生網站首頁",
    "summary": "寫入執行摘要",
    "validate-all": "驗證網站與資料",
    "validate-data": "驗證季度資料",
    "verify-dist": "核對網站輸出",
    "quality-report": "產生資料品質報告",
    "notify-workflow": "送出排程通知",
    "selector-canary": "檢查來源解析規則",
    "notify-selector-canary-failure": "送出來源檢查通知",
    "publish": "建立及合併資料更新 PR",
    "preflight": "執行爬取前品質檢查",
    "dependencies": "安裝鎖定依賴",
    "artifact": "保存或下載驗證後資料",
    "data-changes": "檢查資料更新範圍",
    "build": "執行網站建置",
}

# These codes and guidance are safe to forward to notifications. Raw exception
# messages, file paths and secrets stay out of cross-job outputs.
ERROR_GUIDANCE = {
    "CONFIGURATION": (
        "執行設定無效或缺少必要值",
        "確認訊息中的環境變數名稱、格式及允許範圍，再重新執行。",
    ),
    "SOURCE_NOT_FOUND": (
        "來源季度網頁不存在",
        "確認季度網址與來源網站；未來季度尚未公開可等待，歷史季度缺失需先調查。",
    ),
    "SOURCE_FETCH": (
        "無法取得來源網頁",
        "查看 HTTP 狀態或網路原因，確認來源服務、DNS 與連線；暫時性故障可稍後重試。",
    ),
    "SOURCE_PARSE": (
        "來源網頁不符合解析規則",
        "比對來源 HTML 與 parser，修復後先執行 selector-canary；不要放寬資料品質門檻。",
    ),
    "IMAGE_QUOTA": (
        "圖片服務配額已達安全門檻",
        "先查看 Cloudinary 用量；如需清理，先審查 retention dry-run，不會自動刪圖。",
    ),
    "IMAGE_STORE": (
        "封面下載或圖片服務失敗",
        "查看封面主機、HTTP 狀態及 Cloudinary 權限或用量；確認原因後再重試。",
    ),
    "DATA_CONTRACT": (
        "資料格式或品質檢查未通過",
        "查看檔案或季度、欄位及筆數差異；確認來源與解析結果後修復，避免用不完整資料覆蓋舊檔。",
    ),
    "NOTIFICATION": (
        "通知未能送達",
        "確認 DISCORD_WEBHOOK_URL 設定、網路及 HTTP 狀態；通知故障不等於爬蟲資料故障。",
    ),
    "FILE_PERMISSION": (
        "檔案存取遭到拒絕",
        "檢查指定路徑的寫入權限及檔案占用；Windows 可先關閉使用該輸出目錄的程式，再重試。",
    ),
    "FILE_IO": (
        "檔案讀寫失敗",
        "查看指定路徑、磁碟可用空間及檔案是否存在；先保留舊資料與備份再處理。",
    ),
    "UNEXPECTED": (
        "程式發生未預期錯誤",
        "依執行階段與例外類型查看技術詳細資料，修復後重新執行相關測試。",
    ),
    "PIPELINE": (
        "工作流程步驟未完成",
        "開啟執行連結，查看標示失敗的步驟及第一個錯誤；確認原因後重新執行。",
    ),
    "PR_HEAD_CHANGED": (
        "PR 提交版本已改變",
        "重新取得 main 與 PR 最新版本，重新產生及驗證資料，避免合併過時的結果。",
    ),
    "PR_CLOSED": (
        "資料更新 PR 已關閉但未合併",
        "查看 PR 討論與關閉原因，處理問題後重新執行資料更新流程。",
    ),
    "PR_CHECK_FAILED": (
        "PR 必要檢查失敗或取消",
        "開啟失敗檢查連結，修復第一個錯誤；不要繞過 Quality Gate。",
    ),
    "PR_TIMEOUT": (
        "PR 合併等待逾時",
        "查看 PR 檢查、分支保護與 API 狀態；本次發布未確認完成。",
    ),
}


def redact(value: object) -> str:
    text = str(value)
    secrets = sorted(
        {
            item.strip()
            for name, item in os.environ.items()
            if re.search(r"SECRET|TOKEN|PASSWORD|WEBHOOK|PRIVATE_KEY|API_KEY", name)
            and item.strip()
        },
        key=len,
        reverse=True,
    )
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[REDACTED]@", text)
    text = re.sub(r"(https?://[^\s?#]+)\?[^\s]+", r"\1?[REDACTED]", text)
    text = re.sub(r"(/api/webhooks/)[^\s]+", r"\1[REDACTED]", text)
    # Control characters cannot create new workflow commands/log lines.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = redact(super().format(record))
        return re.sub(r"(?m)^::", "[log] ::", rendered)


@contextmanager
def operation(
    stage: str, *, log_success: bool = True, **context: object
) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        # The innermost failing operation identifies the useful failure stage.
        if not hasattr(exc, "operation_stage"):
            exc.operation_stage = stage
        existing = getattr(exc, "operation_context", {})
        exc.operation_context = {**context, **existing}
        raise
    else:
        if log_success:
            logger.info(
                "步驟完成：%s (%s)，耗時 %.2f 秒",
                STAGE_LABELS.get(stage, stage),
                stage,
                time.monotonic() - started,
            )


@dataclass(frozen=True)
class FailureReport:
    code: str
    stage: str
    error_type: str
    detail: str
    context: str


def describe_failure(exc: Exception, default_stage: str) -> FailureReport:
    categories = (
        (ConfigurationError, "CONFIGURATION"),
        (SourceNotFoundError, "SOURCE_NOT_FOUND"),
        (SourceFetchError, "SOURCE_FETCH"),
        ((ItemParseError, SelectorCanaryError), "SOURCE_PARSE"),
        (QuotaExceededError, "IMAGE_QUOTA"),
        (ImageStoreError, "IMAGE_STORE"),
        (DataContractError, "DATA_CONTRACT"),
        (NotificationError, "NOTIFICATION"),
        (PermissionError, "FILE_PERMISSION"),
        (OSError, "FILE_IO"),
    )
    code = next(
        (code for types, code in categories if isinstance(exc, types)), "UNEXPECTED"
    )
    if isinstance(exc.__cause__, PermissionError):
        code = "FILE_PERMISSION"
    override = getattr(exc, "failure_code", "")
    if override in ERROR_GUIDANCE:
        code = override
    context = getattr(exc, "operation_context", {})
    return FailureReport(
        code=code,
        stage=getattr(exc, "operation_stage", default_stage),
        error_type=type(exc).__name__,
        detail=redact(
            str(exc)
            + "".join(f"\n附加資訊：{note}" for note in getattr(exc, "__notes__", ()))
        )[:4000],
        context=redact("; ".join(f"{key}={value}" for key, value in context.items()))[
            :2000
        ],
    )


def report_failure(exc: Exception, default_stage: str) -> None:
    report = describe_failure(exc, default_stage)
    title, hint = ERROR_GUIDANCE[report.code]
    stage_label = STAGE_LABELS.get(report.stage, report.stage)
    message = (
        f"執行失敗 [{report.code}] {title}\n"
        f"階段：{stage_label} ({report.stage})\n"
        f"類型：{report.error_type}\n"
        f"原因：{report.detail}\n"
        + (f"位置：{report.context}\n" if report.context else "")
        + f"處理建議：{hint}"
    )
    message = re.sub(r"(?m)^::", "[detail] ::", message)
    print(message, file=sys.stderr)
    if report.code == "UNEXPECTED" or os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
        technical = redact("".join(traceback.format_exception(exc)))
        technical = re.sub(r"(?m)^::", "[detail] ::", technical)
        print("技術詳細資料：\n" + technical, file=sys.stderr)

    if os.getenv("GITHUB_ACTIONS") == "true":
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{escaped}")

    sinks = {
        "GITHUB_OUTPUT": f"failure_code={report.code}\nfailure_stage={report.stage}\n",
        "GITHUB_STEP_SUMMARY": (
            f"## 執行失敗\n\n<pre>{html.escape(message)}</pre>\n\n"
        ),
    }
    for name, content in sinks.items():
        destination = os.getenv(name, "").strip()
        if not destination:
            continue
        try:
            with Path(destination).open("a", encoding="utf-8", newline="\n") as output:
                output.write(content)
        except OSError as sink_error:
            print(f"附加診斷寫入失敗 ({name})：{redact(sink_error)}", file=sys.stderr)


def run_command(command: Callable[[], object], default_stage: str) -> int:
    try:
        with operation(default_stage):
            command()
        return 0
    except Exception as exc:
        report_failure(exc, default_stage)
        return 1


def publish_failure(code: str, detail: str) -> int:
    failure = RuntimeError(detail)
    failure.failure_code = code
    report_failure(failure, "publish")
    return 1
