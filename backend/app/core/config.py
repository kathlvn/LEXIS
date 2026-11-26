"""
Central app settings, loaded from environment variables (backend/.env on
the machine that runs LEXIS — the ESSU server, or a local machine as fallback).

Nothing here is hardcoded to a specific machine — moving LEXIS to another
server just means copying the same variable names into its .env.
"""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Database ---
    database_url: str = Field(
        default="postgresql+psycopg://leo_user:leo_pass@localhost:5432/leo_db",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )

    # --- Document storage (core/storage.py) ---
    # "local" = files on this machine's disk (development).
    # "gdrive" = the Legal Office's Google Drive (ESSU Google Workspace).
    storage_backend: str = Field(
        default="local",
        validation_alias=AliasChoices("STORAGE_BACKEND", "storage_backend"),
    )
    # Folder for the local backend. Empty = backend/storage.
    # LOIS_STORAGE_ROOT is the older name, still accepted.
    local_storage_dir: str = Field(
        default="",
        validation_alias=AliasChoices("LOCAL_STORAGE_DIR", "LOIS_STORAGE_ROOT", "local_storage_dir"),
    )
    # Google Drive backend: path to the service-account JSON key (kept outside
    # the repo), and the ID of the Shared Drive folder that holds the files.
    gdrive_credentials_file: str = Field(
        default="",
        validation_alias=AliasChoices("GDRIVE_CREDENTIALS_FILE", "gdrive_credentials_file"),
    )
    gdrive_folder_id: str = Field(
        default="",
        validation_alias=AliasChoices("GDRIVE_FOLDER_ID", "gdrive_folder_id"),
    )

    # --- Auth ---
    secret_key: str = Field(
        default="dev-secret-key-change-me",
        validation_alias=AliasChoices("SECRET_KEY", "secret_key"),
    )
    session_cookie_name: str = Field(
        default="lois_session",
        validation_alias=AliasChoices("SESSION_COOKIE_NAME", "session_cookie_name"),
    )
    session_expire_minutes: int = 60 * 12  # 12 hours — hard limit, even if active
    session_idle_minutes: int = 30         # signed out after this long with no activity
    session_cookie_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("SESSION_COOKIE_SECURE", "session_cookie_secure"),
    )
    verification_code_expire_minutes: int = 15
    max_failed_login_attempts: int = 5
    lockout_minutes: int = 15
    
    # --- Public requester access codes ---
    requester_code_expire_minutes: int = 10
    requester_code_max_attempts: int = 5      
    requester_code_resend_cooldown_seconds: int = 60
    requester_code_max_per_hour: int = 5  
    requester_session_idle_minutes: int = 30      # logged out after this long with no activity
    requester_session_max_minutes: int = 120      # hard limit, even if active
    requester_cookie_name: str = "lois_track"  
    submit_verification_minutes: int = 30         # time to finish submitting after verifying
    submit_cookie_name: str = "lois_submit"
    
    # --- Outgoing email (SMTP) ---
    # Gmail during development/defense; the Legal Office mailbox at turnover.
    # Real values go in backend/.env only — never in this file or on GitHub.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""           # Gmail: a 16-character App Password, not the normal password
    mail_from_address: str = ""
    mail_from_name: str = "ESSU Legal Office"
    mail_daily_limit: int = 300       # Gmail allows ~500/day; stay safely under it      
 
    # Sign-in codes may use at most this many of mail_daily_limit, so a flood
    # of "send code" can never stop status updates from reaching clients.
    mail_daily_code_limit: int = 200

    # --- Bot check before a sign-in code is sent (ALTCHA, self-hosted) ---
    bot_check_enabled: bool = True
    bot_check_cost: int = 5000            # PBKDF2 rounds per try: about a second for a person's browser
    bot_check_expire_minutes: int = 10

    # --- Holiday import (Official Gazette proclamation + Tesseract OCR) ---
    # Full path to tesseract(.exe). Empty = look on PATH, then the standard
    # Windows folder (C:\Program Files\Tesseract-OCR). See core/holiday_import.py.
    tesseract_cmd: str = ""

    # --- Links in emails ---
    # Public address of the LEXIS site. Development: Caddy on localhost:8080.
    # Deployment: the real https address (set PUBLIC_SITE_URL in .env).
    public_site_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices("PUBLIC_SITE_URL", "public_site_url"),
    )

    # --- App ---
    # Must be set in .env: "development" or "production". There is no default,
    # so a forgotten setting can't quietly turn on the development shortcuts;
    # main.py refuses to start without it.
    environment: str = Field(
        default="",
        validation_alias=AliasChoices("ENVIRONMENT", "environment"),
    )
    # Print every SQL statement to the log (values are always hidden). For
    # debugging only; off by default, in development too.
    sql_echo: bool = Field(
        default=False,
        validation_alias=AliasChoices("SQL_ECHO", "sql_echo"),
    )
    # Caddy serves the pages and /api from one address, so no other site
    # needs CORS. Set CORS_ALLOWED_ORIGINS only if that ever changes.
    cors_allowed_origins: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("CORS_ALLOWED_ORIGINS", "cors_allowed_origins"),
    )
    # Who may tell LEXIS the visitor's address in X-Forwarded-For: only
    # Caddy. From anyone else the header is ignored, so it can't be
    # used to dodge the rate limits. Addresses or ranges, e.g.
    # '["127.0.0.1", "::1"]' (Caddy on the same machine, the default) or
    # '["172.16.0.0/12"]' when the API runs in Docker behind Caddy.
    trusted_proxy_ips: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1"],
        validation_alias=AliasChoices("TRUSTED_PROXY_IPS", "trusted_proxy_ips"),
    )
    # Host names LEXIS answers to, besides localhost, 127.0.0.1 and the host
    # of PUBLIC_SITE_URL. Usually left empty.
    allowed_hosts: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("ALLOWED_HOSTS", "allowed_hosts"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()