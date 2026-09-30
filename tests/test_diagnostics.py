from __future__ import annotations

import logging

import pytest

from services.diagnostics import (
    RedactingFormatter,
    describe_failure,
    operation,
    report_failure,
    run_command,
)
from services.errors import (
    ConfigurationError,
    DataContractError,
    ImageStoreError,
    NotificationError,
    QuotaExceededError,
    SourceFetchError,
    SourceNotFoundError,
)


@pytest.mark.parametrize(
    "error,code",
    [
        (ConfigurationError("bad setting"), "CONFIGURATION"),
        (SourceNotFoundError("404"), "SOURCE_NOT_FOUND"),
        (SourceFetchError("503"), "SOURCE_FETCH"),
        (QuotaExceededError("90%"), "IMAGE_QUOTA"),
        (ImageStoreError("offline"), "IMAGE_STORE"),
        (DataContractError("invalid records"), "DATA_CONTRACT"),
        (NotificationError("HTTP 500"), "NOTIFICATION"),
        (PermissionError("denied"), "FILE_PERMISSION"),
        (OSError("disk full"), "FILE_IO"),
        (RuntimeError("unexpected"), "UNEXPECTED"),
    ],
)
def test_failures_have_distinct_machine_readable_codes(error, code):
    assert describe_failure(error, "crawl").code == code


def test_wrapped_permission_error_still_identifies_file_access():
    try:
        raise PermissionError("data.json denied")
    except PermissionError as cause:
        error = DataContractError("Unable to read data.json")
        error.__cause__ = cause
    assert describe_failure(error, "data-validation").code == "FILE_PERMISSION"


def test_failure_keeps_innermost_stage_and_outer_quarter_context():
    with (
        pytest.raises(ImageStoreError) as caught,
        operation("crawl", year="2026", season="秋"),
        operation("images", anime="測試動畫"),
    ):
        raise ImageStoreError("HTTP 503")
    report = describe_failure(caught.value, "build")
    assert report.stage == "images"
    assert "2026" in report.context and "秋" in report.context
    assert "測試動畫" in report.context


def test_failure_report_redacts_secrets_and_escapes_ci_output(
    monkeypatch, tmp_path, capsys
):
    secret = "secret-api-0123456789"
    monkeypatch.setenv("CLOUDINARY_API_SECRET", secret)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output = tmp_path / "output.txt"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    error = ImageStoreError(
        f"HTTP 503 {secret}\n::warning::fake\n<script>bad</script> "
        "https://user:password@host.example/image?signature=private "
        "https://discord.example/api/webhooks/123/token"
    )
    report_failure(error, "images")
    captured = capsys.readouterr()
    rendered = captured.out + captured.err + summary.read_text(encoding="utf-8")
    for forbidden in (secret, "signature=private", "user:password", "/123/token"):
        assert forbidden not in rendered
    assert "處理建議" in captured.err
    assert "[detail] ::warning::fake" in captured.err
    assert captured.out.count("\n") == 1
    assert "%0A" in captured.out
    assert "<script>" not in summary.read_text(encoding="utf-8")
    assert (
        output.read_text(encoding="utf-8")
        == "failure_code=IMAGE_STORE\nfailure_stage=images\n"
    )


def test_diagnostic_sink_failure_does_not_replace_original(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "missing" / "output.txt"))
    report_failure(PermissionError("primary denied"), "static-assets")
    captured = capsys.readouterr()
    assert "FILE_PERMISSION" in captured.err
    assert "primary denied" in captured.err
    assert "附加診斷寫入失敗" in captured.err


def test_command_exit_code_and_unexpected_traceback_are_preserved(capsys):
    def fail():
        raise RuntimeError("unexpected failure")

    assert run_command(fail, "render") == 1
    captured = capsys.readouterr()
    assert "技術詳細資料" in captured.err
    assert "Traceback" in captured.err
    assert run_command(lambda: None, "render") == 0


def test_secondary_failure_is_visible_without_losing_primary(capsys):
    error = ImageStoreError("primary image failure")
    error.add_note("Cache persistence also failed: disk full")
    report_failure(error, "images")
    captured = capsys.readouterr()
    assert "primary image failure" in captured.err
    assert "Cache persistence also failed" in captured.err
    assert "IMAGE_STORE" in captured.err


def test_logging_redacts_exception_traceback_and_workflow_command(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN", "token-123456789")
    try:
        raise RuntimeError("token-123456789\n::error::fake")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failed %s",
            ("token-123456789",),
            sys.exc_info(),
        )
    rendered = RedactingFormatter("%(message)s").format(record)
    assert "token-123456789" not in rendered
    assert "[log] ::error::fake" in rendered


def test_known_failure_does_not_dump_traceback_by_default(monkeypatch, capsys):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    report_failure(
        ConfigurationError("REQUEST_TIMEOUT_SECONDS must be between 1 and 120"),
        "configuration",
    )
    assert "Traceback" not in capsys.readouterr().err


def test_cli_validation_failure_has_readable_stage_and_nonzero_exit(
    monkeypatch, capsys
):
    import manage

    monkeypatch.setattr(
        manage, "parse_args", lambda: type("Args", (), {"command": "verify-dist"})()
    )

    def fail(paths):
        raise PermissionError("dist/static access denied")

    monkeypatch.setattr(manage, "verify_dist", fail)
    assert manage.main() == 1
    message = capsys.readouterr().err
    assert "核對網站輸出" in message
    assert "FILE_PERMISSION" in message
    assert "dist/static" in message
