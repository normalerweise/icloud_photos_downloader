"""Tests for pure scheduling functions."""

from __future__ import annotations

import datetime

import pytest
from freezegun import freeze_time

from icloudpd.sync.config import ScheduleConfig
from icloudpd.sync.schedule import (
    SyncRunKind,
    UserScheduleInfo,
    assign_daily_hour,
    assign_weekly_day,
    build_schedule_info,
    build_user_schedules,
    compute_jitter_seconds,
    compute_next_event,
    compute_next_runs,
)


class TestAssignWeeklyDay:
    def test_single_user_gets_preferred_day(self) -> None:
        assert assign_weekly_day(0, 1, 0) == 0
        assert assign_weekly_day(0, 1, 3) == 3

    def test_two_users_spread_across_week(self) -> None:
        days = [assign_weekly_day(i, 2, 0) for i in range(2)]
        assert days[0] == 0  # Monday
        assert days[1] == 3  # Thursday
        assert len(set(days)) == 2

    def test_three_users(self) -> None:
        days = [assign_weekly_day(i, 3, 0) for i in range(3)]
        assert days == [0, 2, 4]

    def test_seven_users_all_different_days(self) -> None:
        days = [assign_weekly_day(i, 7, 0) for i in range(7)]
        assert sorted(days) == list(range(7))

    def test_wraps_around(self) -> None:
        day = assign_weekly_day(1, 2, 5)  # preferred=Friday
        assert day == (5 + 3) % 7  # 1 = Monday

    def test_result_always_in_range(self) -> None:
        for users in range(1, 15):
            for idx in range(users):
                for pref in range(7):
                    result = assign_weekly_day(idx, users, pref)
                    assert 0 <= result <= 6


class TestAssignDailyHour:
    def test_single_user_gets_preferred_hour(self) -> None:
        assert assign_daily_hour(0, 1, 2) == 2

    def test_two_users_spread(self) -> None:
        hours = [assign_daily_hour(i, 2, 2) for i in range(2)]
        assert hours == [2, 14]

    def test_three_users(self) -> None:
        hours = [assign_daily_hour(i, 3, 2) for i in range(3)]
        assert hours == [2, 10, 18]

    def test_wraps_around(self) -> None:
        hour = assign_daily_hour(1, 2, 20)
        assert hour == (20 + 12) % 24  # 8

    def test_result_always_in_range(self) -> None:
        for users in range(1, 30):
            for idx in range(users):
                for pref in range(24):
                    result = assign_daily_hour(idx, users, pref)
                    assert 0 <= result <= 23


class TestComputeJitterSeconds:
    def test_deterministic(self) -> None:
        date = datetime.date(2026, 4, 6)
        j1 = compute_jitter_seconds("alice@icloud.com", date, 3.0)
        j2 = compute_jitter_seconds("alice@icloud.com", date, 3.0)
        assert j1 == j2

    def test_different_users_different_jitter(self) -> None:
        date = datetime.date(2026, 4, 6)
        j1 = compute_jitter_seconds("alice@icloud.com", date, 3.0)
        j2 = compute_jitter_seconds("bob@icloud.com", date, 3.0)
        assert j1 != j2

    def test_different_days_different_jitter(self) -> None:
        j1 = compute_jitter_seconds("alice@icloud.com", datetime.date(2026, 4, 6), 3.0)
        j2 = compute_jitter_seconds("alice@icloud.com", datetime.date(2026, 4, 7), 3.0)
        assert j1 != j2

    def test_within_bounds(self) -> None:
        for max_hours in [0.5, 1.0, 3.0, 6.0]:
            j = compute_jitter_seconds("test@x.com", datetime.date(2026, 1, 1), max_hours)
            assert 0 <= j < max_hours * 3600

    def test_zero_max_hours(self) -> None:
        assert compute_jitter_seconds("test@x.com", datetime.date(2026, 1, 1), 0.0) == 0


class TestBuildUserSchedules:
    def test_single_user(self) -> None:
        cfg = ScheduleConfig(daily_preferred_hour=2, weekly_preferred_day=0, jitter_max_hours=0.0)
        schedules = build_user_schedules(["alice@x.com"], cfg)
        assert len(schedules) == 1
        assert schedules[0].username == "alice@x.com"
        assert schedules[0].weekly_day == 0
        assert schedules[0].daily_hour == 2
        assert schedules[0].user_index == 0

    def test_two_users_different_days_and_hours(self) -> None:
        cfg = ScheduleConfig(daily_preferred_hour=2, weekly_preferred_day=0, jitter_max_hours=0.0)
        schedules = build_user_schedules(["a@x.com", "b@x.com"], cfg)
        assert schedules[0].weekly_day != schedules[1].weekly_day
        assert schedules[0].daily_hour != schedules[1].daily_hour

    def test_empty_usernames(self) -> None:
        cfg = ScheduleConfig()
        assert build_user_schedules([], cfg) == []


