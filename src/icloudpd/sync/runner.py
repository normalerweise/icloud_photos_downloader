"""Runner for the sync architecture."""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import time
from functools import partial
from pathlib import Path
from threading import Thread
from typing import Any, Callable, Dict, Sequence, Tuple

from icloudpd.authentication import authenticator
from icloudpd.base import (
    ask_password_in_console,
    dummy_password_writter,
    get_password_from_webui,
    keyring_password_writter,
    update_password_status_in_webui,
)
from icloudpd.log_handler import WebUILogHandler
from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.server import serve_app
from icloudpd.status import StatusExchange
from icloudpd.sync.config import ScheduleConfig, SyncGlobalConfig, SyncUserConfig
from icloudpd.sync.database import PhotoDatabase
from icloudpd.sync.download_manager import DownloadManager
from icloudpd.sync.file_manager import FileManager
from icloudpd.sync.filesystem_sync import FilesystemSync
from icloudpd.sync.photo_asset_record_mapper import PhotoAssetRecordMapper
from icloudpd.sync.progress_reporter import TerminalProgressReporter, WebUIProgressReporter
from icloudpd.sync.schedule import (
    SyncRunKind,
    UserSchedule,
    build_schedule_info,
    build_user_schedules,
    compute_jitter_seconds,
)
from icloudpd.sync.sync_manager import SyncManager
from icloudpd.sync.sync_strategy import (
    NoOpStrategy,
    PhotosToSync,
    RecentPhotosStrategy,
    SinceDateStrategy,
)
from pyicloud_ipd.services.photos import PhotosService
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


def _strategy_for_user_config(
    user_config: SyncUserConfig, photos_service: PhotosService
) -> PhotosToSync:
    """Derive sync strategy from user CLI config (for one-shot mode)."""
    if user_config.recent is not None:
        return RecentPhotosStrategy(photos_service, user_config.recent)
    elif user_config.skip_created_before is not None and isinstance(
        user_config.skip_created_before, datetime.datetime
    ):
        return SinceDateStrategy(photos_service, user_config.skip_created_before)
    else:
        return NoOpStrategy(photos_service)


def _strategy_for_run_kind(
    kind: SyncRunKind, lookback_days: int, photos_service: PhotosService
) -> PhotosToSync:
    """Build sync strategy based on scheduled run kind."""
    if kind == SyncRunKind.WEEKLY:
        return NoOpStrategy(photos_service)
    else:
        since = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
            days=lookback_days
        )
        return SinceDateStrategy(photos_service, since)


def _sync_single_user(
    global_config: SyncGlobalConfig,
    user_config: SyncUserConfig,
    strategy_factory: Callable[[PhotosService], PhotosToSync],
    logger: logging.Logger,
    status_exchange: StatusExchange,
    use_web_server: bool,
) -> int:
    """Run one sync pass for a single user. Returns 0 on success, 1 on auth failure."""
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

    photos_to_sync = strategy_factory(icloud.photos)

    stats = sync_manager.sync_photos(photos_to_sync)
    logger.info("Sync complete.")
    print(json.dumps(stats, indent=2))
    return 0


def _sync_all_users(
    global_config: SyncGlobalConfig,
    user_configs: Sequence[SyncUserConfig],
    logger: logging.Logger,
    status_exchange: StatusExchange,
    use_web_server: bool,
) -> int:
    """Run one sync pass for all users. Returns 0 on success, 1 on auth failure."""
    for user_config in user_configs:
        result = _sync_single_user(
            global_config,
            user_config,
            partial(_strategy_for_user_config, user_config),
            logger,
            status_exchange,
            use_web_server,
        )
        if result != 0:
            return result

    status_exchange.clear_current_user()
    return 0


