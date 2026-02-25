-- Migration: Create agent orchestrator tables
-- Run against your existing TrendyReports PostgreSQL database

-- Task queue table
CREATE TABLE IF NOT EXISTS agent_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    context TEXT DEFAULT '',                -- extra notes, links, references
    trust_level TEXT NOT NULL DEFAULT 'full_auto',  -- full_auto | preview_only | plan_only
    priority TEXT NOT NULL DEFAULT 'medium',         -- low | medium | high | urgent
    status TEXT NOT NULL DEFAULT 'queued',            -- queued | planning | coding | reviewing | testing | deploying | done | failed | rejected
    
    -- Agent execution tracking
    plan TEXT,                              -- planner agent output
    code_diff TEXT,                         -- coder agent output (patch/diff)
    review_notes TEXT,                      -- devil's advocate feedback
    review_attempts INTEGER DEFAULT 0,      -- how many DA review cycles
    test_output TEXT,                       -- tester agent output
    error_message TEXT,                     -- if failed, why
    
    -- Deployment tracking
    branch_name TEXT,                       -- git branch created
    pr_url TEXT,                            -- GitHub PR URL
    preview_url TEXT,                       -- Vercel preview URL
    commit_sha TEXT,                        -- final commit SHA
    
    -- Metadata
    files_changed JSONB DEFAULT '[]'::jsonb,   -- array of file paths touched
    agent_log JSONB DEFAULT '[]'::jsonb,       -- chronological log of all agent actions
    estimated_complexity TEXT,                   -- simple | medium | complex (set by planner)
    actual_duration_seconds INTEGER,            -- how long the full pipeline took
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Index for polling queued tasks
CREATE INDEX idx_agent_tasks_status ON agent_tasks(status, priority, created_at);

-- Index for reporting
CREATE INDEX idx_agent_tasks_created ON agent_tasks(created_at DESC);

-- Task execution history (detailed log per agent action)
CREATE TABLE IF NOT EXISTS agent_task_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,          -- planner | coder | devils_advocate | tester | deployer | reporter
    event_type TEXT NOT NULL,          -- started | completed | failed | rejected | approved | retrying
    input_summary TEXT,                -- what was passed to this agent
    output_summary TEXT,               -- what the agent produced
    tokens_used INTEGER DEFAULT 0,     -- API token usage tracking
    cost_cents INTEGER DEFAULT 0,      -- estimated cost in cents
    duration_seconds INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_task_events_task ON agent_task_events(task_id, created_at);

-- Daily summary table (for morning reports)
CREATE TABLE IF NOT EXISTS agent_daily_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    summary_date DATE NOT NULL UNIQUE,
    tasks_completed INTEGER DEFAULT 0,
    tasks_failed INTEGER DEFAULT 0,
    tasks_queued INTEGER DEFAULT 0,
    total_tokens_used INTEGER DEFAULT 0,
    total_cost_cents INTEGER DEFAULT 0,
    summary_text TEXT,                 -- human-readable summary
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_agent_tasks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER agent_tasks_updated_at
    BEFORE UPDATE ON agent_tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_agent_tasks_updated_at();
