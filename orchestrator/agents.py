"""Agent Engine — Each agent is a Claude API call with a specialized role.

Each agent function:
1. Takes task context as input
2. Makes a Claude API call with role-specific system prompt
3. Returns structured output
4. Tracks token usage and cost
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import anthropic

from orchestrator.settings import settings
from orchestrator.github_client import get_file_content, get_directory_listing, get_tree_paths

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# Load product bible once at import
_product_bible: Optional[str] = None


def get_product_bible() -> str:
    """Load the product bible from disk."""
    global _product_bible
    if _product_bible is None:
        bible_path = Path(__file__).parent.parent / settings.product_bible_path
        _product_bible = bible_path.read_text()
    return _product_bible


def _call_claude(system: str, user: str, max_tokens: int = 8000) -> tuple[str, int, int]:
    """Make a Claude API call. Returns (response_text, input_tokens, output_tokens)."""
    start = time.time()
    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    elapsed = time.time() - start
    text = response.content[0].text
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    # Estimate cost (Sonnet pricing: $3/$15 per M tokens)
    cost_cents = int((input_tokens * 3 + output_tokens * 15) / 10000)

    logger.info(
        f"Claude call: {input_tokens}in/{output_tokens}out tokens, "
        f"~${cost_cents/100:.2f}, {elapsed:.1f}s"
    )
    return text, input_tokens, output_tokens


def _read_relevant_files(file_paths: list[str], branch: Optional[str] = None) -> str:
    """Read multiple files from GitHub and format them for context."""
    parts = []
    for path in file_paths:
        content = get_file_content(path, branch=branch)
        if content:
            parts.append(f"=== {path} ===\n{content}\n")
    return "\n".join(parts)


# ─────────────────────────────────────────────
# PLANNER AGENT
# ─────────────────────────────────────────────

PLANNER_SYSTEM = """You are the Planner Agent for TrendyReports, a real estate SaaS platform.

Your job is to take a task description and produce a clear, actionable implementation plan.

You have access to the Product Bible (the authoritative reference for the product) and can
request to read specific files from the codebase to understand current implementation.

Your output must be a JSON object with this structure:
{
    "summary": "One-sentence summary of what this task does",
    "complexity": "simple|medium|complex",
    "files_to_modify": ["path/to/file1.py", "path/to/file2.tsx"],
    "files_to_create": ["path/to/new_file.py"],
    "files_to_read": ["path/to/existing.py"],  // files coder needs for context
    "steps": [
        {
            "order": 1,
            "description": "What to do",
            "file": "path/to/file.py",
            "action": "modify|create|delete"
        }
    ],
    "risks": ["Potential risk 1", "Potential risk 2"],
    "testing": "How to verify this change works",
    "conventions_to_follow": ["Specific patterns from the codebase to match"]
}

Rules:
- Always check the Product Bible for conventions before planning
- Never plan changes that violate the architecture (3-service split, RLS, etc.)
- Prefer modifying existing files over creating new ones
- Reference specific file paths from the actual codebase
- Keep plans concrete — no vague "refactor" steps
- If the task is unclear, say so in risks and suggest clarification
"""


def run_planner(task: dict) -> tuple[dict, int, int]:
    """Run the planner agent. Returns (plan_dict, input_tokens, output_tokens)."""
    bible = get_product_bible()

    # Get repo structure for context
    api_routes = get_directory_listing("apps/api/src/api/routes")
    api_services = get_directory_listing("apps/api/src/api/services")
    web_pages = get_directory_listing("apps/web/src/app/app") if get_directory_listing("apps/web/src/app/app") else get_directory_listing("apps/web/app/app")
    worker_files = get_directory_listing("apps/worker/src/worker")

    repo_structure = f"""
Available API routes: {json.dumps(api_routes, indent=2)}
Available API services: {json.dumps(api_services, indent=2)}
Available frontend pages: {json.dumps(web_pages, indent=2)}
Available worker files: {json.dumps(worker_files, indent=2)}
"""

    user_prompt = f"""## Product Bible
{bible}

## Repository Structure
{repo_structure}

## Task
Title: {task['title']}
Description: {task['description']}
Context: {task.get('context', 'None')}
Priority: {task['priority']}
Trust Level: {task['trust_level']}

