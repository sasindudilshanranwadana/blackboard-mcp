"""
config.py — Optional settings for Blackboard MCP.

Most settings have sensible defaults.
You only need a .env file if you want to override the Learnline URL
or point to a custom session cache location.

Authentication is handled by setup.py (no credentials needed here).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="BB_",
        extra="ignore",
    )

    # CDU Learnline base URL
    base_url: str = "https://online.cdu.edu.au"

    # Where to cache session cookies between server restarts
    session_cache: str = "~/.bb_mcp_session.json"

    # Set to true to suppress browser window during auto-login
    headless: bool = True


settings = Settings()