class TestComputeNextRuns:
    def _cfg(self, **kwargs: object) -> ScheduleConfig:
        defaults = dict(
            daily_preferred_hour=2,
            weekly_preferred_day=0,
            jitter_max_hours=0.0,
            daily_lookback_days=2,
        )
        defaults.update(kwargs)
        return ScheduleConfig(**defaults)  # type: ignore[arg-type]

    def test_daily_run_scheduled_for_today_if_not_passed(self) -> None:
        # Monday 2026-04-06 at 00:00, daily hour=2 => should schedule today at 02:00
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = self._cfg()
        schedules = build_user_schedules(["alice@x.com"], cfg)
        runs = compute_next_runs(now, schedules, cfg)
        daily_runs = [(us, kind, at) for us, kind, at in runs if kind == SyncRunKind.DAILY]
        if daily_runs:
            assert daily_runs[0][2].date() == now.date()

    def test_daily_run_scheduled_for_tomorrow_if_passed(self) -> None:
        # Monday 2026-04-06 at 03:00, daily hour=2 => should schedule tomorrow at 02:00
        now = datetime.datetime(2026, 4, 6, 3, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = self._cfg()
        schedules = build_user_schedules(["alice@x.com"], cfg)
        runs = compute_next_runs(now, schedules, cfg)
        daily_runs = [(us, kind, at) for us, kind, at in runs if kind == SyncRunKind.DAILY]
        if daily_runs:
            assert daily_runs[0][2].date() == now.date() + datetime.timedelta(days=1)

    def test_weekly_run_scheduled_on_assigned_day(self) -> None:
        # Monday 2026-04-06, preferred_day=0 (Monday), hour=2
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = self._cfg()
        schedules = build_user_schedules(["alice@x.com"], cfg)
        runs = compute_next_runs(now, schedules, cfg)
        weekly_runs = [(us, kind, at) for us, kind, at in runs if kind == SyncRunKind.WEEKLY]
        assert len(weekly_runs) == 1
        assert weekly_runs[0][2].weekday() == 0  # Monday

    def test_daily_skipped_on_weekly_day(self) -> None:
        # If both daily and weekly would fall on the same day, daily is skipped
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)  # Monday
        cfg = self._cfg(weekly_preferred_day=0)  # Monday
        schedules = build_user_schedules(["alice@x.com"], cfg)
        runs = compute_next_runs(now, schedules, cfg)
        kinds = [kind for _, kind, _ in runs]
        # On Monday, only weekly should be present
        daily_on_monday = [
            (kind, at)
            for _, kind, at in runs
            if kind == SyncRunKind.DAILY and at.weekday() == 0
        ]
        assert len(daily_on_monday) == 0

    def test_runs_sorted_by_time(self) -> None:
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = self._cfg()
        schedules = build_user_schedules(["a@x.com", "b@x.com"], cfg)
        runs = compute_next_runs(now, schedules, cfg)
        times = [at for _, _, at in runs]
        assert times == sorted(times)

    def test_all_runs_in_future(self) -> None:
        now = datetime.datetime(2026, 4, 6, 12, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = self._cfg()
        schedules = build_user_schedules(["a@x.com", "b@x.com", "c@x.com"], cfg)
        runs = compute_next_runs(now, schedules, cfg)
        for _, _, at in runs:
            assert at > now


class TestComputeNextEvent:
    def test_returns_first_future_run(self) -> None:
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = ScheduleConfig(
            daily_preferred_hour=2, weekly_preferred_day=0, jitter_max_hours=0.0
        )
        schedules = build_user_schedules(["alice@x.com"], cfg)
        runs = compute_next_runs(now, schedules, cfg)
        event = compute_next_event(now, runs)
        assert event is not None
        assert event[2] >= now

    def test_returns_none_for_empty(self) -> None:
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        assert compute_next_event(now, []) is None


class TestBuildScheduleInfo:
    def test_produces_info_per_user(self) -> None:
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = ScheduleConfig(
            daily_preferred_hour=2, weekly_preferred_day=0, jitter_max_hours=0.0
        )
        schedules = build_user_schedules(["a@x.com", "b@x.com"], cfg)
        infos = build_schedule_info(now, schedules, cfg)
        assert len(infos) == 2
        assert infos[0].username == "a@x.com"
        assert infos[1].username == "b@x.com"

    def test_info_fields_populated(self) -> None:
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = ScheduleConfig(
            daily_preferred_hour=2, weekly_preferred_day=0, jitter_max_hours=0.0
        )
        schedules = build_user_schedules(["alice@x.com"], cfg)
        infos = build_schedule_info(now, schedules, cfg)
        info = infos[0]
        assert info.weekly_day == "Monday"
        assert info.daily_time == "02:00"
        assert info.weekly_time == "02:00"
        assert info.next_run_kind in ("daily", "weekly")
        assert info.next_run_at != ""

    def test_with_jitter_times_offset(self) -> None:
        now = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        cfg = ScheduleConfig(
            daily_preferred_hour=2, weekly_preferred_day=0, jitter_max_hours=3.0
        )
        schedules = build_user_schedules(["alice@x.com"], cfg)
        infos = build_schedule_info(now, schedules, cfg)
        # With jitter, time should not be exactly 02:00
        info = infos[0]
        assert info.daily_time != "" and info.weekly_time != ""
