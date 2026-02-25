"""Task Intake Server — simple HTTP API for submitting tasks from your phone.

Endpoints:
    POST /task          — Create a new task
    GET  /tasks         — List recent tasks
    GET  /tasks/:id     — Get task detail
    GET  /stats         — Today's stats
    GET  /              — Simple HTML form (mobile-friendly)

Auth: Bearer token (INTAKE_SECRET from .env)

Usage:
    python -m orchestrator.intake_server
"""

import json
import logging
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from orchestrator import db
from orchestrator.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("intake")

# ─── HTML Form (served at GET /) ───

MOBILE_FORM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TrendyReports — New Task</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { 
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 20px; min-height: 100vh;
  }
  .container { max-width: 500px; margin: 0 auto; }
  h1 { font-size: 24px; margin-bottom: 4px; color: #38bdf8; }
  .subtitle { font-size: 14px; color: #94a3b8; margin-bottom: 24px; }
  label { display: block; font-size: 14px; font-weight: 600; margin-bottom: 6px; color: #cbd5e1; }
  input, textarea, select {
    width: 100%; padding: 12px; border: 1px solid #334155; border-radius: 8px;
    background: #1e293b; color: #e2e8f0; font-size: 16px; margin-bottom: 16px;
    -webkit-appearance: none;
  }
  input:focus, textarea:focus, select:focus {
    outline: none; border-color: #38bdf8; box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15);
  }
  textarea { min-height: 120px; resize: vertical; }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  button {
    width: 100%; padding: 14px; border: none; border-radius: 8px;
    background: #38bdf8; color: #0f172a; font-size: 16px; font-weight: 700;
    cursor: pointer; margin-top: 8px;
  }
  button:active { background: #0ea5e9; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .success {
    background: #065f46; border: 1px solid #10b981; border-radius: 8px;
    padding: 16px; margin-bottom: 16px; display: none;
  }
  .success h3 { color: #34d399; margin-bottom: 4px; }
  .error {
    background: #7f1d1d; border: 1px solid #ef4444; border-radius: 8px;
    padding: 16px; margin-bottom: 16px; display: none;
  }
  .stats {
    background: #1e293b; border-radius: 8px; padding: 16px; margin-top: 24px;
    border: 1px solid #334155;
  }
  .stats-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
  .stat-item { text-align: center; }
  .stat-value { font-size: 24px; font-weight: 700; color: #38bdf8; }
  .stat-label { font-size: 12px; color: #94a3b8; }
</style>
</head>
<body>
<div class="container">
  <h1>🤖 New Task</h1>
  <p class="subtitle">TrendyReports Agent Orchestrator</p>

  <div id="success" class="success">
    <h3>✅ Task Queued</h3>
    <p id="success-msg"></p>
  </div>
  <div id="error" class="error">
    <p id="error-msg"></p>
  </div>

  <form id="taskForm">
    <label for="title">What needs to happen?</label>
    <input type="text" id="title" name="title" placeholder="e.g. Add sqft column to CMA table" required>

    <label for="description">Details</label>
    <textarea id="description" name="description" placeholder="Describe what you want built, changed, or fixed..."></textarea>

    <div class="row">
      <div>
        <label for="priority">Priority</label>
        <select id="priority" name="priority">
          <option value="medium" selected>Medium</option>
          <option value="low">Low</option>
          <option value="high">High</option>
          <option value="urgent">Urgent</option>
        </select>
      </div>
      <div>
        <label for="trust_level">Trust Level</label>
        <select id="trust_level" name="trust_level">
          <option value="full_auto" selected>Full Auto (ship it)</option>
          <option value="preview_only">Preview Only</option>
          <option value="plan_only">Plan Only</option>
        </select>
      </div>
    </div>

    <label for="context">Extra Context (optional)</label>
    <textarea id="context" name="context" style="min-height:60px" placeholder="Links, file paths, references..."></textarea>

    <button type="submit" id="submitBtn">🚀 Queue Task</button>
  </form>

  <div class="stats" id="stats"></div>
</div>

<script>
const SECRET = localStorage.getItem('intake_secret') || prompt('Enter intake secret:');
if (SECRET) localStorage.setItem('intake_secret', SECRET);

document.getElementById('taskForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = 'Queuing...';

  try {
    const body = {
      title: document.getElementById('title').value,
      description: document.getElementById('description').value,
      priority: document.getElementById('priority').value,
      trust_level: document.getElementById('trust_level').value,
      context: document.getElementById('context').value,
    };
    const res = await fetch('/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${SECRET}` },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById('success').style.display = 'block';
      document.getElementById('success-msg').textContent = `"${body.title}" queued (${body.priority})`;
      document.getElementById('error').style.display = 'none';
      document.getElementById('taskForm').reset();
    } else {
      throw new Error(data.error || 'Failed');
    }
  } catch (err) {
    document.getElementById('error').style.display = 'block';
    document.getElementById('error-msg').textContent = err.message;
    document.getElementById('success').style.display = 'none';
  }
  btn.disabled = false;
  btn.textContent = '🚀 Queue Task';
});

// Load stats
(async () => {
  try {
    const res = await fetch('/stats', { headers: { 'Authorization': `Bearer ${SECRET}` } });
    const data = await res.json();
    document.getElementById('stats').innerHTML = `
      <div class="stats-grid">
        <div class="stat-item"><div class="stat-value">${data.completed}</div><div class="stat-label">Done Today</div></div>
        <div class="stat-item"><div class="stat-value">${data.queued}</div><div class="stat-label">Queued</div></div>
        <div class="stat-item"><div class="stat-value">${data.in_progress}</div><div class="stat-label">In Progress</div></div>
        <div class="stat-item"><div class="stat-value">${data.failed}</div><div class="stat-label">Failed</div></div>
      </div>`;
  } catch (e) {}
})();
</script>
</body>
</html>"""


class IntakeHandler(BaseHTTPRequestHandler):
    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {settings.intake_secret}":
            return True
        self._respond(401, {"error": "Unauthorized"})
        return False

    def _respond(self, status: int, body, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if isinstance(body, str):
            self.wfile.write(body.encode())
        elif isinstance(body, dict) or isinstance(body, list):
            self.wfile.write(json.dumps(body, default=str).encode())

    def do_OPTIONS(self):
        self._respond(200, "")

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(MOBILE_FORM_HTML.encode())
            return

        if not self._check_auth():
            return

        if parsed.path == "/stats":
            stats = db.get_daily_stats()
            self._respond(200, stats)
            return

        if parsed.path == "/tasks":
            # Simple list of recent tasks
            with db.db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, title, status, priority, created_at, pr_url "
                        "FROM agent_tasks ORDER BY created_at DESC LIMIT 20"
                    )
                    tasks = [dict(r) for r in cur.fetchall()]
            self._respond(200, tasks)
            return

        if parsed.path.startswith("/tasks/"):
            task_id = parsed.path.split("/")[-1]
            try:
                task = db.get_task(uuid.UUID(task_id))
                if task:
                    self._respond(200, task)
                else:
                    self._respond(404, {"error": "Not found"})
            except ValueError:
                self._respond(400, {"error": "Invalid UUID"})
            return

        self._respond(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/task":
            self._respond(404, {"error": "Not found"})
            return

        if not self._check_auth():
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        title = body.get("title", "").strip()
        if not title:
            self._respond(400, {"error": "title is required"})
            return

        task = db.create_task(
            title=title,
            description=body.get("description", ""),
            context=body.get("context", ""),
            trust_level=body.get("trust_level", "full_auto"),
            priority=body.get("priority", "medium"),
        )

        logger.info(f"Task created: {task['id']} — {title}")
        self._respond(201, {"id": str(task["id"]), "title": title, "status": "queued"})

    def log_message(self, format, *args):
        """Suppress default access logs for cleaner output."""
        pass


def main():
    port = settings.intake_port
    server = HTTPServer(("0.0.0.0", port), IntakeHandler)
    logger.info(f"Intake server running on http://0.0.0.0:{port}")
    logger.info(f"Open on your phone: http://YOUR_SERVER_IP:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down intake server...")
        server.server_close()


if __name__ == "__main__":
    main()
