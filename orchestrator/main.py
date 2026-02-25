"""Main Orchestrator — polls the task queue and runs the agent pipeline.

Usage:
    python -m orchestrator.main              # Run forever (poll loop)
    python -m orchestrator.main --once       # Process one task and exit
    python -m orchestrator.main --task UUID  # Process a specific task
"""

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone

from orchestrator import db
from orchestrator.agents import (
    run_planner,
    run_coder,
    run_devils_advocate,
    run_tester,
)
from orchestrator.github_client import (
    create_branch,
    commit_file,
    create_pull_request,
    delete_file,
)
from orchestrator.inbox import check_inbox
from orchestrator.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("orchestrator")


def _append_log(task: dict, entry: dict) -> list:
    """Append to the task's agent_log."""
    log = task.get("agent_log") or []
    if isinstance(log, str):
        log = json.loads(log)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    log.append(entry)
    return log


def _check_budget() -> bool:
    """Check if we're within daily budget."""
    spent = db.get_daily_cost()
    if spent >= settings.daily_budget_cents:
        logger.warning(
            f"Daily budget exhausted: ${spent/100:.2f} / ${settings.daily_budget_cents/100:.2f}"
        )
        return False
    return True


def process_task(task: dict) -> bool:
    """Run the full agent pipeline on a task. Returns True if successful."""
    task_id = uuid.UUID(str(task["id"]))
    logger.info(f"═══ Processing task: {task['title']} ({task_id}) ═══")
    pipeline_start = time.time()

    try:
        # ── PHASE 1: PLANNING ──
        logger.info("Phase 1: Planning...")
        db.update_task(task_id, status="planning")
        db.log_event(task_id, "planner", "started", input_summary=task["description"])

        plan, plan_in, plan_out = run_planner(task)

        db.update_task(
            task_id,
            plan=json.dumps(plan, indent=2),
        )
        task["agent_log"] = _append_log(task, {
            "agent": "planner",
            "action": "plan_created",
            "complexity": plan.get("complexity"),
            "files_to_modify": plan.get("files_to_modify", []),
            "files_to_create": plan.get("files_to_create", []),
        })
        db.update_task(task_id, agent_log=task["agent_log"])
        db.log_event(
            task_id, "planner", "completed",
            output_summary=plan.get("summary", ""),
            tokens_used=plan_in + plan_out,
            cost_cents=int((plan_in * 3 + plan_out * 15) / 10000),
        )

        # Check for unclear tasks
        if plan.get("complexity") == "unknown" or not plan.get("steps"):
            logger.warning("Planner couldn't produce a clear plan. Marking as failed.")
            db.update_task(
                task_id,
                status="failed",
                error_message="Planner could not produce a clear plan. Task may be too vague.",
            )
            return False

        # ── PHASE 2: CODING (with review loop) ──
        branch_name = f"agent/{task_id.hex[:8]}-{task['title'].lower().replace(' ', '-')[:30]}"
        create_branch(branch_name)
        db.update_task(task_id, branch_name=branch_name, status="coding")

        review_feedback = None
        code_output = None
        approved = False

        for attempt in range(1, settings.max_review_cycles + 1):
            if not _check_budget():
                db.update_task(task_id, status="failed", error_message="Daily budget exhausted")
                return False

            # ── CODER ──
            logger.info(f"Phase 2: Coding (attempt {attempt})...")
            db.log_event(task_id, "coder", "started", input_summary=f"Attempt {attempt}")

            code_output, code_in, code_out = run_coder(task, plan, review_feedback)

            db.update_task(task_id, code_diff=json.dumps(code_output, indent=2))
            task["agent_log"] = _append_log(task, {
                "agent": "coder",
                "action": "code_written",
                "attempt": attempt,
                "files_count": len(code_output.get("files", [])),
                "commit_message": code_output.get("commit_message", ""),
            })
            db.update_task(task_id, agent_log=task["agent_log"])
            db.log_event(
                task_id, "coder", "completed",
                output_summary=code_output.get("commit_message", ""),
                tokens_used=code_in + code_out,
                cost_cents=int((code_in * 3 + code_out * 15) / 10000),
            )

            # ── DEVIL'S ADVOCATE ──
            logger.info(f"Phase 3: Devil's Advocate review (attempt {attempt})...")
            db.update_task(task_id, status="reviewing", review_attempts=attempt)
            db.log_event(task_id, "devils_advocate", "started", input_summary=f"Review attempt {attempt}")

            review, rev_in, rev_out = run_devils_advocate(task, plan, code_output, attempt)

            db.update_task(task_id, review_notes=json.dumps(review, indent=2))
            task["agent_log"] = _append_log(task, {
                "agent": "devils_advocate",
                "action": "review_complete",
                "attempt": attempt,
                "decision": review.get("decision"),
                "confidence": review.get("confidence"),
                "issues_count": len(review.get("issues", [])),
            })
            db.update_task(task_id, agent_log=task["agent_log"])
            db.log_event(
                task_id, "devils_advocate",
                "approved" if review.get("decision") == "approve" else "rejected",
                output_summary=review.get("summary", ""),
                tokens_used=rev_in + rev_out,
                cost_cents=int((rev_in * 3 + rev_out * 15) / 10000),
            )

            if review.get("decision") == "approve":
                approved = True
                logger.info(f"✅ Devil's Advocate approved (attempt {attempt}, confidence {review.get('confidence', '?')})")
                break
            else:
                critical_issues = [
                    i for i in review.get("issues", [])
                    if i.get("severity") == "critical"
                ]
                logger.info(
                    f"❌ Devil's Advocate rejected: {len(critical_issues)} critical issues. "
                    f"Sending back to coder..."
                )
                review_feedback = json.dumps(review.get("issues", []), indent=2)

        if not approved:
            logger.warning("Max review cycles exhausted. Marking as failed.")
            db.update_task(
                task_id,
                status="failed",
                error_message=f"Failed devil's advocate review after {settings.max_review_cycles} attempts",
            )
            return False

        # ── PHASE 4: TESTING ──
        logger.info("Phase 4: Testing...")
        db.update_task(task_id, status="testing")
        db.log_event(task_id, "tester", "started")

        test_result, test_in, test_out = run_tester(task, code_output)

        db.update_task(task_id, test_output=json.dumps(test_result, indent=2))
        db.log_event(
            task_id, "tester",
            "completed" if test_result.get("verdict") != "fail" else "failed",
            output_summary=test_result.get("verdict", "unknown"),
            tokens_used=test_in + test_out,
            cost_cents=int((test_in * 3 + test_out * 15) / 10000),
        )

        if test_result.get("verdict") == "fail":
            logger.warning("Tester flagged failures. Marking as failed.")
            db.update_task(
                task_id,
                status="failed",
                error_message="Tester identified likely build/test failures",
            )
            return False

        # ── PHASE 5: DEPLOY (commit to branch + PR) ──
        logger.info("Phase 5: Deploying...")
        db.update_task(task_id, status="deploying")

        files_changed = []
        last_sha = None
        for file_change in code_output.get("files", []):
            path = file_change["path"]
            action = file_change["action"]
            content = file_change.get("content", "")

            if action == "delete":
                last_sha = delete_file(path, f"chore: delete {path}", branch_name)
            else:
                last_sha = commit_file(
                    path=path,
                    content=content,
                    message=code_output.get("commit_message", f"feat: {task['title']}"),
                    branch=branch_name,
                )
            files_changed.append(path)

        db.update_task(task_id, commit_sha=last_sha, files_changed=files_changed)

        # Create PR
        trust = task.get("trust_level", "full_auto")
        auto_merge = trust == "full_auto"

        pr_body = f"""## Task
{task['title']}

## Description
{task['description']}

## Plan
{plan.get('summary', 'No summary')}

## Files Changed
{chr(10).join(f'- `{f}`' for f in files_changed)}

## Review
- Devil's Advocate: **Approved** (confidence: {review.get('confidence', '?')})
- Review attempts: {attempt}
- Tester: **{test_result.get('verdict', 'unknown')}**

---
*Automated by TrendyReports Agent Orchestrator*
"""

        pr_info = create_pull_request(
            branch=branch_name,
            title=f"[agent] {code_output.get('commit_message', task['title'])}",
            body=pr_body,
            auto_merge=auto_merge,
        )

        db.update_task(task_id, pr_url=pr_info["url"])

        task["agent_log"] = _append_log(task, {
            "agent": "deployer",
            "action": "pr_created",
            "pr_number": pr_info["number"],
            "pr_url": pr_info["url"],
            "auto_merged": pr_info.get("merged", False),
        })
        db.update_task(task_id, agent_log=task["agent_log"])
        db.log_event(task_id, "deployer", "completed", output_summary=pr_info["url"])

        # ── DONE ──
        elapsed = int(time.time() - pipeline_start)
        db.update_task(
            task_id,
            status="done",
            completed_at=datetime.now(timezone.utc).isoformat(),
            actual_duration_seconds=elapsed,
        )

        logger.info(
            f"═══ Task complete: {task['title']} ═══\n"
            f"    PR: {pr_info['url']}\n"
            f"    Auto-merged: {pr_info.get('merged', False)}\n"
            f"    Duration: {elapsed}s\n"
            f"    Files: {len(files_changed)}"
        )
        return True

    except Exception as e:
        logger.exception(f"Task failed with exception: {e}")
        db.update_task(
            task_id,
            status="failed",
            error_message=str(e),
            completed_at=datetime.now(timezone.utc).isoformat(),
            actual_duration_seconds=int(time.time() - pipeline_start),
        )
        db.log_event(task_id, "orchestrator", "failed", output_summary=str(e))
        return False


