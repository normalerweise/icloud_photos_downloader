"""Pure scheduling functions for two-tier sync: daily (recent) + weekly (full).

All functions are pure — no I/O, no side effects, no imports of iCloud libs.
"""

from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from icloudpd.sync.config import ScheduleConfig


class SyncRunKind(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass(frozen=True)
class UserSchedule:
    user_index: int
    username: str
    weekly_day: int  # 0=Monday, 6=Sunday
    daily_hour: int  # 0-23
    jitter_seconds: int


@dataclass(frozen=True)
class UserScheduleInfo:
    """Serializable schedule info for the web UI."""

    username: str
    weekly_day: str  # e.g. "Wednesday"
    weekly_time: str  # e.g. "05:42"
    daily_time: str  # e.g. "03:17"
    next_run_at: str  # ISO timestamp
    next_run_kind: str  # "daily" or "weekly"


def assign_weekly_day(user_index: int, num_users: int, preferred_day: int) -> int:
    """Distribute users across weekdays deterministically.

    >>> assign_weekly_day(0, 3, 0)
    0
    >>> assign_weekly_day(1, 3, 0)
    2
    >>> assign_weekly_day(2, 3, 0)
    4
    """
    if num_users <= 0:
        return preferred_day % 7
    spacing = max(7 // num_users, 1)
    return (preferred_day + user_index * spacing) % 7


def assign_daily_hour(user_index: int, num_users: int, preferred_hour: int) -> int:
    """Spread daily runs across hours.

    >>> assign_daily_hour(0, 3, 2)
    2
    >>> assign_daily_hour(1, 3, 2)
    10
    >>> assign_daily_hour(2, 3, 2)
    18
    """
    if num_users <= 0:
        return preferred_hour % 24
    spacing = max(24 // num_users, 1)
    return (preferred_hour + user_index * spacing) % 24


def compute_jitter_seconds(username: str, date: datetime.date, max_hours: float) -> int:
    """Deterministic per-user-per-day jitter via hash.

    Same user+date always produces the same jitter. Different days produce different offsets.

    >>> j1 = compute_jitter_seconds("alice@icloud.com", datetime.date(2026, 4, 6), 3.0)
    >>> j2 = compute_jitter_seconds("alice@icloud.com", datetime.date(2026, 4, 6), 3.0)
    >>> j1 == j2
    True
    >>> 0 <= j1 < 3 * 3600
    True
    """
    max_seconds = int(max_hours * 3600)
    if max_seconds <= 0:
        return 0
    return abs(hash(username + date.isoformat())) % max_seconds


def build_user_schedules(
    usernames: Sequence[str], config: ScheduleConfig
) -> Sequence[UserSchedule]:
    """Compute per-user schedule assignments from config.

    >>> from icloudpd.sync.config import ScheduleConfig
    >>> cfg = ScheduleConfig(daily_preferred_hour=2, weekly_preferred_day=0, jitter_max_hours=0.0)
    >>> schedules = build_user_schedules(["a@x.com", "b@x.com"], cfg)
    >>> schedules[0].weekly_day
    0
    >>> schedules[1].weekly_day
    3
    """
    num_users = len(usernames)
    return [
        UserSchedule(
            user_index=i,
            username=username,
            weekly_day=assign_weekly_day(i, num_users, config.weekly_preferred_day),
            daily_hour=assign_daily_hour(i, num_users, config.daily_preferred_hour),
            jitter_seconds=0,  # jitter is computed per-day at schedule time
        )
        for i, username in enumerate(usernames)
    ]


def _next_occurrence_of_weekday_hour(
    now: datetime.datetime,
    weekday: int,
    hour: int,
    jitter_seconds: int,
) -> datetime.datetime:
    """Find the next datetime for a given weekday and hour (with jitter offset)."""
    today = now.date()
    days_ahead = (weekday - today.weekday()) % 7
    candidate_date = today + datetime.timedelta(days=days_ahead)
    candidate = datetime.datetime(
        candidate_date.year,
        candidate_date.month,
        candidate_date.day,
        hour,
        0,
        0,
        tzinfo=now.tzinfo,
    ) + datetime.timedelta(seconds=jitter_seconds)
    if candidate <= now:
        candidate_date = today + datetime.timedelta(days=days_ahead + 7)
        candidate = datetime.datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            hour,
            0,
            0,
            tzinfo=now.tzinfo,
        ) + datetime.timedelta(seconds=jitter_seconds)
    return candidate


def _next_occurrence_of_hour(
    now: datetime.datetime,
    hour: int,
    jitter_seconds: int,
) -> datetime.datetime:
    """Find the next datetime for a given hour of day (with jitter offset)."""
    today = now.date()
    candidate = datetime.datetime(
        today.year, today.month, today.day, hour, 0, 0, tzinfo=now.tzinfo
    ) + datetime.timedelta(seconds=jitter_seconds)
    if candidate <= now:
        tomorrow = today + datetime.timedelta(days=1)
        candidate = datetime.datetime(
            tomorrow.year, tomorrow.month, tomorrow.day, hour, 0, 0, tzinfo=now.tzinfo
        ) + datetime.timedelta(seconds=jitter_seconds)
    return candidate


def compute_next_runs(
    now: datetime.datetime,
    user_schedules: Sequence[UserSchedule],
    config: ScheduleConfig,
) -> Sequence[tuple[UserSchedule, SyncRunKind, datetime.datetime]]:
    """Compute the next daily and weekly run for each user, sorted by time.

    Returns a sequence of (user_schedule, run_kind, scheduled_at) tuples.
    """
    runs: list[tuple[UserSchedule, SyncRunKind, datetime.datetime]] = []
    for us in user_schedules:
        jitter = compute_jitter_seconds(us.username, now.date(), config.jitter_max_hours)

        weekly_at = _next_occurrence_of_weekday_hour(now, us.weekly_day, us.daily_hour, jitter)
        runs.append((us, SyncRunKind.WEEKLY, weekly_at))

        daily_at = _next_occurrence_of_hour(now, us.daily_hour, jitter)
        # Skip daily run if it falls on the same day as the weekly run
        if daily_at.date() != weekly_at.date():
            runs.append((us, SyncRunKind.DAILY, daily_at))

    runs.sort(key=lambda r: r[2])
    return runs


def compute_next_event(
    now: datetime.datetime,
    runs: Sequence[tuple[UserSchedule, SyncRunKind, datetime.datetime]],
) -> tuple[UserSchedule, SyncRunKind, datetime.datetime] | None:
    """Return the next run at or after `now`, or None if empty."""
    for run in runs:
        if run[2] >= now:
            return run
    return None


def build_schedule_info(
    now: datetime.datetime,
    user_schedules: Sequence[UserSchedule],
    config: ScheduleConfig,
) -> Sequence[UserScheduleInfo]:
    """Build web-UI-friendly schedule info for each user."""
    runs = compute_next_runs(now, user_schedules, config)

    # Group next runs by user
    next_per_user: dict[str, tuple[SyncRunKind, datetime.datetime]] = {}
    for us, kind, at in runs:
        if us.username not in next_per_user or at < next_per_user[us.username][1]:
            next_per_user[us.username] = (kind, at)

    infos: list[UserScheduleInfo] = []
    for us in user_schedules:
        jitter = compute_jitter_seconds(us.username, now.date(), config.jitter_max_hours)
        jitter_td = datetime.timedelta(seconds=jitter)

        daily_time = (
            datetime.datetime(2000, 1, 1, us.daily_hour, 0, 0) + jitter_td
        ).strftime("%H:%M")

        weekly_base = datetime.datetime(2000, 1, 1, us.daily_hour, 0, 0) + jitter_td
        weekly_time = weekly_base.strftime("%H:%M")

        next_kind, next_at = next_per_user.get(
            us.username, (SyncRunKind.DAILY, now)
        )

        infos.append(
            UserScheduleInfo(
                username=us.username,
                weekly_day=calendar.day_name[us.weekly_day],
                weekly_time=weekly_time,
                daily_time=daily_time,
                next_run_at=next_at.isoformat(),
                next_run_kind=next_kind.value,
            )
        )
    return infos
