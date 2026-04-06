"""CLI entry point for the sync architecture (icloudpd-sync)."""

from __future__ import annotations

import argparse
import copy
import datetime
import pathlib
import sys
from pathlib import Path
from typing import Sequence, Tuple

from tzlocal import get_localzone

import foundation
from foundation.core import map_
from foundation.string_utils import lower
from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.string_helpers import parse_timestamp_or_timedelta
from icloudpd.sync.config import (
    NotificationConfig,
    ScheduleConfig,
    SyncGlobalConfig,
    SyncUserConfig,
)
from icloudpd.sync.config_file import load_config, read_config_file


def _ensure_tzinfo(tz: datetime.tzinfo, dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.astimezone(tz)
    return dt


def _parse_timestamp_or_timedelta_tz(
    formatted: str | None,
) -> datetime.datetime | datetime.timedelta | None:
    if formatted is None:
        return None
    result = parse_timestamp_or_timedelta(formatted)
    if result is None:
        raise argparse.ArgumentTypeError("Not an ISO timestamp or time interval in days")
    if isinstance(result, datetime.datetime):
        return _ensure_tzinfo(get_localzone(), result)
    return result


def _log_level(inp: str) -> LogLevel:
    if inp == "debug":
        return LogLevel.DEBUG
    elif inp == "info":
        return LogLevel.INFO
    elif inp == "error":
        return LogLevel.ERROR
    else:
        raise argparse.ArgumentTypeError(f"Unsupported log level {inp}")


def _add_global_options(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    cloned = copy.deepcopy(parser)
    cloned.add_argument("--help", "-h", action="store_true", default=False)
    cloned.add_argument("--version", action="store_true", default=False)
    cloned.add_argument(
        "--config",
        help="Path to YAML config file",
        default=None,
    )
    cloned.add_argument(
        "--log-level",
        help="Log level (default: %(default)s)",
        choices=["debug", "info", "error"],
        default="debug",
        type=lower,
    )
    cloned.add_argument(
        "--domain",
        help="iCloud root domain. Use 'cn' for mainland China. Default: %(default)s",
        choices=["com", "cn"],
        default="com",
    )
    cloned.add_argument(
        "--password-provider",
        dest="password_providers",
        help="Password providers in order. Default: [parameter, keyring, console]",
        choices=["console", "keyring", "parameter", "webui"],
        default=None,
        action="append",
        type=lower,
    )
    cloned.add_argument(
        "--mfa-provider",
        help="Where to get the MFA code from (default: %(default)s)",
        choices=["console", "webui"],
        default="console",
        type=lower,
    )
    cloned.add_argument(
        "--watch",
        help="Enable scheduled watch mode (daily + weekly sync runs)",
        action="store_true",
        default=False,
    )
    cloned.add_argument(
        "--daily-hour",
        help="Preferred hour (0-23) for daily runs (default: %(default)s)",
        type=int,
        default=2,
    )
    cloned.add_argument(
        "--weekly-day",
        help="Preferred weekday (0=Mon..6=Sun) for weekly full sync (default: %(default)s)",
        type=int,
        default=0,
    )
    cloned.add_argument(
        "--jitter-hours",
        help="Max random jitter in hours to spread API load (default: %(default)s)",
        type=float,
        default=3.0,
    )
    cloned.add_argument(
        "--daily-lookback-days",
        help="Days to look back for daily runs (default: %(default)s)",
        type=int,
        default=2,
    )
    cloned.add_argument(
        "--smtp-username",
        help="SMTP username for email notifications when authentication expires",
        default=None,
    )
    cloned.add_argument(
        "--smtp-password",
        help="SMTP password for email notifications",
        default=None,
    )
    cloned.add_argument(
        "--smtp-host",
        help="SMTP server host (default: %(default)s)",
        default="smtp.gmail.com",
    )
    cloned.add_argument(
        "--smtp-port",
        help="SMTP server port (default: %(default)s)",
        type=int,
        default=587,
    )
    cloned.add_argument(
        "--smtp-no-tls",
        help="Disable TLS for SMTP",
        action="store_true",
        default=False,
    )
    cloned.add_argument(
        "--notification-email",
        help="Email address for notifications (default: SMTP username)",
        default=None,
    )
    cloned.add_argument(
        "--notification-email-from",
        help="From address for notification emails",
        default=None,
    )
    cloned.add_argument(
        "--notification-script",
        help="Script to run when authentication requires user interaction",
        default=None,
    )
    return cloned


def _add_user_options(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    cloned = copy.deepcopy(parser)
    cloned.add_argument(
        "-d",
        "--directory",
        metavar="DIRECTORY",
        help="Local directory for downloads",
    )
    cloned.add_argument(
        "--auth-only",
        action="store_true",
        help="Create/update cookie and session tokens only",
    )
    cloned.add_argument(
        "--cookie-directory",
        help="Directory to store authentication cookies (default: %(default)s)",
        default="~/.pyicloud",
    )
    cloned.add_argument(
        "--recent",
        help="Number of recent photos to download (default: all)",
        type=int,
    )
    cloned.add_argument(
        "--skip-created-before",
        help="Skip assets created before this ISO timestamp or interval (e.g. 20d = 20 days ago)",
        type=_parse_timestamp_or_timedelta_tz,
    )
    return cloned


def _add_username(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    cloned = copy.deepcopy(parser)
    cloned.add_argument(
        "-u",
        "--username",
        help="Apple ID email address. Starts a new user configuration group.",
        type=lower,
    )
    cloned.add_argument(
        "-p",
        "--password",
        help="iCloud password (when password-provider includes 'parameter')",
        default=None,
    )
    return cloned


_GLOBAL_DEFAULTS = {
    "log_level": "debug",
    "domain": "com",
    "password_providers": None,
    "mfa_provider": "console",
    "watch": False,
    "daily_hour": 2,
    "weekly_day": 0,
    "jitter_hours": 3.0,
    "daily_lookback_days": 2,
    "smtp_username": None,
    "smtp_password": None,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_no_tls": False,
    "notification_email": None,
    "notification_email_from": None,
    "notification_script": None,
}


def _apply_cli_overrides(
    yaml_config: SyncGlobalConfig, cli_ns: argparse.Namespace,
) -> SyncGlobalConfig:
    """Override YAML global config with explicitly provided CLI args.

    A CLI arg is considered explicit when its value differs from the argparse default.
    """
    log_level = yaml_config.log_level
    if cli_ns.log_level != _GLOBAL_DEFAULTS["log_level"]:
        log_level = _log_level(cli_ns.log_level)

    domain = yaml_config.domain
    if cli_ns.domain != _GLOBAL_DEFAULTS["domain"]:
        domain = cli_ns.domain

    password_providers = yaml_config.password_providers
    if cli_ns.password_providers is not None:
        password_providers = list(
            map_(
                PasswordProvider,
                foundation.unique_sequence(cli_ns.password_providers),
            )
        )

    mfa_provider = yaml_config.mfa_provider
    if cli_ns.mfa_provider != _GLOBAL_DEFAULTS["mfa_provider"]:
        mfa_provider = MFAProvider(cli_ns.mfa_provider)

    schedule = yaml_config.schedule
    if cli_ns.watch and schedule is None:
        schedule = ScheduleConfig()
    if schedule is not None:
        schedule = ScheduleConfig(
            daily_preferred_hour=(
                cli_ns.daily_hour
                if cli_ns.daily_hour != _GLOBAL_DEFAULTS["daily_hour"]
                else schedule.daily_preferred_hour
            ),
            weekly_preferred_day=(
                cli_ns.weekly_day
                if cli_ns.weekly_day != _GLOBAL_DEFAULTS["weekly_day"]
                else schedule.weekly_preferred_day
            ),
            jitter_max_hours=(
                cli_ns.jitter_hours
                if cli_ns.jitter_hours != _GLOBAL_DEFAULTS["jitter_hours"]
                else schedule.jitter_max_hours
            ),
            daily_lookback_days=(
                cli_ns.daily_lookback_days
                if cli_ns.daily_lookback_days != _GLOBAL_DEFAULTS["daily_lookback_days"]
                else schedule.daily_lookback_days
            ),
        )

    notification = yaml_config.notification
    if _has_cli_notification(cli_ns):
        notification = _notification_from_ns(cli_ns)

    return SyncGlobalConfig(
        log_level=log_level,
        domain=domain,
        password_providers=password_providers,
        mfa_provider=mfa_provider,
        schedule=schedule,
        notification=notification,
    )


def _has_cli_notification(ns: argparse.Namespace) -> bool:
    return (
        ns.smtp_username is not None
        or ns.notification_email is not None
        or ns.notification_script is not None
    )


def _notification_from_ns(ns: argparse.Namespace) -> NotificationConfig:
    script = ns.notification_script
    return NotificationConfig(
        smtp_username=ns.smtp_username,
        smtp_password=ns.smtp_password,
        smtp_host=ns.smtp_host,
        smtp_port=ns.smtp_port,
        smtp_no_tls=ns.smtp_no_tls,
        notification_email=ns.notification_email,
        notification_email_from=ns.notification_email_from,
        notification_script=pathlib.Path(script) if script else None,
    )


def parse(args: Sequence[str]) -> Tuple[SyncGlobalConfig, Sequence[SyncUserConfig]]:
    if len(args) == 0:
        args = ["--help"]

    global_parser = _add_global_options(
        argparse.ArgumentParser(exit_on_error=False, add_help=False, allow_abbrev=False)
    )
    global_ns, non_global_args = global_parser.parse_known_args(args)

    splitted_args = foundation.split_with_alternatives(["-u", "--username"], non_global_args)
    has_cli_users = len(splitted_args) > 1

    if global_ns.config and not has_cli_users:
        raw = read_config_file(Path(global_ns.config))
        yaml_global, yaml_users = load_config(raw)
        global_config = _apply_cli_overrides(yaml_global, global_ns)
        return global_config, yaml_users

    default_args = splitted_args[0]

    default_parser = _add_user_options(
        argparse.ArgumentParser(exit_on_error=False, add_help=False, allow_abbrev=False)
    )
    default_ns = default_parser.parse_args(default_args)

    user_parser = _add_username(
        _add_user_options(
            argparse.ArgumentParser(exit_on_error=False, add_help=False, allow_abbrev=False)
        )
    )

    user_configs = [
        SyncUserConfig(
            username=ns.username,
            password=ns.password,
            directory=ns.directory,
            auth_only=ns.auth_only,
            cookie_directory=ns.cookie_directory,
            recent=ns.recent,
            skip_created_before=ns.skip_created_before,
        )
        for ns in (
            user_parser.parse_args(user_args, copy.deepcopy(default_ns))
            for user_args in splitted_args[1:]
        )
    ]

    schedule_config = (
        ScheduleConfig(
            daily_preferred_hour=global_ns.daily_hour,
            weekly_preferred_day=global_ns.weekly_day,
            jitter_max_hours=global_ns.jitter_hours,
            daily_lookback_days=global_ns.daily_lookback_days,
        )
        if global_ns.watch
        else None
    )

    notification_config = _notification_from_ns(global_ns) if _has_cli_notification(global_ns) else None

    global_config = SyncGlobalConfig(
        log_level=_log_level(global_ns.log_level),
        domain=global_ns.domain,
        password_providers=list(
            map_(
                PasswordProvider,
                foundation.unique_sequence(
                    global_ns.password_providers or ["parameter", "keyring", "console"]
                ),
            )
        ),
        mfa_provider=MFAProvider(global_ns.mfa_provider),
        schedule=schedule_config,
        notification=notification_config,
    )

    return global_config, user_configs


def _format_help() -> str:
    dummy = argparse.ArgumentParser(exit_on_error=False, add_help=False, allow_abbrev=False)
    global_parser = _add_global_options(copy.deepcopy(dummy))
    user_options_parser = _add_user_options(copy.deepcopy(dummy))
    user_parser = _add_username(copy.deepcopy(dummy))

    lines = [
        "usage: icloudpd-sync [GLOBAL] [COMMON] [<USER> [COMMON] ...]",
        "",
        "GLOBAL options:",
        global_parser.format_help(),
        "COMMON options (defaults for all users):",
        user_options_parser.format_help(),
        "USER options:",
        user_parser.format_help(),
    ]
    return "\n".join(lines)


def cli() -> int:
    try:
        global_config, user_configs = parse(sys.argv[1:])
    except argparse.ArgumentError as error:
        print(error)
        return 2

    if global_config == parse(["--help"])[0] and not user_configs:
        # No args provided, help was injected
        pass

    # Handle --help and --version via global_ns directly
    # Re-parse to check for help/version flags
    global_parser = _add_global_options(
        argparse.ArgumentParser(exit_on_error=False, add_help=False, allow_abbrev=False)
    )
    global_ns, _ = global_parser.parse_known_args(sys.argv[1:])

    if not sys.argv[1:] or global_ns.help:
        print(_format_help())
        return 0
    elif global_ns.version:
        print(foundation.version_info_formatted())
        return 0

    if not user_configs:
        print("At least one -u/--username is required")
        return 2

    for uc in user_configs:
        if not uc.auth_only and not uc.directory:
            print("--directory or --auth-only is required for each user")
            return 2

    from icloudpd.sync.runner import run_sync

    return run_sync(global_config, user_configs)
