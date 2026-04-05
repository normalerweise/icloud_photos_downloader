"""Tests for deployment-related changes: web UI wiring, progress bridge, watch mode."""

from unittest.mock import Mock

from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.progress import Progress
from icloudpd.status import StatusExchange
from icloudpd.sync.config import SyncGlobalConfig, SyncUserConfig
from icloudpd.sync.progress_reporter import WebUIProgressReporter
from icloudpd.sync.runner import (
    _needs_web_server,
    build_password_providers,
)


class TestNeedsWebServer:
    def test_webui_mfa_needs_server(self) -> None:
        config = SyncGlobalConfig(
            log_level="debug",
            domain="com",
            password_providers=[PasswordProvider.CONSOLE],
            mfa_provider=MFAProvider.WEBUI,
        )
        assert _needs_web_server(config) is True

    def test_webui_password_needs_server(self) -> None:
        config = SyncGlobalConfig(
            log_level="debug",
            domain="com",
            password_providers=[PasswordProvider.WEBUI],
            mfa_provider=MFAProvider.CONSOLE,
        )
        assert _needs_web_server(config) is True

    def test_console_only_no_server(self) -> None:
        config = SyncGlobalConfig(
            log_level="debug",
            domain="com",
            password_providers=[PasswordProvider.CONSOLE],
            mfa_provider=MFAProvider.CONSOLE,
        )
        assert _needs_web_server(config) is False


class TestWebUIProgressReporter:
    def test_phase_start_sets_count_and_message(self) -> None:
        progress = Progress()
        reporter = WebUIProgressReporter(progress)
        reporter.phase_start("Phase 1: Change detection", 100)
        assert progress.photos_count == 100
        assert progress.photos_counter == 0
        assert progress.photos_last_message == "Starting Phase 1: Change detection"

    def test_phase_progress_updates_counter(self) -> None:
        progress = Progress()
        reporter = WebUIProgressReporter(progress)
        reporter.phase_start("Phase 1", 200)
        reporter.phase_progress(50, 200)
        assert progress.photos_counter == 50
        assert progress.photos_percent == 25

    def test_phase_complete_sets_message(self) -> None:
        progress = Progress()
        reporter = WebUIProgressReporter(progress)
        reporter.phase_complete("Phase 4", {"downloaded": 5})
        assert progress.photos_last_message == "Completed Phase 4"

    def test_sync_complete_sets_message(self) -> None:
        progress = Progress()
        reporter = WebUIProgressReporter(progress)
        reporter.sync_complete({"total_assets": 100})
        assert progress.photos_last_message == "Sync complete"


class TestBuildPasswordProviders:
    def test_webui_provider_included(self) -> None:
        status_exchange = StatusExchange()
        logger = Mock()
        providers = build_password_providers(
            [PasswordProvider.WEBUI],
            None,
            logger,
            status_exchange,
        )
        assert "webui" in providers

    def test_parameter_provider_returns_password(self) -> None:
        status_exchange = StatusExchange()
        logger = Mock()
        providers = build_password_providers(
            [PasswordProvider.PARAMETER],
            "my-password",
            logger,
            status_exchange,
        )
        reader, _ = providers["parameter"]
        assert reader("any-user") == "my-password"


class TestStatusExchangeGenericConfig:
    def test_accepts_sync_global_config(self) -> None:
        se = StatusExchange()
        config = SyncGlobalConfig(
            log_level="debug",
            domain="com",
            password_providers=[PasswordProvider.CONSOLE],
            mfa_provider=MFAProvider.CONSOLE,
        )
        se.set_global_config(config)
        assert se.get_global_config() is config

    def test_accepts_sync_user_configs(self) -> None:
        se = StatusExchange()
        configs = [
            SyncUserConfig(
                username="user@test.com",
                password=None,
                directory="/tmp/test",
                auth_only=False,
                cookie_directory="~/.pyicloud",
                recent=None,
                skip_created_before=None,
            )
        ]
        se.set_user_configs(configs)
        assert se.get_user_configs() == configs


class TestSyncGlobalConfigWatchMode:
    def test_watch_with_interval_default_none(self) -> None:
        config = SyncGlobalConfig(
            log_level="debug",
            domain="com",
            password_providers=[PasswordProvider.CONSOLE],
            mfa_provider=MFAProvider.CONSOLE,
        )
        assert config.watch_with_interval is None

    def test_watch_with_interval_set(self) -> None:
        config = SyncGlobalConfig(
            log_level="debug",
            domain="com",
            password_providers=[PasswordProvider.CONSOLE],
            mfa_provider=MFAProvider.CONSOLE,
            watch_with_interval=3600,
        )
        assert config.watch_with_interval == 3600
