"""Send email notifications for iCloud Photos Downloader events."""

import datetime
import logging
import smtplib
from typing import cast


def send_notification(
    logger: logging.Logger,
    smtp_email: str | None,
    smtp_password: str | None,
    smtp_host: str,
    smtp_port: int,
    smtp_no_tls: bool,
    to_addr: str | None,
    from_addr: str | None,
    subject: str,
    body: str,
) -> None:
    """Send an email notification."""
    to_addr = cast(str, to_addr if to_addr is not None else smtp_email)
    from_addr = (
        from_addr
        if from_addr is not None
        else (f"iCloud Photos Downloader <{smtp_email}>" if smtp_email else to_addr)
    )
    logger.info(f"Sending notification via email: {subject}")
    smtp = smtplib.SMTP(smtp_host, smtp_port)
    smtp.set_debuglevel(0)
    # leaving explicit call of connect to not break unit tests, even though it is
    # called implicitly via constructor parameters
    smtp.connect(smtp_host, smtp_port)
    if not smtp_no_tls:
        smtp.starttls()

    if smtp_email is not None and smtp_password is not None:
        smtp.login(smtp_email, smtp_password)

    date = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = (
        f"From: {from_addr}\n"
        f"To: {to_addr}\n"
        f"Subject: {subject}\n"
        f"Date: {date}\n\n"
        f"{body}"
    )

    smtp.sendmail(from_addr, to_addr, msg)
    smtp.quit()


def send_2sa_notification(
    logger: logging.Logger,
    username: str,
    smtp_email: str | None,
    smtp_password: str | None,
    smtp_host: str,
    smtp_port: int,
    smtp_no_tls: bool,
    to_addr: str | None,
    from_addr: str | None = None,
) -> None:
    """Send an email notification when 2SA is expired."""
    subject = "icloud_photos_downloader: Two step authentication has expired"
    body = (
        f"Hello,\n\n"
        f"{username}'s two-step authentication has expired for the "
        f"icloud_photos_downloader script.\n"
        f"Please log in to your server and run the script manually "
        f"to update two-step authentication."
    )
    send_notification(
        logger, smtp_email, smtp_password, smtp_host, smtp_port,
        smtp_no_tls, to_addr, from_addr, subject, body,
    )
