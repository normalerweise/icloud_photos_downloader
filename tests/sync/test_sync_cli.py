"""Tests for sync CLI argument parsing."""

from pathlib import Path

import yaml

from icloudpd.log_level import LogLevel
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


class TestParseWithConfigFile:
    def _write_config(self, tmp_path: Path, data: dict) -> str:
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(data))
        return str(p)

    def test_config_file_only(self, tmp_path: Path) -> None:
        path = self._write_config(tmp_path, {
            "log_level": "info",
            "domain": "cn",
            "password_providers": ["webui"],
            "mfa_provider": "webui",
            "schedule": {"daily_preferred_hour": 5},
            "users": [
                {"username": "alice@icloud.com", "directory": "/photos/alice"},
            ],
        })
        g, users = parse(["--config", path])
        assert g.log_level == LogLevel.INFO
        assert g.domain == "cn"
        assert g.password_providers == [PasswordProvider.WEBUI]
        assert g.mfa_provider == MFAProvider.WEBUI
        assert g.schedule is not None
        assert g.schedule.daily_preferred_hour == 5
        assert len(users) == 1
        assert users[0].username == "alice@icloud.com"
        assert users[0].password is None

    def test_cli_overrides_yaml_global(self, tmp_path: Path) -> None:
        path = self._write_config(tmp_path, {
            "log_level": "info",
            "users": [{"username": "a@b.com", "directory": "/d"}],
        })
        g, _ = parse(["--config", path, "--log-level", "error"])
        assert g.log_level == LogLevel.ERROR

    def test_cli_users_replace_yaml_users(self, tmp_path: Path) -> None:
        path = self._write_config(tmp_path, {
            "users": [
                {"username": "yaml@user.com", "directory": "/yaml"},
            ],
        })
        _, users = parse([
            "--config", path,
            "-u", "cli@user.com", "-d", "/cli",
        ])
        assert len(users) == 1
        assert users[0].username == "cli@user.com"

    def test_yaml_schedule_implies_watch(self, tmp_path: Path) -> None:
        path = self._write_config(tmp_path, {
            "schedule": {"daily_preferred_hour": 4},
            "users": [{"username": "a@b.com", "directory": "/d"}],
        })
        g, _ = parse(["--config", path])
        assert g.schedule is not None
        assert g.schedule.daily_preferred_hour == 4

    def test_cli_watch_adds_schedule_to_yaml_without_schedule(self, tmp_path: Path) -> None:
        path = self._write_config(tmp_path, {
            "users": [{"username": "a@b.com", "directory": "/d"}],
        })
        g, _ = parse(["--config", path, "--watch"])
        assert g.schedule is not None

    def test_yaml_notification_preserved(self, tmp_path: Path) -> None:
        path = self._write_config(tmp_path, {
            "notification": {"script": "/bin/notify.sh"},
            "users": [{"username": "a@b.com", "directory": "/d"}],
        })
        g, _ = parse(["--config", path])
        assert g.notification is not None
        assert str(g.notification.notification_script) == "/bin/notify.sh"

    def test_cli_notification_overrides_yaml(self, tmp_path: Path) -> None:
        path = self._write_config(tmp_path, {
            "notification": {"script": "/bin/old.sh"},
            "users": [{"username": "a@b.com", "directory": "/d"}],
        })
        g, _ = parse([
            "--config", path,
            "--notification-script", "/bin/new.sh",
        ])
        assert g.notification is not None
        assert str(g.notification.notification_script) == "/bin/new.sh"


class TestParseNotificationCli:
    def test_no_notification_by_default(self) -> None:
        g, _ = parse(["-u", "a@b.com", "-d", "/d"])
        assert g.notification is None

    def test_notification_script(self) -> None:
        g, _ = parse([
            "--notification-script", "/bin/notify.sh",
            "-u", "a@b.com", "-d", "/d",
        ])
        assert g.notification is not None
        assert str(g.notification.notification_script) == "/bin/notify.sh"

    def test_smtp_notification(self) -> None:
        g, _ = parse([
            "--smtp-username", "me@gmail.com",
            "--smtp-password", "secret",
            "--notification-email", "alerts@example.com",
            "-u", "a@b.com", "-d", "/d",
        ])
        assert g.notification is not None
        assert g.notification.smtp_username == "me@gmail.com"
        assert g.notification.smtp_password == "secret"
        assert g.notification.notification_email == "alerts@example.com"
