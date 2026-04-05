"""Tests for sync CLI argument parsing."""

from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.sync.cli import parse


class TestParseCli:
    def test_watch_with_interval(self) -> None:
        global_config, user_configs = parse(
            ["--watch-with-interval", "3600", "-u", "user@test.com", "-d", "/tmp/test"]
        )
        assert global_config.watch_with_interval == 3600

    def test_no_watch_with_interval(self) -> None:
        global_config, user_configs = parse(
            ["-u", "user@test.com", "-d", "/tmp/test"]
        )
        assert global_config.watch_with_interval is None

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
                "--watch-with-interval", "60",
                "-u", "user1@test.com", "-d", "/photos/user1",
                "-u", "user2@test.com", "-d", "/photos/user2",
            ]
        )
        assert len(user_configs) == 2
        assert user_configs[0].username == "user1@test.com"
        assert user_configs[1].username == "user2@test.com"
        assert global_config.watch_with_interval == 60
