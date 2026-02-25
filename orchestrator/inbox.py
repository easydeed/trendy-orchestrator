"""Inbox watcher — checks for tasks committed to the orchestrator repo via GitHub.

How it works:
1. Claude (or anyone) commits a JSON array of tasks to `tasks/inbox.json` in the
   trendy-orchestrator repo.
2. Each poll cycle, the worker checks for that file.
3. If tasks are found, they're inserted into the DB queue.
4. The file is cleared (replaced with an empty array) so tasks aren't re-queued.

This lets Claude queue tasks directly from any conversation by committing to GitHub,
bypassing network restrictions that block direct access to the intake server.
"""

import json
import logging
from typing import Optional

from github import Github, GithubException
from github.Repository import Repository

from orchestrator import db
from orchestrator.settings import settings

logger = logging.getLogger(__name__)

INBOX_PATH = "tasks/inbox.json"

_orch_repo: Optional[Repository] = None


def _get_orchestrator_repo() -> Repository:
    """Get the orchestrator repo (not the TrendyReports codebase repo)."""
    global _orch_repo
    if _orch_repo is None:
        gh = Github(settings.github_token)
        _orch_repo = gh.get_repo(settings.github_orchestrator_repo)
    return _orch_repo


def check_inbox() -> int:
    """Check for tasks in the inbox file. Returns number of tasks queued."""
    if not settings.github_token:
        return 0

    try:
        repo = _get_orchestrator_repo()
        try:
            content = repo.get_contents(INBOX_PATH, ref="main")
        except GithubException as e:
            if e.status == 404:
                return 0  # No inbox file yet — that's fine
            raise

        # Parse the inbox
        raw = content.decoded_content.decode("utf-8").strip()
        if not raw or raw == "[]":
            return 0

        try:
            tasks = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in {INBOX_PATH}, skipping")
            return 0

        if not isinstance(tasks, list) or len(tasks) == 0:
            return 0

        # Queue each task
        queued = 0
        for t in tasks:
            if not isinstance(t, dict) or not t.get("title"):
                logger.warning(f"Skipping invalid task entry: {t}")
                continue

            db.create_task(
                title=t["title"],
                description=t.get("description", ""),
                context=t.get("context", ""),
                trust_level=t.get("trust_level", "full_auto"),
                priority=t.get("priority", "medium"),
            )
            logger.info(f"Inbox: queued '{t['title']}'")
            queued += 1

        # Clear the inbox by replacing with empty array
        repo.update_file(
            path=INBOX_PATH,
            message="chore: clear inbox (tasks queued)",
            content="[]",
            sha=content.sha,
            branch="main",
        )
        logger.info(f"Inbox: cleared {queued} tasks from {INBOX_PATH}")

        return queued

    except Exception as e:
        logger.warning(f"Inbox check failed (non-fatal): {e}")
        return 0
