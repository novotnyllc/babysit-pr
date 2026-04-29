import argparse
import importlib.util
import tempfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("gh_pr_watch.py")
MODULE_SPEC = importlib.util.spec_from_file_location("gh_pr_watch", MODULE_PATH)
gh_pr_watch = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(gh_pr_watch)


def sample_pr():
    return {
        "number": 123,
        "url": "https://github.com/openai/codex/pull/123",
        "repo": "openai/codex",
        "head_sha": "abc123",
        "head_branch": "feature",
        "state": "OPEN",
        "merged": False,
        "closed": False,
        "mergeable": "MERGEABLE",
        "merge_state_status": "CLEAN",
        "review_decision": "",
    }


def sample_checks(**overrides):
    checks = {
        "pending_count": 0,
        "failed_count": 0,
        "passed_count": 12,
        "all_terminal": True,
    }
    checks.update(overrides)
    return checks


def test_collect_snapshot_fetches_review_items_before_ci(monkeypatch, tmp_path):
    call_order = []
    pr = sample_pr()

    monkeypatch.setattr(gh_pr_watch, "resolve_pr", lambda *args, **kwargs: pr)
    monkeypatch.setattr(gh_pr_watch, "load_state", lambda path: ({}, True))
    monkeypatch.setattr(
        gh_pr_watch,
        "get_authenticated_login",
        lambda: call_order.append("auth") or "octocat",
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "fetch_new_review_items",
        lambda *args, **kwargs: call_order.append("review") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "get_pr_checks",
        lambda *args, **kwargs: call_order.append("checks") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "summarize_checks",
        lambda checks: call_order.append("summarize") or sample_checks(),
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "get_workflow_runs_for_sha",
        lambda *args, **kwargs: call_order.append("workflow") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "failed_runs_from_workflow_runs",
        lambda *args, **kwargs: call_order.append("failed_runs") or [],
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "recommend_actions",
        lambda *args, **kwargs: call_order.append("recommend") or ["idle"],
    )
    monkeypatch.setattr(gh_pr_watch, "save_state", lambda *args, **kwargs: None)

    args = argparse.Namespace(
        pr="123",
        repo=None,
        state_file=str(tmp_path / "watcher-state.json"),
        max_flaky_retries=3,
    )

    gh_pr_watch.collect_snapshot(args)

    assert call_order.index("review") < call_order.index("checks")
    assert call_order.index("review") < call_order.index("workflow")


def test_recommend_actions_prioritizes_review_comments():
    actions = gh_pr_watch.recommend_actions(
        sample_pr(),
        sample_checks(failed_count=1),
        [{"run_id": 99}],
        [{"kind": "review_comment", "id": "1"}],
        0,
        3,
    )

    assert actions == [
        "process_review_comment",
        "diagnose_ci_failure",
        "retry_failed_checks",
    ]


def test_actionable_review_bot_login_allows_copilot_without_bot_suffix():
    assert gh_pr_watch.is_actionable_review_bot_login("copilot-pull-request-reviewer")


def test_actionable_review_bot_login_allows_known_review_automation_accounts():
    assert gh_pr_watch.is_actionable_review_bot_login("bugbot[bot]")
    assert gh_pr_watch.is_actionable_review_bot_login("claude[bot]")
    assert gh_pr_watch.is_actionable_review_bot_login("coderabbitai")
    assert gh_pr_watch.is_actionable_review_bot_login("cursor[bot]")
    assert gh_pr_watch.is_actionable_review_bot_login("gemini-code-assist[bot]")
    assert gh_pr_watch.is_actionable_review_bot_login("sourcery-ai[bot]")


def test_fetch_new_review_items_surfaces_copilot_review_with_none_association(monkeypatch):
    pr = sample_pr()
    review_payload = [
        {
            "id": 456,
            "user": {"login": "copilot-pull-request-reviewer"},
            "author_association": "NONE",
            "submitted_at": "2026-04-23T14:00:00Z",
            "body": "This looks actionable.",
            "html_url": "https://github.com/openai/codex/pull/123#pullrequestreview-456",
        }
    ]

    def fake_list_paginated(endpoint, repo=None, per_page=100):
        assert repo == pr["repo"]
        if endpoint.endswith("/reviews"):
            return review_payload
        return []

    monkeypatch.setattr(gh_pr_watch, "gh_api_list_paginated", fake_list_paginated)

    state = {
        "seen_issue_comment_ids": [],
        "seen_review_comment_ids": [],
        "seen_review_ids": [],
    }
    new_items = gh_pr_watch.fetch_new_review_items(
        pr,
        state,
        fresh_state=True,
        authenticated_login="octocat",
    )

    assert [item["id"] for item in new_items] == ["456"]
    assert state["seen_review_ids"] == ["456"]


def test_fetch_new_review_items_ignores_untrusted_non_allowlisted_automation(monkeypatch):
    pr = sample_pr()
    review_payload = [
        {
            "id": 789,
            "user": {"login": "random-reviewer-service"},
            "author_association": "NONE",
            "submitted_at": "2026-04-23T14:00:00Z",
            "body": "Untrusted automation.",
            "html_url": "https://github.com/openai/codex/pull/123#pullrequestreview-789",
        }
    ]

    def fake_list_paginated(endpoint, repo=None, per_page=100):
        if endpoint.endswith("/reviews"):
            return review_payload
        return []

    monkeypatch.setattr(gh_pr_watch, "gh_api_list_paginated", fake_list_paginated)

    state = {
        "seen_issue_comment_ids": [],
        "seen_review_comment_ids": [],
        "seen_review_ids": [],
    }
    new_items = gh_pr_watch.fetch_new_review_items(
        pr,
        state,
        fresh_state=True,
        authenticated_login="octocat",
    )

    assert new_items == []
    assert state["seen_review_ids"] == []


def test_run_watch_keeps_polling_open_ready_to_merge_pr(monkeypatch):
    sleeps = []
    events = []
    snapshot = {
        "pr": sample_pr(),
        "checks": sample_checks(),
        "failed_runs": [],
        "new_review_items": [],
        "actions": ["ready_to_merge"],
        "retry_state": {
            "current_sha_retries_used": 0,
            "max_flaky_retries": 3,
        },
    }

    monkeypatch.setattr(
        gh_pr_watch,
        "collect_snapshot",
        lambda args: (snapshot, Path(tempfile.gettempdir()) / "codex-babysit-pr-state.json"),
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "print_event",
        lambda event, payload: events.append((event, payload)),
    )

    class StopWatch(Exception):
        pass

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise StopWatch

    monkeypatch.setattr(gh_pr_watch.time, "sleep", fake_sleep)

    with pytest.raises(StopWatch):
        gh_pr_watch.run_watch(argparse.Namespace(poll_seconds=30))

    assert sleeps == [30, 30]
    assert [event for event, _ in events] == ["snapshot", "snapshot"]
