"""YAML config file loader for icloudpd-sync."""

from __future__ import annotations

import pathlib
from pathlib import Path
from typing import Any, Sequence, Tuple

import yaml

from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.string_helpers import parse_timestamp_or_timedelta
from icloudpd.sync.config import (
    NotificationConfig,
    ScheduleConfig,
    SyncGlobalConfig,
    SyncUserConfig,
)

_FORBIDDEN_KEYS = frozenset({"password", "passwords"})


def load_config(
    raw: dict[str, Any],
) -> Tuple[SyncGlobalConfig, Sequence[SyncUserConfig]]:
    """Parse a raw YAML dict into config dataclasses.

    Pure function — takes a dict (already loaded from YAML),
    returns the same tuple as cli.parse().
    Raises ValueError on validation failures.
    """
    _reject_forbidden_keys(raw)

    schedule = _parse_schedule(raw.get("schedule"))
    notification = _parse_notification(raw.get("notification"))

    global_config = SyncGlobalConfig(
        log_level=LogLevel(raw.get("log_level", "debug")),
        domain=raw.get("domain", "com"),
        password_providers=[
            PasswordProvider(p)
            for p in raw.get("password_providers", ["parameter", "keyring", "console"])
        ],
        mfa_provider=MFAProvider(raw.get("mfa_provider", "console")),
        schedule=schedule,
        notification=notification,
    )

    defaults = raw.get("defaults", {})
    _reject_forbidden_keys(defaults)

    users_raw = raw.get("users", [])
    if not users_raw:
        raise ValueError("Config file must define at least one user")

    user_configs = [_parse_user(defaults, u) for u in users_raw]

    return global_config, user_configs


def _parse_schedule(schedule_raw: dict[str, Any] | None) -> ScheduleConfig | None:
    if schedule_raw is None:
        return None
    return ScheduleConfig(
        daily_preferred_hour=schedule_raw.get("daily_preferred_hour", 2),
        weekly_preferred_day=schedule_raw.get("weekly_preferred_day", 0),
        jitter_max_hours=schedule_raw.get("jitter_max_hours", 3.0),
        daily_lookback_days=schedule_raw.get("daily_lookback_days", 2),
    )


def _parse_notification(raw: dict[str, Any] | None) -> NotificationConfig | None:
    if raw is None:
        return None
    script = raw.get("script")
    return NotificationConfig(
        smtp_username=raw.get("smtp_username"),
        smtp_password=raw.get("smtp_password"),
        smtp_host=raw.get("smtp_host", "smtp.gmail.com"),
        smtp_port=raw.get("smtp_port", 587),
        smtp_no_tls=raw.get("smtp_no_tls", False),
        notification_email=raw.get("email"),
        notification_email_from=raw.get("email_from"),
        notification_script=pathlib.Path(script) if script else None,
    )


def _parse_user(
    defaults: dict[str, Any],
    user: dict[str, Any],
) -> SyncUserConfig:
    _reject_forbidden_keys(user)
    merged = {**defaults, **user}

    if "username" not in merged:
        raise ValueError("Each user must have a 'username'")

    skip_raw = merged.get("skip_created_before")
    skip_parsed = (
        parse_timestamp_or_timedelta(str(skip_raw)) if skip_raw is not None else None
    )

    return SyncUserConfig(
        username=merged["username"],
        password=None,
        directory=merged.get("directory", ""),
        auth_only=merged.get("auth_only", False),
        cookie_directory=merged.get("cookie_directory", "~/.pyicloud"),
        recent=merged.get("recent"),
        skip_created_before=skip_parsed,
    )


def _reject_forbidden_keys(d: dict[str, Any]) -> None:
    found = _FORBIDDEN_KEYS & d.keys()
    if found:
        raise ValueError(
            f"Config file must not contain secrets: {', '.join(sorted(found))}"
        )


def read_config_file(path: Path) -> dict[str, Any]:
    """Read YAML from disk. Side effect kept at the edge."""
    with path.open() as f:
        result = yaml.safe_load(f)
    if not isinstance(result, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(result).__name__}")
    return result
