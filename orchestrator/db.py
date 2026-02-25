"""Database operations for the agent task queue."""

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from orchestrator.settings import settings


@contextmanager
def db_conn():
    """Open a database connection with dict row factory."""
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def create_task(
    title: str,
    description: str,
    context: str = "",
    trust_level: str = "full_auto",
    priority: str = "medium",
) -> dict:
    """Insert a new task into the queue."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_tasks (title, description, context, trust_level, priority)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (title, description, context, trust_level, priority),
            )
            task = cur.fetchone()
            conn.commit()
            return dict(task)


def get_next_task() -> Optional[dict]:
    """Get the next queued task, prioritized by priority then age.
    Uses SELECT FOR UPDATE SKIP LOCKED to prevent double-processing."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM agent_tasks
                WHERE status = 'queued'
                ORDER BY
                    CASE priority
                        WHEN 'urgent' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 3
                    END,
                    created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            task = cur.fetchone()
            if task:
                cur.execute(
                    """
                    UPDATE agent_tasks
                    SET status = 'planning', started_at = now()
                    WHERE id = %s
                    """,
                    (task["id"],),
                )
                conn.commit()
                return dict(task)
            return None


def update_task(task_id: uuid.UUID, **kwargs) -> dict:
    """Update task fields."""
    if not kwargs:
        return get_task(task_id)

    set_clauses = []
    values = []
    for key, value in kwargs.items():
        if key in ("agent_log", "files_changed") and isinstance(value, (list, dict)):
            set_clauses.append(f"{key} = %s::jsonb")
            values.append(json.dumps(value, default=str))
        else:
            set_clauses.append(f"{key} = %s")
            values.append(value)

    values.append(str(task_id))

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE agent_tasks
                SET {', '.join(set_clauses)}
                WHERE id = %s
                RETURNING *
                """,
                values,
            )
            task = cur.fetchone()
            conn.commit()
            return dict(task) if task else {}


def get_task(task_id: uuid.UUID) -> Optional[dict]:
    """Get a single task by ID."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM agent_tasks WHERE id = %s", (str(task_id),))
            task = cur.fetchone()
            return dict(task) if task else None


def log_event(
    task_id: uuid.UUID,
    agent_name: str,
    event_type: str,
    input_summary: str = "",
    output_summary: str = "",
    tokens_used: int = 0,
    cost_cents: int = 0,
    duration_seconds: int = 0,
):
    """Log an agent event."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_task_events
                (task_id, agent_name, event_type, input_summary, output_summary,
                 tokens_used, cost_cents, duration_seconds)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(task_id),
                    agent_name,
                    event_type,
                    input_summary,
                    output_summary,
                    tokens_used,
                    cost_cents,
                    duration_seconds,
                ),
            )
            conn.commit()


def get_daily_stats() -> dict:
    """Get today's task statistics."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'done') as completed,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    COUNT(*) FILTER (WHERE status = 'queued') as queued,
                    COUNT(*) FILTER (WHERE status NOT IN ('done', 'failed', 'queued')) as in_progress,
                    COALESCE(SUM(actual_duration_seconds) FILTER (WHERE status = 'done'), 0) as total_seconds
                FROM agent_tasks
                WHERE created_at >= CURRENT_DATE
                """
            )
            return dict(cur.fetchone())


def get_daily_cost() -> int:
    """Get today's total cost in cents."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(cost_cents), 0) as total
                FROM agent_task_events
                WHERE created_at >= CURRENT_DATE
                """
            )
            return cur.fetchone()["total"]
