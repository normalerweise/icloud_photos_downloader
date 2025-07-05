#!/usr/bin/env python
"""Main script that uses Click to parse command-line arguments"""

from multiprocessing import freeze_support

import foundation
from foundation.core import compose, constant, identity
from icloudpd.mfa_provider import MFAProvider

freeze_support()  # fmt: skip # fixing tqdm on macos

import datetime
import json
import logging
import os
import subprocess
import sys
import time
import typing
from logging import Logger
from pathlib import Path
from threading import Thread
from typing import (
    Callable,
    Dict,
    NoReturn,
    Sequence,
    Tuple,
    TypeVar,
)

import click
from tqdm.contrib.logging import logging_redirect_tqdm
from tzlocal import get_localzone

from icloudpd import constants
from icloudpd.authentication import TwoStepAuthRequiredError, authenticator
from icloudpd.config import Config
from icloudpd.email_notifications import send_2sa_notification
from icloudpd.paths import clean_filename, remove_unicode_chars
from icloudpd.server import serve_app
from icloudpd.status import Status, StatusExchange
from icloudpd.string_helpers import parse_timestamp_or_timedelta
from pyicloud_ipd.base import PyiCloudService
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.utils import (
    get_password_from_keyring,
    store_password_in_keyring,
)
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize


def build_filename_cleaner(
    _ctx: click.Context, _param: click.Parameter, is_keep_unicode: bool
) -> Callable[[str], str]:
    """Map keep_unicode parameter for function for cleaning filenames"""
    # redefining typed vars instead of using in ternary directly is a mypy hack
    r: Callable[[str], str] = remove_unicode_chars
    i: Callable[[str], str] = identity
    return compose(
        (r if not is_keep_unicode else i),
        clean_filename,
    )


def lp_filename_concatinator(filename: str) -> str:
    name, ext = os.path.splitext(filename)
    if not ext:
        return filename
    return name + ("_HEVC.MOV" if ext.lower().endswith(".heic") else ".MOV")


def lp_filename_original(filename: str) -> str:
    name, ext = os.path.splitext(filename)
    if not ext:
        return filename
    return name + ".MOV"


def build_lp_filename_generator(
    _ctx: click.Context, _param: click.Parameter, lp_filename_policy: str
) -> Callable[[str], str]:
    # redefining typed vars instead of using in ternary directly is a mypy hack
    return lp_filename_original if lp_filename_policy == "original" else lp_filename_concatinator


def raw_policy_generator(
    _ctx: click.Context, _param: click.Parameter, raw_policy: str
) -> RawTreatmentPolicy:
    # redefining typed vars instead of using in ternary directly is a mypy hack
    if raw_policy == "as-is":
        return RawTreatmentPolicy.AS_IS
    elif raw_policy == "original":
        return RawTreatmentPolicy.AS_ORIGINAL
    elif raw_policy == "alternative":
        return RawTreatmentPolicy.AS_ALTERNATIVE
    else:
        raise ValueError(f"policy was provided with unsupported value of '{raw_policy}'")


def size_generator(
    _ctx: click.Context, _param: click.Parameter, sizes: Sequence[str]
) -> Sequence[AssetVersionSize]:
    def _map(size: str) -> AssetVersionSize:
        if size == "original":
            return AssetVersionSize.ORIGINAL
        elif size == "adjusted":
            return AssetVersionSize.ADJUSTED
        elif size == "alternative":
            return AssetVersionSize.ALTERNATIVE
        elif size == "medium":
            return AssetVersionSize.MEDIUM
        elif size == "thumb":
            return AssetVersionSize.THUMB
        else:
            raise ValueError(f"size was provided with unsupported value of '{size}'")

    return [_map(_s) for _s in sizes]


def mfa_provider_generator(
    _ctx: click.Context, _param: click.Parameter, provider: str
) -> MFAProvider:
    if provider == "console":
        return MFAProvider.CONSOLE
    elif provider == "webui":
        return MFAProvider.WEBUI
    else:
        raise ValueError(f"mfa provider has unsupported value of '{provider}'")


def ask_password_in_console(_user: str) -> str | None:
    return typing.cast(str | None, click.prompt("iCloud Password", hide_input=True))


