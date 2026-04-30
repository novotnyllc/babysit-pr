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


def sample_copilot_review(**overrides):
    review = {
        "requester": "@copilot",
        "requested_reviewer": "Copilot",
        "request_attempted": True,
        "request_succeeded": True,
        "request_unavailable": False,
        "request_retryable": False,
        "request_error": None,
        "requested_reviewers_confirmed": True,
        "pending": False,
        "requested_reviewer_logins": [],
    }
    review.update(overrides)
    return review


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
        "get_requested_reviewers",
        lambda *args, **kwargs: call_order.append("requested_reviewers") or {"users": [], "teams": []},
    )
    monkeypatch.setattr(
        gh_pr_watch,
        "request_copilot_review_if_possible",
        lambda *args, **kwargs: call_order.append("copilot") or sample_copilot_review(),
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


def test_get_pr_checks_treats_no_checks_as_empty(monkeypatch):
    def fake_gh_json(args, repo=None):
        raise gh_pr_watch.GhCommandError("stderr: no checks reported on the branch")

    monkeypatch.setattr(gh_pr_watch, "gh_json", fake_gh_json)

    assert gh_pr_watch.get_pr_checks("123", repo="openai/codex") == []


def test_get_requested_reviewers_best_effort_returns_empty_on_api_error(monkeypatch):
    def fake_get_requested_reviewers(repo, pr_number):
        raise gh_pr_watch.GhCommandError("temporary requested reviewers failure")

    monkeypatch.setattr(gh_pr_watch, "get_requested_reviewers", fake_get_requested_reviewers)

    requested_reviewers, error = gh_pr_watch.get_requested_reviewers_best_effort(
        "openai/codex",
        123,
    )

    assert requested_reviewers == {"users": [], "teams": []}
    assert "temporary requested reviewers failure" in error


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


def test_requested_reviewer_logins_extracts_users_only():
    requested_reviewers = {
        "users": [{"login": "Copilot"}, {"login": "octocat"}, {"name": "missing-login"}],
        "teams": [{"slug": "reviewers"}],
    }

    assert gh_pr_watch.requested_reviewer_logins(requested_reviewers) == [
        "Copilot",
        "octocat",
    ]


def test_has_pending_copilot_review_from_requested_reviewers():
    assert gh_pr_watch.has_pending_copilot_review({"users": [{"login": "Copilot"}]})
    assert gh_pr_watch.has_pending_copilot_review(
        {"users": [{"login": "copilot-pull-request-reviewer[bot]"}]}
    )
    assert not gh_pr_watch.has_pending_copilot_review({"users": [{"login": "octocat"}]})


def test_permanent_copilot_request_error_classification():
    assert gh_pr_watch.is_permanent_copilot_request_error("reviewer not found")
    assert gh_pr_watch.is_permanent_copilot_request_error("Could not resolve to a user")
    assert not gh_pr_watch.is_permanent_copilot_request_error("network timeout")


def test_request_copilot_review_records_success_and_pending_reviewer(monkeypatch):
    calls = []
    pr = sample_pr()
    state = {}

    def fake_gh_text(args, repo=None):
        calls.append((args, repo))
        return ""

    monkeypatch.setattr(gh_pr_watch, "gh_text", fake_gh_text)
    monkeypatch.setattr(
        gh_pr_watch,
        "get_requested_reviewers",
        lambda repo, pr_number: {"users": [{"login": "Copilot"}], "teams": []},
    )

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert calls == [
        (["pr", "edit", "123", "--add-reviewer", "@copilot"], "openai/codex")
    ]
    assert status["request_attempted"] is True
    assert status["request_succeeded"] is True
    assert status["request_unavailable"] is False
    assert status["pending"] is True
    assert status["requested_reviewers_confirmed"] is True
    assert state["copilot_review"]["head_sha"] == "abc123"


def test_request_copilot_review_records_confirmed_nonpending_followup(monkeypatch):
    pr = sample_pr()
    state = {}

    monkeypatch.setattr(gh_pr_watch, "gh_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        gh_pr_watch,
        "get_requested_reviewers",
        lambda repo, pr_number: {"users": [], "teams": []},
    )

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert status["request_succeeded"] is True
    assert status["pending"] is False
    assert status["pending_unknown"] is False
    assert status["requested_reviewers_confirmed"] is True


def test_request_copilot_review_tolerates_unavailable_reviewer(monkeypatch):
    pr = sample_pr()
    state = {}

    def fake_gh_text(args, repo=None):
        raise gh_pr_watch.GhCommandError("reviewer not found")

    monkeypatch.setattr(gh_pr_watch, "gh_text", fake_gh_text)

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert status["request_attempted"] is True
    assert status["request_succeeded"] is False
    assert status["request_unavailable"] is True
    assert status["request_retryable"] is False
    assert "reviewer not found" in status["request_error"]
    assert state["copilot_review"]["request_unavailable"] is True


def test_request_copilot_review_allows_retry_after_transient_error(monkeypatch):
    pr = sample_pr()
    state = {}

    monkeypatch.setattr(gh_pr_watch.time, "time", lambda: 1000)

    def fake_gh_text(args, repo=None):
        raise gh_pr_watch.GhCommandError("network timeout")

    monkeypatch.setattr(gh_pr_watch, "gh_text", fake_gh_text)

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert status["request_attempted"] is True
    assert status["request_succeeded"] is False
    assert status["request_unavailable"] is False
    assert status["request_retryable"] is True
    assert "network timeout" in status["request_error"]
    assert state["copilot_review"]["request_attempted"] is False
    assert state["copilot_review"]["request_retryable"] is True
    assert state["copilot_review"]["last_request_attempt_at"] == 1000
    assert state["copilot_review"]["request_retry_after"] == 1300


def test_request_copilot_review_defers_retry_until_retry_after(monkeypatch):
    pr = sample_pr()
    state = {
        "copilot_review": {
            "head_sha": "abc123",
            "request_attempted": False,
            "request_succeeded": False,
            "request_unavailable": False,
            "request_retryable": True,
            "request_error": "network timeout",
            "last_request_attempt_at": 1000,
            "request_retry_after": 1300,
        }
    }

    def fake_gh_text(args, repo=None):
        raise AssertionError("should not retry before request_retry_after")

    monkeypatch.setattr(gh_pr_watch, "gh_text", fake_gh_text)
    monkeypatch.setattr(gh_pr_watch.time, "time", lambda: 1200)

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert status["request_retryable"] is True
    assert status["pending_unknown"] is True


def test_request_copilot_review_retries_after_retry_after(monkeypatch):
    pr = sample_pr()
    state = {
        "copilot_review": {
            "head_sha": "abc123",
            "request_attempted": False,
            "request_succeeded": False,
            "request_unavailable": False,
            "request_retryable": True,
            "request_error": "network timeout",
            "last_request_attempt_at": 1000,
            "request_retry_after": 1300,
        }
    }
    calls = []

    def fake_gh_text(args, repo=None):
        calls.append((args, repo))
        return ""

    monkeypatch.setattr(gh_pr_watch, "gh_text", fake_gh_text)
    monkeypatch.setattr(gh_pr_watch.time, "time", lambda: 1400)
    monkeypatch.setattr(
        gh_pr_watch,
        "get_requested_reviewers",
        lambda repo, pr_number: {"users": [{"login": "Copilot"}], "teams": []},
    )

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert calls == [
        (["pr", "edit", "123", "--add-reviewer", "@copilot"], "openai/codex")
    ]
    assert status["request_succeeded"] is True
    assert status["pending"] is True
    assert status["requested_reviewers_confirmed"] is True


def test_request_copilot_review_tolerates_failed_followup_status(monkeypatch):
    pr = sample_pr()
    state = {}

    monkeypatch.setattr(gh_pr_watch, "gh_text", lambda *args, **kwargs: "")

    def fake_get_requested_reviewers(repo, pr_number):
        raise gh_pr_watch.GhCommandError("temporary API error")

    monkeypatch.setattr(gh_pr_watch, "get_requested_reviewers", fake_get_requested_reviewers)

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert status["request_attempted"] is True
    assert status["request_succeeded"] is True
    assert status["request_unavailable"] is False
    assert status["pending"] is False
    assert status["pending_unknown"] is True
    assert "temporary API error" in status["request_error"]
    assert state["copilot_review"]["request_succeeded"] is True


def test_request_copilot_review_does_not_retry_same_sha(monkeypatch):
    pr = sample_pr()
    state = {
        "copilot_review": {
            "head_sha": "abc123",
            "request_attempted": True,
            "request_succeeded": False,
            "request_unavailable": True,
            "request_error": "not enabled",
        }
    }

    def fake_gh_text(args, repo=None):
        raise AssertionError("should not retry a completed attempt for the same SHA")

    monkeypatch.setattr(gh_pr_watch, "gh_text", fake_gh_text)

    status = gh_pr_watch.request_copilot_review_if_possible(
        pr,
        state,
        {"users": [], "teams": []},
    )

    assert status["request_attempted"] is True
    assert status["request_unavailable"] is True
    assert status["pending"] is False


def test_recommend_actions_waits_for_pending_copilot_review():
    actions = gh_pr_watch.recommend_actions(
        sample_pr(),
        sample_checks(),
        [],
        [],
        0,
        3,
        copilot_review=sample_copilot_review(pending=True),
    )

    assert actions == ["wait_for_copilot_review"]


def test_recommend_actions_waits_for_unknown_copilot_review_status():
    actions = gh_pr_watch.recommend_actions(
        sample_pr(),
        sample_checks(),
        [],
        [],
        0,
        3,
        copilot_review=sample_copilot_review(pending_unknown=True),
    )

    assert actions == ["wait_for_copilot_review"]


def test_pending_copilot_review_blocks_ready_to_merge():
    assert not gh_pr_watch.is_pr_ready_to_merge(
        sample_pr(),
        sample_checks(),
        [],
        copilot_review=sample_copilot_review(pending=True),
    )


def test_unknown_copilot_review_status_blocks_ready_to_merge():
    assert not gh_pr_watch.is_pr_ready_to_merge(
        sample_pr(),
        sample_checks(),
        [],
        copilot_review=sample_copilot_review(pending_unknown=True),
    )


def test_actionable_review_bot_login_allows_copilot_without_bot_suffix():
    assert gh_pr_watch.is_actionable_review_bot_login("Copilot")
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
