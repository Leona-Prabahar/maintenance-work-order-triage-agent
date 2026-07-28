"""Application configuration loaded from environment variables."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    max_tokens: int = 8192
    # Cap the agentic tool-use loop so a misbehaving turn can't run forever.
    max_tool_iterations: int = 6

    # MCP server (Streamable HTTP endpoint)
    mcp_server_url: str = "http://mcp-server:8001/mcp"

    # Human-in-the-loop approval
    approval_signing_secret: str = ""
    # Grants are minted at the moment of the click and redeemed immediately, so
    # this only needs to cover one network round-trip.
    approval_ttl_seconds: int = 120
    # Crew that safety-critical work falls back to when the safety rule had to
    # override the model's classification. Empty disables the re-route.
    safety_crew_code: str = "SRR-D"

    # CORS — comma separated origins ("*" allows all, for local/dev)
    cors_origins: str = "*"


settings = Settings()