Produce your implementation plan as a JSON object. Only output valid JSON, no markdown fences."""

    response_text, in_tok, out_tok = _call_claude(PLANNER_SYSTEM, user_prompt, max_tokens=4000)

    # Parse JSON from response
    try:
        # Handle case where Claude wraps in markdown
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            clean = clean.rsplit("```", 1)[0]
        plan = json.loads(clean)
    except json.JSONDecodeError:
        plan = {
            "summary": "Failed to parse plan",
            "complexity": "unknown",
            "raw_response": response_text,
            "files_to_modify": [],
            "files_to_create": [],
            "files_to_read": [],
            "steps": [],
            "risks": ["Planner output was not valid JSON"],
            "testing": "",
            "conventions_to_follow": [],
        }

    return plan, in_tok, out_tok


# ─────────────────────────────────────────────
# CODER AGENT
# ─────────────────────────────────────────────

CODER_SYSTEM = """You are the Coder Agent for TrendyReports, a real estate SaaS platform.

Your job is to implement code changes according to a plan. You receive:
1. The implementation plan from the Planner
2. The current content of relevant files
3. The Product Bible for conventions

Your output must be a JSON object with this structure:
{
    "files": [
        {
            "path": "apps/api/src/api/routes/example.py",
            "action": "modify|create|delete",
            "content": "full file content here (for modify/create)",
            "explanation": "what changed and why"
        }
    ],
    "commit_message": "feat: add X to Y",
    "notes": "Any implementation notes for the reviewer"
}

Rules:
- Output COMPLETE file contents (not diffs) — the system will commit the full file
- Follow existing code patterns EXACTLY — match imports, naming, error handling
- Python: snake_case, type hints, docstrings on public functions
- TypeScript: camelCase variables, PascalCase components, Zod for validation
- Never introduce new dependencies without noting it
- Never hardcode secrets or environment-specific values
- Commit messages follow conventional commits: feat:, fix:, refactor:, docs:
- If something in the plan doesn't make sense, note it but implement your best interpretation
"""


def run_coder(task: dict, plan: dict, review_feedback: Optional[str] = None) -> tuple[dict, int, int]:
    """Run the coder agent. Returns (code_output, input_tokens, output_tokens)."""
    bible = get_product_bible()

    # Read files the planner identified
    files_to_read = plan.get("files_to_read", []) + plan.get("files_to_modify", [])
    file_contents = _read_relevant_files(list(set(files_to_read)))

    review_section = ""
    if review_feedback:
        review_section = f"""
## Devil's Advocate Feedback (address these issues)
{review_feedback}
"""

    user_prompt = f"""## Product Bible (key conventions)
{bible}

## Implementation Plan
{json.dumps(plan, indent=2)}

## Current File Contents
{file_contents}
{review_section}

## Task
Title: {task['title']}
Description: {task['description']}

Implement the changes. Output valid JSON only, no markdown fences."""

    response_text, in_tok, out_tok = _call_claude(CODER_SYSTEM, user_prompt, max_tokens=16000)

    try:
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            clean = clean.rsplit("```", 1)[0]
        code_output = json.loads(clean)
    except json.JSONDecodeError:
        code_output = {
            "files": [],
            "commit_message": "failed to parse coder output",
            "notes": response_text,
        }

    return code_output, in_tok, out_tok


# ─────────────────────────────────────────────
# DEVIL'S ADVOCATE AGENT
# ─────────────────────────────────────────────

DEVILS_ADVOCATE_SYSTEM = """You are the Devil's Advocate Agent for TrendyReports, a real estate SaaS platform.

Your job is to critically review code changes and decide if they should ship. You are the last
line of defense before code goes to production. Be thorough but practical.

You receive:
1. The original task description
2. The implementation plan
3. The actual code changes
4. The Product Bible

Your output must be a JSON object:
{
    "decision": "approve|reject",
    "confidence": 0.0-1.0,
    "issues": [
        {
            "severity": "critical|warning|nitpick",
            "file": "path/to/file.py",
            "description": "what's wrong",
            "suggestion": "how to fix it"
        }
    ],
    "summary": "Overall assessment in 2-3 sentences",
    "product_alignment": "How well this fits the product vision (good/ok/poor)",
    "convention_violations": ["Any coding convention violations found"],
    "security_concerns": ["Any security issues"],
    "missing_tests": "What tests should exist for this change"
}

