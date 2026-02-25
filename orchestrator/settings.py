"""Orchestrator configuration — loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-20250514"

    # Database (same as TrendyReports)
    database_url: str

    # GitHub
    github_token: str
    github_repo: str  # "owner/repo"
    github_default_branch: str = "main"

    # Orchestrator behavior
    orchestrator_poll_interval_seconds: int = 30
    max_review_cycles: int = 3
    daily_budget_cents: int = 1500  # $15/day default cap

    # Notifications
    notification_email: str = ""
    notification_sms: str = ""

    # Intake server
    intake_port: int = 8080
    intake_secret: str = "change-me"

    # Paths
    product_bible_path: str = "PRODUCT_BIBLE.md"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