def run_once():
    """Process the next queued task."""
    if not _check_budget():
        logger.info("Daily budget exhausted. Sleeping.")
        return False

    task = db.get_next_task()
    if not task:
        return False

    return process_task(task)


def run_specific(task_id: str):
    """Process a specific task by ID."""
    task = db.get_task(uuid.UUID(task_id))
    if not task:
        logger.error(f"Task {task_id} not found")
        return False

    # Reset status if re-running
    db.update_task(uuid.UUID(task_id), status="queued")
    task["status"] = "queued"
    db.update_task(uuid.UUID(task_id), status="planning", started_at=datetime.now(timezone.utc).isoformat())

    return process_task(task)


def main():
    parser = argparse.ArgumentParser(description="TrendyReports Agent Orchestrator")
    parser.add_argument("--once", action="store_true", help="Process one task and exit")
    parser.add_argument("--task", type=str, help="Process a specific task ID")
    args = parser.parse_args()

    if args.task:
        success = run_specific(args.task)
        sys.exit(0 if success else 1)

    if args.once:
        success = run_once()
        sys.exit(0 if success else 1)

    # Continuous poll loop
    logger.info(
        f"Starting orchestrator (poll every {settings.orchestrator_poll_interval_seconds}s, "
        f"budget ${settings.daily_budget_cents/100:.2f}/day)"
    )

    while True:
        try:
            # Check GitHub inbox for tasks committed by Claude
            try:
                inbox_count = check_inbox()
                if inbox_count:
                    logger.info(f"Loaded {inbox_count} tasks from GitHub inbox")
            except Exception as e:
                logger.warning(f"Inbox check error (non-fatal): {e}")

            task_processed = run_once()
            if task_processed:
                # Immediately check for more work
                continue
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.exception(f"Orchestrator loop error: {e}")

        time.sleep(settings.orchestrator_poll_interval_seconds)


if __name__ == "__main__":
    main()
