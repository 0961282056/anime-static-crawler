from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / ".github/scripts/check_pr_merge.py"
SPEC = importlib.util.spec_from_file_location("check_pr_merge", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
poll = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(poll)
HEAD = "a" * 40
URL = "https://github.com/owner/repo/pull/1"


def mock_responses(monkeypatch: pytest.MonkeyPatch, responses: list[object]) -> None:
    values = iter(responses)
    monkeypatch.setattr(poll, "gh_json", lambda arguments: next(values))


@pytest.mark.parametrize("state,expected", [("MERGED", 0), ("CLOSED", 1)])
def test_terminal_pr_states(monkeypatch, state, expected):
    mock_responses(monkeypatch, [{"state": state, "headRefOid": HEAD}])
    assert poll.inspect_pr(URL, HEAD) == expected


def test_different_head_is_rejected_even_if_merged(monkeypatch):
    mock_responses(monkeypatch, [{"state": "MERGED", "headRefOid": "b" * 40}])
    assert poll.inspect_pr(URL, HEAD) == 1


@pytest.mark.parametrize("bucket", ["fail", "cancel"])
def test_required_failure_is_reported_without_waiting(monkeypatch, capsys, bucket):
    info = {"state": "OPEN", "headRefOid": HEAD}
    mock_responses(
        monkeypatch,
        [
            info,
            [{"name": "quality", "bucket": bucket, "link": "https://example.test"}],
            info,
        ],
    )
    assert poll.inspect_pr(URL, HEAD) == 1
    captured = capsys.readouterr()
    assert "quality" in captured.err
    assert "PR_CHECK_FAILED" in captured.err


@pytest.mark.parametrize(
    "checks",
    [None, [], [{"bucket": "pending"}], [{"bucket": "pass"}], [{"bucket": "skipping"}]],
)
def test_missing_pending_or_passing_checks_never_count_as_merged(monkeypatch, checks):
    mock_responses(monkeypatch, [{"state": "OPEN", "headRefOid": HEAD}, checks])
    assert poll.inspect_pr(URL, HEAD) == 2


def test_api_failure_during_failure_recheck_keeps_waiting(monkeypatch):
    mock_responses(
        monkeypatch, [{"state": "OPEN", "headRefOid": HEAD}, [{"bucket": "fail"}], None]
    )
    assert poll.inspect_pr(URL, HEAD) == 2


def test_merge_during_failure_recheck_is_success(monkeypatch):
    mock_responses(
        monkeypatch,
        [
            {"state": "OPEN", "headRefOid": HEAD},
            [{"bucket": "fail"}],
            {"state": "MERGED", "headRefOid": HEAD},
        ],
    )
    assert poll.inspect_pr(URL, HEAD) == 0


def test_changed_head_during_failure_recheck_is_rejected(monkeypatch):
    mock_responses(
        monkeypatch,
        [
            {"state": "OPEN", "headRefOid": HEAD},
            [{"bucket": "fail"}],
            {"state": "OPEN", "headRefOid": "b" * 40},
        ],
    )
    assert poll.inspect_pr(URL, HEAD) == 1


@pytest.mark.parametrize("info", [None, {}, {"headRefOid": HEAD, "state": "UNKNOWN"}])
def test_missing_or_unknown_status_keeps_waiting(monkeypatch, info):
    mock_responses(monkeypatch, [info])
    assert poll.inspect_pr(URL, HEAD) == 2


@pytest.mark.parametrize("returncode", [0, 1, 8])
def test_gh_nonzero_check_status_still_parses_json(monkeypatch, returncode):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode, json.dumps([{"bucket": "pending"}]), ""
        ),
    )
    assert poll.gh_json(["checks", URL]) == [{"bucket": "pending"}]


@pytest.mark.parametrize("stdout,code", [("not JSON", 1), ("{}", 4), ("", 1)])
def test_cli_or_json_errors_keep_waiting(monkeypatch, stdout, code):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, code, stdout, ""),
    )
    assert poll.gh_json(["checks", URL]) is None


def test_cli_timeout_is_bounded(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 20
        raise subprocess.TimeoutExpired("gh", 20)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert poll.gh_json(["checks", URL]) is None
