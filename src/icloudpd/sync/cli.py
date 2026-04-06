"""CLI entry point for the sync architecture (icloudpd-sync)."""

from __future__ import annotations

import argparse
import copy
import datetime
import sys
from typing import Sequence, Tuple

from tzlocal import get_localzone

import foundation
from foundation.core import map_
from foundation.string_utils import lower
from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.string_helpers import parse_timestamp_or_timedelta
from icloudpd.sync.config import ScheduleConfig, SyncGlobalConfig, SyncUserConfig


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


def parse(args: Sequence[str]) -> Tuple[SyncGlobalConfig, Sequence[SyncUserConfig]]:
    if len(args) == 0:
        args = ["--help"]

    global_parser = _add_global_options(
        argparse.ArgumentParser(exit_on_error=False, add_help=False, allow_abbrev=False)
    )
    global_ns, non_global_args = global_parser.parse_known_args(args)

    splitted_args = foundation.split_with_alternatives(["-u", "--username"], non_global_args)
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
