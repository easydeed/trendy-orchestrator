# TrendyReports Agent Orchestrator

Autonomous AI development pipeline for TrendyReports. Submit tasks from your phone, agents build and ship code while you sleep.

## How It Works

```
You (phone) → Task Intake Form
    → Planner Agent (breaks down the task)
        → Coder Agent (implements changes)
            → Devil's Advocate Agent (reviews code)
                → [Loop if rejected, max 3x]
                    → Tester Agent (validates build)
                        → Deployer (commits + PR + auto-merge)
                            → Task marked done
```

## Architecture

- **Orchestrator** — Python service running on Render, polls task queue every 30s
- **Intake Server** — Simple HTTP server with mobile-friendly form
- **Task Queue** — PostgreSQL table in your existing TrendyReports database
- **Agents** — Claude API calls with specialized system prompts
- **GitHub** — Agents commit to branches, create PRs, auto-merge when trust is full

## Quick Start

### 1. Run the migration

```bash
psql $DATABASE_URL -f migrations/001_create_agent_tasks.sql
```

### 2. Set up environment

```bash
cp .env.example .env
# Edit .env with your actual values:
# - ANTHROPIC_API_KEY
# - DATABASE_URL (same as your TrendyReports DB)
# - GITHUB_TOKEN (Personal Access Token with repo scope)
# - GITHUB_REPO (owner/repo)
# - INTAKE_SECRET (random string for auth)
```

### 3. Install dependencies

```bash
pip install poetry
poetry install
```

### 4. Test locally

```bash
# Terminal 1: Start the intake server
python -m orchestrator.intake_server

# Terminal 2: Start the orchestrator
python -m orchestrator.main

# Terminal 3: Submit a test task
curl -X POST http://localhost:8080/task \
  -H "Authorization: Bearer your-intake-secret" \
  -H "Content-Type: application/json" \
  -d '{"title": "Add tooltip to dashboard metric cards", "description": "Each metric card on the dashboard should show a tooltip on hover explaining what the metric means", "priority": "low"}'
```

### 5. Deploy to Render

- Create a new **Background Worker** for the orchestrator
- Create a new **Web Service** for the intake server
- Set env vars from render.yaml
- Both services use the same DATABASE_URL as your TrendyReports API

### 6. Bookmark on your phone

Open `https://your-intake-server.onrender.com` on your phone. Enter your INTAKE_SECRET once (it saves to localStorage). Bookmark it. That's your task submission interface.

## Task Trust Levels

| Level | What happens |
|-------|-------------|
| `full_auto` | Agent builds, reviews, tests, merges to main. You wake up to shipped code. |
| `preview_only` | Agent builds and creates PR but does NOT merge. You review the PR. |
| `plan_only` | Agent creates a plan only. No code written. You review the plan. |

## Cost

Each task costs roughly $0.15–$0.50 in Claude API calls depending on complexity. With the default $15/day budget cap, that's 30-100 tasks per day.

## Files

```
trendy-orchestrator/
├── PRODUCT_BIBLE.md              # Product reference for all agents
├── orchestrator/
│   ├── main.py                   # Orchestrator loop + pipeline
│   ├── agents.py                 # Agent definitions (planner, coder, DA, tester)
│   ├── db.py                     # Task queue database operations
│   ├── github_client.py          # GitHub branch/commit/PR operations
│   ├── intake_server.py          # HTTP task submission server
│   └── settings.py               # Configuration
├── migrations/
│   └── 001_create_agent_tasks.sql
├── render.yaml                   # Render deployment config
├── pyproject.toml                # Python dependencies
└── .env.example                  # Environment template
```
