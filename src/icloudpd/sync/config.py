"""Configuration for the sync architecture."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Sequence

from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider


@dataclass(kw_only=True)
class SyncUserConfig:
    username: str
    password: str | None
    directory: str
    auth_only: bool
    cookie_directory: str
    recent: int | None
    skip_created_before: datetime.datetime | datetime.timedelta | None


@dataclass(kw_only=True)
class SyncGlobalConfig:
    log_level: LogLevel
    domain: str
    password_providers: Sequence[PasswordProvider]
    mfa_provider: MFAProvider
    watch_with_interval: int | None = None