def get_password_from_webui(
    logger: Logger, status_exchange: StatusExchange
) -> Callable[[str], str | None]:
    def _intern(_user: str) -> str | None:
        """Request two-factor authentication through Webui."""
        if not status_exchange.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_PASSWORD):
            logger.error("Expected NO_INPUT_NEEDED, but got something else")
            return None

        # wait for input
        while True:
            status = status_exchange.get_status()
            if status == Status.NEED_PASSWORD:
                time.sleep(1)
            else:
                break
        if status_exchange.replace_status(Status.SUPPLIED_PASSWORD, Status.CHECKING_PASSWORD):
            password = status_exchange.get_payload()
            if not password:
                logger.error("Internal error: did not get password for SUPPLIED_PASSWORD status")
                status_exchange.replace_status(
                    Status.CHECKING_PASSWORD, Status.NO_INPUT_NEEDED
                )
                return None
            return password

        logger.error("Failed to transition to CHECKING_PASSWORD status")
        return None

    return _intern


def update_password_status_in_webui(status_exchange: StatusExchange) -> Callable[[str, str], None]:
    def _intern(_u: str, _p: str) -> None:
        # TODO we are not handling wrong passwords...
        status_exchange.replace_status(Status.CHECKING_PASSWORD, Status.NO_INPUT_NEEDED)
        return None

    return _intern


def dummy_password_writter(_u: str, _p: str) -> None:
    pass


def keyring_password_writter(logger: Logger) -> Callable[[str, str], None]:
    def _intern(username: str, password: str) -> None:
        try:
            store_password_in_keyring(username, password)
        except Exception as e:
            logger.error(f"Failed to store password in keyring: {e}")

    return _intern


def password_provider_generator(
    _ctx: click.Context, _param: click.Parameter, providers: Sequence[str]
) -> Dict[str, Tuple[Callable[[str], str | None], Callable[[str, str], None]]]:
    def _map(provider: str) -> Tuple[Callable[[str], str | None], Callable[[str, str], None]]:
        if provider == "console":
            return (ask_password_in_console, dummy_password_writter)
        elif provider == "keyring":
            # We'll inject the logger later in the main function
            return (get_password_from_keyring, dummy_password_writter)
        elif provider == "parameter":
            return (constant(None), dummy_password_writter)
        elif provider == "webui":
            # We'll inject the logger and status_exchange later in the main function
            return (ask_password_in_console, dummy_password_writter)
        else:
            raise ValueError(f"password provider has unsupported value of '{provider}'")

    return {provider: _map(provider) for provider in providers}


def lp_size_generator(
    _ctx: click.Context, _param: click.Parameter, size: str
) -> LivePhotoVersionSize:
    if size == "original":
        return LivePhotoVersionSize.ORIGINAL
    elif size == "medium":
        return LivePhotoVersionSize.MEDIUM
    elif size == "thumb":
        return LivePhotoVersionSize.THUMB
    else:
        raise ValueError(f"size was provided with unsupported value of '{size}'")


def file_match_policy_generator(
    _ctx: click.Context, _param: click.Parameter, policy: str
) -> FileMatchPolicy:
    if policy == "name-size-dedup-with-suffix":
        return FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX
    elif policy == "name-id7":
        return FileMatchPolicy.NAME_ID7
    else:
        raise ValueError(f"policy was provided with unsupported value of '{policy}'")


def skip_created_before_generator(
    _ctx: click.Context, _param: click.Parameter, formatted: str
) -> datetime.datetime | datetime.timedelta | None:
    if not formatted:
        return None
    result = parse_timestamp_or_timedelta(formatted)
    if result is None:
        raise ValueError(
            "--skip-created-before parameter did not parse ISO timestamp or interval successfully"
        )
    if isinstance(result, datetime.datetime):
        # Ensure datetime has timezone info for proper comparison in SinceDateStrategy
        if result.tzinfo is None:
            return result.replace(tzinfo=get_localzone())
        return result
    return result


def locale_setter(_ctx: click.Context, _param: click.Parameter, use_os_locale: bool) -> bool:
    # set locale
    if use_os_locale:
        import locale

        locale.setlocale(locale.LC_ALL, "")
    return use_os_locale