def _run_scheduled(
    global_config: SyncGlobalConfig,
    user_configs: Sequence[SyncUserConfig],
    schedule_config: ScheduleConfig,
    logger: logging.Logger,
    status_exchange: StatusExchange,
    use_web_server: bool,
) -> int:
    """Run the two-tier scheduled sync loop using the schedule library."""
    import schedule

    usernames = [uc.username for uc in user_configs]
    user_schedules = build_user_schedules(usernames, schedule_config)
    user_config_by_name = {uc.username: uc for uc in user_configs}
    user_schedule_by_name = {us.username: us for us in user_schedules}

    pending_runs: list[tuple[UserSchedule, SyncRunKind]] = []

    def _enqueue(user_schedule: UserSchedule, kind: SyncRunKind) -> None:
        # On the weekly day, both daily and weekly jobs fire.
        # Skip the daily run — the weekly full sync covers it.
        if kind == SyncRunKind.DAILY:
            today_weekday = datetime.date.today().weekday()
            if today_weekday == user_schedule.weekly_day:
                return
        pending_runs.append((user_schedule, kind))

    scheduler = schedule.Scheduler()

    _register_jobs(scheduler, user_schedules, schedule_config, _enqueue, logger)

    # Set initial schedule info for web UI
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    status_exchange.set_schedule_info(
        build_schedule_info(now, user_schedules, schedule_config)
    )

    logger.info("Scheduled sync mode active. Registered jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job}")

    progress = status_exchange.get_progress()

    while True:
        scheduler.run_pending()

        # Process manually triggered runs from the web UI
        for username, kind in status_exchange.drain_manual_triggers():
            if username in user_config_by_name:
                logger.info(f"Manual {kind.value} sync triggered for {username}")
                pending_runs.append(
                    (user_schedule_by_name[username], kind)
                )

        for user_schedule, kind in pending_runs:
            user_config = user_config_by_name[user_schedule.username]
            logger.info(f"Starting {kind.value} sync for {user_schedule.username}")
            result = _sync_single_user(
                global_config,
                user_config,
                partial(_strategy_for_run_kind, kind, schedule_config.daily_lookback_days),
                logger,
                status_exchange,
                use_web_server,
            )
            if result != 0:
                logger.warning(
                    f"{kind.value} sync failed for {user_schedule.username} "
                    f"with code {result}, will retry next cycle"
                )
            status_exchange.clear_current_user()
        pending_runs.clear()

        # Update schedule info after processing runs
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        status_exchange.set_schedule_info(
            build_schedule_info(now, user_schedules, schedule_config)
        )

        # Show real time until next run in the UI, sleep in short chunks
        idle = scheduler.idle_seconds
        total_wait = max(int(idle if idle is not None else 60), 1)
        progress.waiting = total_wait
        elapsed = 0
        while elapsed < total_wait:
            if progress.cancel:
                logger.info("Cancel requested, shutting down")
                return 0
            if progress.resume:
                logger.info("Resume requested, starting next sync early")
                progress.reset()
                break
            time.sleep(1)
            elapsed += 1
            progress.waiting = max(total_wait - elapsed, 0)
        progress.waiting = 0


def _register_jobs(
    scheduler: Any,
    user_schedules: Sequence[UserSchedule],
    config: ScheduleConfig,
    enqueue: Callable[[UserSchedule, SyncRunKind], None],
    logger: logging.Logger,
) -> None:
    """Register daily and weekly jobs for each user on the scheduler."""
    import calendar

    day_methods = {
        0: "monday",
        1: "tuesday",
        2: "wednesday",
        3: "thursday",
        4: "friday",
        5: "saturday",
        6: "sunday",
    }

    for us in user_schedules:
        # Compute jitter for today to get the time offset
        today = datetime.date.today()
        jitter = compute_jitter_seconds(us.username, today, config.jitter_max_hours)
        jitter_td = datetime.timedelta(seconds=jitter)
        base_time = datetime.datetime(2000, 1, 1, us.daily_hour, 0, 0) + jitter_td
        time_str = base_time.strftime("%H:%M")

        # Daily job
        daily_job = scheduler.every().day.at(time_str)
        daily_job.do(enqueue, us, SyncRunKind.DAILY)
        daily_job.tag(f"daily-{us.username}")

        # Weekly job on assigned day
        day_attr = day_methods[us.weekly_day]
        weekly_job = getattr(scheduler.every(), day_attr).at(time_str)
        weekly_job.do(enqueue, us, SyncRunKind.WEEKLY)
        weekly_job.tag(f"weekly-{us.username}")

        logger.debug(
            f"User {us.username}: daily at {time_str}, "
            f"weekly on {calendar.day_name[us.weekly_day]} at {time_str}"
        )


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

    if not global_config.schedule:
        return _sync_all_users(
            global_config, user_configs, logger, status_exchange, use_web_server
        )

    return _run_scheduled(
        global_config,
        user_configs,
        global_config.schedule,
        logger,
        status_exchange,
        use_web_server,
    )
