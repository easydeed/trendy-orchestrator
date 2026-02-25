-- ============================================================
-- NUCLEAR FIX: Drop and recreate all agent tables
-- Run this once to guarantee schema matches code exactly.
-- This will delete any existing test tasks (that's fine).
-- ============================================================

DROP TABLE IF EXISTS agent_daily_summaries CASCADE;
DROP TABLE IF EXISTS agent_task_events CASCADE;
DROP TABLE IF EXISTS agent_tasks CASCADE;

-- ── Task Queue ──
CREATE TABLE agent_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    context TEXT DEFAULT '',
    trust_level TEXT NOT NULL DEFAULT 'full_auto',
    priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'queued',
    plan TEXT,
    code_diff TEXT,
    review_notes TEXT,
    review_attempts INTEGER DEFAULT 0,
    test_output TEXT,
    error_message TEXT,
    branch_name TEXT,
    pr_url TEXT,
    preview_url TEXT,
    commit_sha TEXT,
    files_changed JSONB DEFAULT '[]'::jsonb,
    agent_log JSONB DEFAULT '[]'::jsonb,
    estimated_complexity TEXT,
    actual_duration_seconds INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- ── Event Log ──
CREATE TABLE agent_task_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    input_summary TEXT DEFAULT '',
    output_summary TEXT DEFAULT '',
    tokens_used INTEGER DEFAULT 0,
    cost_cents INTEGER DEFAULT 0,
    duration_seconds INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Daily Summaries ──
CREATE TABLE agent_daily_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    summary_date DATE NOT NULL UNIQUE,
    tasks_completed INTEGER DEFAULT 0,
    tasks_failed INTEGER DEFAULT 0,
    total_cost_cents INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    prs_merged INTEGER DEFAULT 0,
    summary_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Indexes ──
CREATE INDEX idx_agent_tasks_status ON agent_tasks(status);
CREATE INDEX idx_agent_tasks_created ON agent_tasks(created_at DESC);
CREATE INDEX idx_agent_task_events_task ON agent_task_events(task_id);
