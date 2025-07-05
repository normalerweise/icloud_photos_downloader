from typing import Callable, Dict, Tuple

from icloudpd.mfa_provider import MFAProvider


class Config:
    def __init__(
        self,
        directory: str | None,
        username: str,
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
        password_providers: Dict[
            str, Tuple[Callable[[str], str | None], Callable[[str, str], None]]
        ],
        mfa_provider: MFAProvider,
        use_os_locale: bool,
    ):
        self.directory = directory
        self.username = username
        self.auth_only = auth_only
        self.cookie_directory = cookie_directory
        self.recent = recent
        self.until_found = until_found
        self.list_albums = list_albums
        self.library = library
        self.list_libraries = list_libraries
        self.xmp_sidecar = xmp_sidecar
        self.auto_delete = auto_delete
        self.only_print_filenames = only_print_filenames
        self.set_exif_datetime = set_exif_datetime
        self.smtp_username = smtp_username
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_no_tls = smtp_no_tls
        self.notification_email = notification_email
        self.notification_email_from = notification_email_from
        self.log_level = log_level
        self.no_progress_bar = no_progress_bar
        self.notification_script = notification_script
        self.domain = domain
        self.watch_with_interval = watch_with_interval
        self.dry_run = dry_run
        self.password_providers = " ".join(str(e) for e in password_providers)
        self.mfa_provider = mfa_provider
        self.use_os_locale = use_os_locale