Rules for decision:
- APPROVE if: no critical issues, code follows conventions, fits the product
- REJECT if: critical bugs, security issues, breaks existing functionality, violates architecture,
  or significantly deviates from the plan without good reason
- Nitpicks alone should NOT cause rejection — note them but approve
- You are NOT looking for perfection. You are looking for "will this break something or
  embarrass the product?"
- This is a web app for real estate agents, not a nuclear reactor. Calibrate accordingly.
- After 3 review cycles, lower your bar — ship it if it's not broken

Critical checks (always verify):
1. Does it respect RLS / multi-tenant isolation?
2. Does it handle errors (not bare except, proper HTTP status codes)?
3. Does it follow the existing file/naming patterns?
4. Will it break the build?
5. Does it match what the task asked for?
"""


def run_devils_advocate(task: dict, plan: dict, code_output: dict, attempt: int = 1) -> tuple[dict, int, int]:
    """Run the devil's advocate. Returns (review, input_tokens, output_tokens)."""
    bible = get_product_bible()

    # Summarize code changes for the reviewer
    changes_summary = []
    for f in code_output.get("files", []):
        changes_summary.append(
            f"### {f['path']} ({f['action']})\n"
            f"Explanation: {f.get('explanation', 'none')}\n"
            f"Content length: {len(f.get('content', ''))} chars\n"
            f"```\n{f.get('content', '')[:3000]}\n```"  # First 3000 chars
        )

    user_prompt = f"""## Product Bible
{bible}

## Original Task
Title: {task['title']}
Description: {task['description']}

## Implementation Plan
{json.dumps(plan, indent=2)}

## Code Changes (Review Attempt #{attempt})
Commit message: {code_output.get('commit_message', 'none')}
Coder notes: {code_output.get('notes', 'none')}

{chr(10).join(changes_summary)}

Review this change. Output valid JSON only, no markdown fences."""

    response_text, in_tok, out_tok = _call_claude(DEVILS_ADVOCATE_SYSTEM, user_prompt, max_tokens=4000)

    try:
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            clean = clean.rsplit("```", 1)[0]
        review = json.loads(clean)
    except json.JSONDecodeError:
        review = {
            "decision": "approve",
            "confidence": 0.5,
            "issues": [],
            "summary": "Failed to parse review — approving with low confidence",
            "raw_response": response_text,
        }

    return review, in_tok, out_tok


# ─────────────────────────────────────────────
# TESTER AGENT
# ─────────────────────────────────────────────

TESTER_SYSTEM = """You are the Tester Agent for TrendyReports.

Your job is to evaluate whether code changes will pass the build and existing tests.
You cannot run tests directly — instead, you analyze the code for likely failures.

Your output must be a JSON object:
{
    "verdict": "pass|fail|warning",
    "checks": [
        {
            "check": "TypeScript compilation",
            "result": "pass|fail|warning",
            "details": "explanation"
        }
    ],
    "suggested_manual_tests": ["things to verify manually if possible"]
}

Checks to perform:
1. TypeScript: Will `tsc --noEmit` pass? Check for type errors, missing imports, wrong types
2. Python: Will the module import cleanly? Check for syntax errors, missing imports
3. Template rendering: If Jinja2 templates changed, will they render without UndefinedError?
4. Database: If SQL/migrations involved, are they safe? Additive only?
5. API contracts: Do request/response shapes match frontend expectations?
"""


def run_tester(task: dict, code_output: dict) -> tuple[dict, int, int]:
    """Run the tester agent. Returns (test_result, input_tokens, output_tokens)."""
    changes_summary = []
    for f in code_output.get("files", []):
        changes_summary.append(f"### {f['path']} ({f['action']})\n```\n{f.get('content', '')}\n```")

    user_prompt = f"""## Task
{task['title']}: {task['description']}

## Code Changes
{chr(10).join(changes_summary)}

Analyze these changes for potential build/test failures. Output valid JSON only."""

    response_text, in_tok, out_tok = _call_claude(TESTER_SYSTEM, user_prompt, max_tokens=3000)

    try:
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            clean = clean.rsplit("```", 1)[0]
        result = json.loads(clean)
    except json.JSONDecodeError:
        result = {"verdict": "warning", "checks": [], "raw_response": response_text}

    return result, in_tok, out_tok