def report_version(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
    if not value or ctx.resilient_parsing:
        return value
    vi = foundation.version_info_formatted()
    click.echo(vi)
    ctx.exit()


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

RetrierT = TypeVar("RetrierT")


@click.command(context_settings=CONTEXT_SETTINGS, options_metavar="<options>", no_args_is_help=True)
# Authentication Options
@click.option(
    "-u",
    "--username",
    help="Your iCloud username or email address",
    metavar="<username>",
    prompt="iCloud username/email",
)
@click.option(
    "-p",
    "--password",
    help="Your iCloud password (default: use PyiCloud keyring or prompt for password)",
    metavar="<password>",
)
@click.option(
    "--auth-only",
    help="Create/Update cookie and session tokens only.",
    is_flag=True,
)
@click.option(
    "--cookie-directory",
    help="Directory to store cookies for authentication (default: ~/.pyicloud)",
    metavar="</cookie/directory>",
    default="~/.pyicloud",
)
@click.option(
    "--password-provider",
    "password_providers",
    help="Specifies passwords provider to check in the specified order",
    type=click.Choice(["console", "keyring", "parameter", "webui"], case_sensitive=False),
    default=["parameter", "keyring", "console"],
    show_default=True,
    multiple=True,
    callback=password_provider_generator,
)
@click.option(
    "--mfa-provider",
    help="Specified where to get MFA code from",
    type=click.Choice(["console", "webui"], case_sensitive=False),
    default="console",
    show_default=True,
    callback=mfa_provider_generator,
)
@click.option(
    "--domain",
    help="What iCloud root domain to use. Use 'cn' for mainland China (default: 'com')",
    type=click.Choice(["com", "cn"]),
    default="com",
)
# Functional Options
@click.option(
    "-d",
    "--directory",
    help="Local directory that should be used for download",
    type=click.Path(exists=True),
    metavar="<directory>",
)
@click.option(
    "--recent",
    help="Number of recent photos to download (default: download all photos)",
    type=click.IntRange(0),
)
@click.option(
    "--until-found",
    help="Download most recently added photos until we find x number of "
    "previously downloaded consecutive photos (default: download all photos)",
    type=click.IntRange(0),
)
@click.option(
    "--skip-created-before",
    help="Do not process assets created before specified timestamp in ISO format (2025-01-02) or interval from now (20d)",
    callback=skip_created_before_generator,
)
@click.option(
    "-l",
    "--list-albums",
    help="Lists the available albums",
    is_flag=True,
)
@click.option(
    "--library",
    help="Library to download (default: Personal Library)",
    metavar="<library>",
    default="PrimarySync",
)
@click.option(
    "--list-libraries",
    help="Lists the available libraries",
    is_flag=True,
)
@click.option(
    "--xmp-sidecar",
    help="Export additional data as XMP sidecar files (default: don't export)",
    is_flag=True,
)
@click.option(
    "--auto-delete",
    help='Scans the "Recently Deleted" folder and deletes any files found in there. '
    + "(If you restore the photo in iCloud, it will be downloaded again.)",
    is_flag=True,
)
@click.option(
    "--only-print-filenames",
    help="Only prints the filenames of all files that will be downloaded "
    "(not including files that are already downloaded.)"
    + "(Does not download or delete any files.)",
    is_flag=True,
)
@click.option(
    "--set-exif-datetime",
    help="Write the DateTimeOriginal exif tag from file creation date, " + "if it doesn't exist.",
    is_flag=True,
)
@click.option(
    "--watch-with-interval",
    help="Run downloading in a infinite cycle, waiting specified seconds between runs",
    type=click.IntRange(1),
)
@click.option(
    "--dry-run",
    help="Do not modify local system or iCloud",
    is_flag=True,
    default=False,
)
# General Config Options
@click.option(
    "--log-level",
    help="Log level (default: debug)",
    type=click.Choice(["debug", "info", "error"]),
    default="debug",
)
@click.option(
    "--no-progress-bar",
    help="Disables the one-line progress bar and prints log messages on separate lines "
    "(Progress bar is disabled by default if there is no tty attached)",
    is_flag=True,
)
@click.option(
    "--use-os-locale",
    help="Use locale of the host OS to format dates",
    is_flag=True,
    default=False,
    is_eager=True,
    callback=locale_setter,
)
@click.option(
    "--keep-unicode-in-filenames",
    "filename_cleaner",
    help="Keep unicode chars in file names or remove non all ascii chars",
    type=bool,
    default=False,
    callback=build_filename_cleaner,
)
@click.option(
    "--smtp-username",
    help="Your SMTP username, for sending email notifications when "
    "two-step authentication expires.",
    metavar="<smtp_username>",
)
@click.option(
    "--smtp-password",
    help="Your SMTP password, for sending email notifications when "
    "two-step authentication expires.",
    metavar="<smtp_password>",
)
@click.option(
    "--smtp-host",
    help="Your SMTP server host. Defaults to: smtp.gmail.com",
    metavar="<smtp_host>",
    default="smtp.gmail.com",
)
@click.option(
    "--smtp-port",
    help="Your SMTP server port. Default: 587 (Gmail)",
    metavar="<smtp_port>",
    type=click.IntRange(0),
    default=587,
)
@click.option(
    "--smtp-no-tls",
    help="Pass this flag to disable TLS for SMTP (TLS is required for Gmail)",
    metavar="<smtp_no_tls>",
    is_flag=True,
)
@click.option(
    "--notification-email",
    help="Email address where you would like to receive email notifications. "
    "Default: SMTP username",
    metavar="<notification_email>",
)
@click.option(
    "--notification-email-from",
    help="Email address from which you would like to receive email notifications. "
    "Default: SMTP username or notification-email",
    metavar="<notification_email_from>",
)
@click.option(
    "--notification-script",
    type=click.Path(),
    help="Runs an external script when two factor authentication expires. "
    "(path required: /path/to/my/script.sh)",
)
@click.option(
    "--version",
    help="Show the version, commit hash and timestamp",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=report_version,
)
def main(
    directory: str | None,
    username: str,
    password: str | None,
    auth_only: bool,
    cookie_directory: str,
    recent: int | None,
    until_found: int | None,
    list_albums: bool,
    library: str,
    list_libraries: bool,
    xmp_sidecar: bool,
    auto_delete: bool,
    only_print_filenames: bool,
    set_exif_datetime: bool,
    smtp_username: str | None,
    smtp_password: str | None,
    smtp_host: str,
    smtp_port: int,
    smtp_no_tls: bool,
    notification_email: str | None,
    notification_email_from: str | None,
    log_level: str,
    no_progress_bar: bool,
    notification_script: str | None,
    domain: str,
    watch_with_interval: int | None,
    dry_run: bool,
    filename_cleaner: Callable[[str], str],
    password_providers: Dict[str, Tuple[Callable[[str], str | None], Callable[[str, str], None]]],
    mfa_provider: MFAProvider,
    use_os_locale: bool,
    skip_created_before: datetime.datetime | datetime.timedelta | None,
) -> NoReturn:
    """Download all iCloud photos to a local directory"""

    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logger = logging.getLogger("icloudpd")
    if only_print_filenames:
        logger.disabled = True
    else:
        # Need to make sure disabled is reset to the correct value,
        # because the logger instance is shared between tests.
        logger.disabled = False
        if log_level == "debug":
            logger.setLevel(logging.DEBUG)
        elif log_level == "info":
            logger.setLevel(logging.INFO)
        elif log_level == "error":
            logger.setLevel(logging.ERROR)

    with logging_redirect_tqdm():
        # check required directory param only if not list albums
        if not list_albums and not list_libraries and not directory and not auth_only:
            print("--auth-only, --directory, --list-libraries or --list-albums are required")
            sys.exit(2)

        if auto_delete and (list_albums or only_print_filenames):
            print("--auto-delete is not compatible with --list_albums, --only_print_filenames")
            sys.exit(2)

        if watch_with_interval and (list_albums or only_print_filenames):  # pragma: no cover
            print(
                "--watch_with_interval is not compatible with --list_albums, --only_print_filenames"
            )
            sys.exit(2)

        # hacky way to use one param in another
        if password and "parameter" in password_providers:
            # replace
            password_providers["parameter"] = (constant(password), lambda _r, _w: None)

        if len(password_providers) == 0:  # pragma: no cover
            print("You need to specify at least one --password-provider")
            sys.exit(2)

        if "console" in password_providers and "webui" in password_providers:
            print("Console and webui are not compatible in --password-provider")
            sys.exit(2)

        if "console" in password_providers and list(password_providers)[-1] != "console":
            print("Console must be the last --password-provider")
            sys.exit(2)

        if "webui" in password_providers and list(password_providers)[-1] != "webui":
            print("Webui must be the last --password-provider")
            sys.exit(2)

        status_exchange = StatusExchange()
        config = Config(
            directory=directory,
            username=username,
            auth_only=auth_only,
            cookie_directory=cookie_directory,
            recent=recent,
            until_found=until_found,
            list_albums=list_albums,
            library=library,
            list_libraries=list_libraries,
            xmp_sidecar=xmp_sidecar,
            auto_delete=auto_delete,
            only_print_filenames=only_print_filenames,
            set_exif_datetime=set_exif_datetime,
            smtp_username=smtp_username,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_no_tls=smtp_no_tls,
            notification_email=notification_email,
            notification_email_from=notification_email_from,
            log_level=log_level,
            no_progress_bar=no_progress_bar,
            notification_script=notification_script,
            domain=domain,
            watch_with_interval=watch_with_interval,
            dry_run=dry_run,
            password_providers=password_providers,
            mfa_provider=mfa_provider,
            use_os_locale=use_os_locale,
        )
        status_exchange.set_config(config)

        # hacky way to use one param in another
        if "webui" in password_providers:
            # replace
            password_providers["webui"] = (
                get_password_from_webui(logger, status_exchange),
                update_password_status_in_webui(status_exchange),
            )

        # hacky way to inject logger
        if "keyring" in password_providers:
            # replace
            password_providers["keyring"] = (
                get_password_from_keyring,
                keyring_password_writter(logger),
            )

        # start web server
        if mfa_provider == MFAProvider.WEBUI:
            server_thread = Thread(target=serve_app, daemon=True, args=[logger, status_exchange])
            server_thread.start()

        # Authenticate and get icloud.photos
        try:
            icloud = authenticator(
                logger,
                domain,
                filename_cleaner,
                lambda x: x + ".MOV",  # Fixed live photo naming for new architecture
                RawTreatmentPolicy.AS_IS,  # Fixed for new architecture
                FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX,  # Fixed for new architecture
                password_providers,
                mfa_provider,
                status_exchange,
            )(
                username,
                cookie_directory,
                smtp_username is not None
                or notification_email is not None
                or notification_script is not None,
                os.environ.get("CLIENT_ID"),
            )
        except TwoStepAuthRequiredError:
            if notification_script is not None:
                subprocess.call([notification_script])
            if smtp_username is not None or notification_email is not None:
                send_2sa_notification(
                    logger,
                    username,
                    smtp_username,
                    smtp_password,
                    smtp_host,
                    smtp_port,
                    smtp_no_tls,
                    notification_email,
                    notification_email_from,
                )
            sys.exit(1)

        if auth_only:
            logger.info("Authentication completed successfully")
            sys.exit(0)

        # Use new download architecture
        from icloudpd.new_download.sync_manager import SyncManager
        from icloudpd.new_download.sync_work import (
            NoOpStrategy,
            RecentPhotosStrategy,
            SinceDateStrategy,
        )

        if not directory:
            raise ValueError("Download directory must be specified with --directory")
        base_dir = Path(directory)
        sync_manager = SyncManager(base_dir)
        # Select filter strategy
        if recent is not None:
            photos_to_sync = RecentPhotosStrategy(icloud.photos, recent)
        elif skip_created_before is not None and isinstance(skip_created_before, datetime.datetime):
            photos_to_sync = SinceDateStrategy(icloud.photos, skip_created_before)
        else:
            photos_to_sync = NoOpStrategy(icloud.photos)
        # Run sync
        stats = sync_manager.sync_photos(photos_to_sync.__iter__())
        print("Sync complete.")
        print(json.dumps(stats, indent=2))
        sys.exit(0)


def retrier(
    func: Callable[[], RetrierT], error_handler: Callable[[Exception, int], None]
) -> RetrierT:
    """Run main func and retry helper if receive session error"""
    attempts = 0
    while True:
        try:
            return func()
        except Exception as ex:
            attempts += 1
            error_handler(ex, attempts)
            if attempts > constants.MAX_RETRIES:
                raise


def session_error_handle_builder(
    logger: Logger, icloud: PyiCloudService
) -> Callable[[Exception, int], None]:
    """Build handler for session error"""

    def session_error_handler(ex: Exception, attempt: int) -> None:
        """Handles session errors in the PhotoAlbum photos iterator"""
        if "Invalid global session" in str(ex):
            if attempt > constants.MAX_RETRIES:
                logger.error("iCloud re-authentication failed. Please try again later.")
                raise ex
            logger.error("Session error, re-authenticating...")
            if attempt > 1:
                # If the first re-authentication attempt failed,
                # start waiting a few seconds before retrying in case
                # there are some issues with the Apple servers
                time.sleep(constants.WAIT_SECONDS * attempt)
            icloud.authenticate()

    return session_error_handler


def internal_error_handle_builder(logger: logging.Logger) -> Callable[[Exception, int], None]:
    """Build handler for internal error"""

    def internal_error_handler(ex: Exception, attempt: int) -> None:
        """Handles session errors in the PhotoAlbum photos iterator"""
        if "INTERNAL_ERROR" in str(ex):
            if attempt > constants.MAX_RETRIES:
                logger.error("Internal Error at Apple.")
                raise ex
            logger.error("Internal Error at Apple, retrying...")
            # start waiting a few seconds before retrying in case
            # there are some issues with the Apple servers
            time.sleep(constants.WAIT_SECONDS * attempt)

    return internal_error_handler


def compose_handlers(
    handlers: Sequence[Callable[[Exception, int], None]],
) -> Callable[[Exception, int], None]:
    """Compose multiple error handlers"""

    def composed(ex: Exception, retries: int) -> None:
        for handler in handlers:
            handler(ex, retries)

    return composed


RetrierT = TypeVar("RetrierT")
