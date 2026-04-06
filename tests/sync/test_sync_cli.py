"""Tests for sync CLI argument parsing."""

from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.sync.cli import parse


class TestParseCli:
    def test_watch_mode(self) -> None:
        global_config, user_configs = parse(
            ["--watch", "-u", "user@test.com", "-d", "/tmp/test"]
        )
        assert global_config.schedule is not None
        assert global_config.schedule.daily_preferred_hour == 2
        assert global_config.schedule.weekly_preferred_day == 0
        assert global_config.schedule.jitter_max_hours == 3.0
        assert global_config.schedule.daily_lookback_days == 2

    def test_watch_with_custom_schedule(self) -> None:
        global_config, _ = parse(
            [
                "--watch",
                "--daily-hour", "14",
                "--weekly-day", "3",
                "--jitter-hours", "1.5",
                "--daily-lookback-days", "5",
                "-u", "user@test.com", "-d", "/tmp/test",
            ]
        )
        assert global_config.schedule is not None
        assert global_config.schedule.daily_preferred_hour == 14
        assert global_config.schedule.weekly_preferred_day == 3
        assert global_config.schedule.jitter_max_hours == 1.5
        assert global_config.schedule.daily_lookback_days == 5

    def test_no_watch_mode(self) -> None:
        global_config, user_configs = parse(
            ["-u", "user@test.com", "-d", "/tmp/test"]
        )
        assert global_config.schedule is None

    def test_webui_mfa_provider(self) -> None:
        global_config, _ = parse(
            ["--mfa-provider", "webui", "-u", "user@test.com", "-d", "/tmp"]
        )
        assert global_config.mfa_provider == MFAProvider.WEBUI

    def test_webui_password_provider(self) -> None:
        global_config, _ = parse(
            ["--password-provider", "webui", "-u", "user@test.com", "-d", "/tmp"]
        )
        assert PasswordProvider.WEBUI in global_config.password_providers

    def test_multiple_users(self) -> None:
        global_config, user_configs = parse(
            [
                "--watch",
                "-u", "user1@test.com", "-d", "/photos/user1",
                "-u", "user2@test.com", "-d", "/photos/user2",
            ]
        )
        assert len(user_configs) == 2
        assert user_configs[0].username == "user1@test.com"
        assert user_configs[1].username == "user2@test.com"
        assert global_config.schedule is not None
