"""Runner for the sync architecture."""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
from functools import partial
from pathlib import Path
from threading import Thread
from typing import Callable, Dict, Sequence, Tuple

from icloudpd.authentication import authenticator
from icloudpd.log_handler import WebUILogHandler
from icloudpd.base import (
    ask_password_in_console,
    dummy_password_writter,
    get_password_from_webui,
    keyring_password_writter,
    update_password_status_in_webui,
)
from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.server import serve_app
from icloudpd.status import StatusExchange
from icloudpd.sync.config import SyncGlobalConfig, SyncUserConfig
from icloudpd.sync.database import PhotoDatabase
from icloudpd.sync.download_manager import DownloadManager
from icloudpd.sync.file_manager import FileManager
from icloudpd.sync.filesystem_sync import FilesystemSync
from icloudpd.sync.photo_asset_record_mapper import PhotoAssetRecordMapper
from icloudpd.sync.progress_reporter import TerminalProgressReporter, WebUIProgressReporter
from icloudpd.sync.sync_manager import SyncManager
from icloudpd.sync.sync_strategy import (
    NoOpStrategy,
    RecentPhotosStrategy,
    SinceDateStrategy,
)
from pyicloud_ipd.utils import get_password_from_keyring


def create_logger(log_level: LogLevel) -> logging.Logger:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logger = logging.getLogger("icloudpd")
    logger.disabled = False
    if log_level == LogLevel.DEBUG:
        logger.setLevel(logging.DEBUG)
    elif log_level == LogLevel.INFO:
        logger.setLevel(logging.INFO)
    elif log_level == LogLevel.ERROR:
        logger.setLevel(logging.ERROR)
    else:
        raise ValueError(f"Unsupported logging level {log_level}")
    return logger


def build_password_providers(
    providers: Sequence[PasswordProvider],
    password: str | None,
    logger: logging.Logger,
    status_exchange: StatusExchange,
) -> Dict[str, Tuple[Callable[[str], str | None], Callable[[str, str], None]]]:
    result: Dict[str, Tuple[Callable[[str], str | None], Callable[[str, str], None]]] = {}
    for provider in providers:
        if provider == PasswordProvider.PARAMETER:
            result[provider.value] = (
                lambda _username, pw=password: pw,
                dummy_password_writter,
            )
        elif provider == PasswordProvider.KEYRING:
            result[provider.value] = (
                get_password_from_keyring,
                keyring_password_writter(logger),
            )
        elif provider == PasswordProvider.CONSOLE:
            result[provider.value] = (
                ask_password_in_console,
                dummy_password_writter,
            )
        elif provider == PasswordProvider.WEBUI:
            result[provider.value] = (
                partial(get_password_from_webui, logger, status_exchange),
                partial(update_password_status_in_webui, status_exchange),
            )
    return result


def _needs_web_server(global_config: SyncGlobalConfig) -> bool:
    return (
        global_config.mfa_provider == MFAProvider.WEBUI
        or PasswordProvider.WEBUI in global_config.password_providers
    )


def _sync_all_users(
    global_config: SyncGlobalConfig,
    user_configs: Sequence[SyncUserConfig],
    logger: logging.Logger,
    status_exchange: StatusExchange,
    use_web_server: bool,
) -> int:
    """Run one sync pass for all users. Returns 0 on success, 1 on auth failure."""
    for user_config in user_configs:
        logger.info(f"Processing user: {user_config.username}")
        status_exchange.set_current_user(user_config.username)

        password_providers = build_password_providers(
            global_config.password_providers,
            user_config.password,
            logger,
            status_exchange,
        )

        try:
            icloud = authenticator(
                logger,
                global_config.domain,
                password_providers,
                global_config.mfa_provider,
                status_exchange,
                user_config.username,
                lambda: None,
                lambda _response: None,
                user_config.cookie_directory,
                os.environ.get("CLIENT_ID"),
            )
        except Exception:
            logger.exception(f"Authentication failed for {user_config.username}")
            return 1

        if user_config.auth_only:
            logger.info("Authentication completed successfully")
            return 0

        base_dir = Path(user_config.directory)

        database = PhotoDatabase(base_dir)
        file_manager = FileManager(base_dir)
        mapper = PhotoAssetRecordMapper()
        download_manager = DownloadManager(file_manager, icloud.photos.session, mapper)
        filesystem_sync = FilesystemSync(base_dir, database)
        progress_reporter = (
            WebUIProgressReporter(status_exchange.get_progress())
            if use_web_server
            else TerminalProgressReporter()
        )

        sync_manager = SyncManager(
            base_dir,
            database,
            file_manager,
            mapper,
            download_manager,
            filesystem_sync,
            progress_reporter,
            photo_library=icloud.photos,
        )

        if user_config.recent is not None:
            photos_to_sync = RecentPhotosStrategy(icloud.photos, user_config.recent)
        elif user_config.skip_created_before is not None and isinstance(
            user_config.skip_created_before, datetime.datetime
        ):
            photos_to_sync = SinceDateStrategy(icloud.photos, user_config.skip_created_before)
        else:
            photos_to_sync = NoOpStrategy(icloud.photos)

        stats = sync_manager.sync_photos(photos_to_sync)
        logger.info("Sync complete.")
        print(json.dumps(stats, indent=2))

    status_exchange.clear_current_user()
    return 0


def run_sync(
    global_config: SyncGlobalConfig,
    user_configs: Sequence[SyncUserConfig],
) -> int:
    logger = create_logger(global_config.log_level)
    status_exchange = StatusExchange()

    status_exchange.set_global_config(global_config)
    status_exchange.set_user_configs(user_configs)

    use_web_server = _needs_web_server(global_config)

    if use_web_server:
        web_log_handler = WebUILogHandler(status_exchange.get_log_buffer())
        web_log_handler.setLevel(logger.level)
        logger.addHandler(web_log_handler)

        logger.info("Starting web server for WebUI authentication...")
        server_thread = Thread(
            target=serve_app, daemon=True, args=[logger, status_exchange]
        )
        server_thread.start()

    if not global_config.watch_with_interval:
        return _sync_all_users(
            global_config, user_configs, logger, status_exchange, use_web_server
        )

    # Watch mode: sync repeatedly with interval
    import time

    logger.info(
        f"Running in watch mode with interval {global_config.watch_with_interval}s"
    )
    while True:
        result = _sync_all_users(
            global_config, user_configs, logger, status_exchange, use_web_server
        )
        if result != 0:
            logger.warning(f"Sync iteration failed with code {result}, will retry next cycle")

        progress = status_exchange.get_progress()
        wait = global_config.watch_with_interval
        logger.info(f"Waiting {wait} seconds before next sync...")
        for i in range(1, wait):
            progress.waiting = wait - i
            if progress.resume:
                logger.info("Resume requested, starting next sync early")
                progress.reset()
                break
            if progress.cancel:
                logger.info("Cancel requested, shutting down")
                return 0
            time.sleep(1)
        progress.waiting = 0
