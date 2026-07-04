"""Settings — everything secret lives in .env, never in code."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Xero OAuth2 (create an app at developer.xero.com -> My Apps)
    xero_client_id: str = ""
    xero_client_secret: str = ""
    xero_redirect_uri: str = "http://localhost:8912/callback"
    # Granular scopes — mandatory for apps created on/after 2 Mar 2026 (the old
    # broad accounting.transactions is rejected with invalid_scope for them).
    # invoices covers credit notes too; settings.read covers GET /Accounts.
    xero_scopes: str = (
        "offline_access openid profile email "
        "accounting.invoices accounting.payments accounting.banktransactions "
        "accounting.contacts accounting.settings.read accounting.attachments"
    )

    # Behaviour
    dry_run: bool = True                 # guardrail: nothing is filed or written unless explicitly disabled
    db_path: str = "claimback.db"
    token_cache_path: str = ".xero_tokens.json"

    # Detection thresholds
    no_scan_days: int = 10               # no tracking movement for N days => presumed lost
    claim_window_days: int = 28          # courier time-bar for filing

    # Xero chart-of-accounts codes — VERIFY against the demo org on day one:
    # GET /Accounts, pick a revenue/other-income code for recoveries and a bank
    # account with "enable payments" ticked for payouts.
    xero_recoveries_account: str = "260"
    xero_payment_account: str = "090"


settings = Settings()
