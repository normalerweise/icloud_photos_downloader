"""Tests for YAML config file loader."""

import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.sync.config_file import load_config, read_config_file


class TestLoadConfig:
    def test_minimal(self) -> None:
        raw: dict[str, Any] = {
            "users": [{"username": "a@b.com", "directory": "/photos"}],
        }
        g, users = load_config(raw)
        assert g.log_level == LogLevel.DEBUG
        assert g.domain == "com"
        assert g.mfa_provider == MFAProvider.CONSOLE
        assert g.schedule is None
        assert len(users) == 1
        assert users[0].username == "a@b.com"
        assert users[0].directory == "/photos"
        assert users[0].password is None

    def test_full_global(self) -> None:
        raw: dict[str, Any] = {
            "log_level": "info",
            "domain": "cn",
            "password_providers": ["keyring", "webui"],
            "mfa_provider": "webui",
            "schedule": {
                "daily_preferred_hour": 5,
                "weekly_preferred_day": 3,
                "jitter_max_hours": 1.5,
                "daily_lookback_days": 7,
            },
            "users": [{"username": "x@y.com", "directory": "/d"}],
        }
        g, _ = load_config(raw)
        assert g.log_level == LogLevel.INFO
        assert g.domain == "cn"
        assert g.password_providers == [PasswordProvider.KEYRING, PasswordProvider.WEBUI]
        assert g.mfa_provider == MFAProvider.WEBUI
        assert g.schedule is not None
        assert g.schedule.daily_preferred_hour == 5
        assert g.schedule.weekly_preferred_day == 3
        assert g.schedule.jitter_max_hours == 1.5
        assert g.schedule.daily_lookback_days == 7

    def test_schedule_defaults(self) -> None:
        raw: dict[str, Any] = {
            "schedule": {},
            "users": [{"username": "a@b.com", "directory": "/d"}],
        }
        g, _ = load_config(raw)
        assert g.schedule is not None
        assert g.schedule.daily_preferred_hour == 2
        assert g.schedule.weekly_preferred_day == 0
        assert g.schedule.jitter_max_hours == 3.0
        assert g.schedule.daily_lookback_days == 2

    def test_multiple_users(self) -> None:
        raw: dict[str, Any] = {
            "users": [
                {"username": "a@b.com", "directory": "/a"},
                {"username": "c@d.com", "directory": "/c", "recent": 100},
            ],
        }
        _, users = load_config(raw)
        assert len(users) == 2
        assert users[0].username == "a@b.com"
        assert users[1].username == "c@d.com"
        assert users[1].recent == 100

    def test_defaults_merge(self) -> None:
        raw: dict[str, Any] = {
            "defaults": {"cookie_directory": "/cookies", "directory": "/base"},
            "users": [
                {"username": "a@b.com"},
                {"username": "b@b.com", "cookie_directory": "/other"},
            ],
        }
        _, users = load_config(raw)
        assert users[0].cookie_directory == "/cookies"
        assert users[0].directory == "/base"
        assert users[1].cookie_directory == "/other"
        assert users[1].directory == "/base"

    def test_skip_created_before_interval(self) -> None:
        raw: dict[str, Any] = {
            "users": [
                {"username": "a@b.com", "directory": "/d", "skip_created_before": "30d"},
            ],
        }
        _, users = load_config(raw)
        assert users[0].skip_created_before == datetime.timedelta(days=30)

    def test_skip_created_before_timestamp(self) -> None:
        raw: dict[str, Any] = {
            "users": [
                {
                    "username": "a@b.com",
                    "directory": "/d",
                    "skip_created_before": "2024-01-15T00:00:00",
                },
            ],
        }
        _, users = load_config(raw)
        assert isinstance(users[0].skip_created_before, datetime.datetime)

    def test_auth_only(self) -> None:
        raw: dict[str, Any] = {
            "users": [{"username": "a@b.com", "auth_only": True}],
        }
        _, users = load_config(raw)
        assert users[0].auth_only is True
        assert users[0].directory == ""


class TestNotificationConfig:
    def test_no_notification(self) -> None:
        raw: dict[str, Any] = {
            "users": [{"username": "a@b.com", "directory": "/d"}],
        }
        g, _ = load_config(raw)
        assert g.notification is None

    def test_notification_with_smtp(self) -> None:
        raw: dict[str, Any] = {
            "notification": {
                "smtp_username": "me@gmail.com",
                "smtp_password": "app-password",
                "email": "alerts@example.com",
            },
            "users": [{"username": "a@b.com", "directory": "/d"}],
        }
        g, _ = load_config(raw)
        assert g.notification is not None
        assert g.notification.smtp_username == "me@gmail.com"
        assert g.notification.smtp_password == "app-password"
        assert g.notification.notification_email == "alerts@example.com"
        assert g.notification.smtp_host == "smtp.gmail.com"
        assert g.notification.smtp_port == 587

    def test_notification_with_script(self) -> None:
        raw: dict[str, Any] = {
            "notification": {
                "script": "/usr/local/bin/notify.sh",
            },
            "users": [{"username": "a@b.com", "directory": "/d"}],
        }
        g, _ = load_config(raw)
        assert g.notification is not None
        assert g.notification.notification_script is not None
        assert str(g.notification.notification_script) == "/usr/local/bin/notify.sh"
        assert g.notification.smtp_username is None

    def test_notification_custom_smtp(self) -> None:
        raw: dict[str, Any] = {
            "notification": {
                "smtp_host": "mail.example.com",
                "smtp_port": 465,
                "smtp_no_tls": True,
                "email": "admin@example.com",
                "email_from": "icloudpd@nas.local",
            },
            "users": [{"username": "a@b.com", "directory": "/d"}],
        }
        g, _ = load_config(raw)
        assert g.notification is not None
        assert g.notification.smtp_host == "mail.example.com"
        assert g.notification.smtp_port == 465
        assert g.notification.smtp_no_tls is True
        assert g.notification.notification_email_from == "icloudpd@nas.local"


class TestRejectSecrets:
    def test_rejects_password_at_root(self) -> None:
        with pytest.raises(ValueError, match="secrets"):
            load_config({"password": "x", "users": [{"username": "a@b.com"}]})

    def test_rejects_password_in_user(self) -> None:
        with pytest.raises(ValueError, match="secrets"):
            load_config(
                {"users": [{"username": "a@b.com", "password": "x", "directory": "/d"}]}
            )

    def test_rejects_password_in_defaults(self) -> None:
        with pytest.raises(ValueError, match="secrets"):
            load_config(
                {
                    "defaults": {"password": "secret"},
                    "users": [{"username": "a@b.com", "directory": "/d"}],
                }
            )

    def test_rejects_passwords_plural(self) -> None:
        with pytest.raises(ValueError, match="secrets"):
            load_config(
                {"passwords": {"a": "b"}, "users": [{"username": "a@b.com"}]}
            )


class TestValidation:
    def test_no_users(self) -> None:
        with pytest.raises(ValueError, match="at least one user"):
            load_config({"users": []})

    def test_no_users_key(self) -> None:
        with pytest.raises(ValueError, match="at least one user"):
            load_config({})

    def test_user_without_username(self) -> None:
        with pytest.raises(ValueError, match="username"):
            load_config({"users": [{"directory": "/d"}]})


class TestReadConfigFile:
    def test_reads_yaml(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump({"users": [{"username": "a@b.com", "directory": "/d"}]})
        )
        raw = read_config_file(config_path)
        assert raw["users"][0]["username"] == "a@b.com"

    def test_rejects_non_mapping(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            read_config_file(config_path)
