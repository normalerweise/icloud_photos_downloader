#!/usr/bin/env python
"""Main script that uses Click to parse command-line arguments"""
from __future__ import print_function
import os
import sys
import time
import datetime
import logging
import itertools
import subprocess
import json
import urllib
import click

from tqdm import tqdm
from tzlocal import get_localzone

from pyicloud_ipd.exceptions import PyiCloudAPIResponseError

from icloudpd.logger import setup_logger
from icloudpd.authentication import authenticate, TwoStepAuthRequiredError
from icloudpd import download
from icloudpd.email_notifications import send_2sa_notification
from icloudpd.string_helpers import truncate_middle
from icloudpd.autodelete import autodelete_photos
from icloudpd.paths import local_download_path
from icloudpd.paths import local_dowload_dir
from icloudpd.paths import library_link_path
from icloudpd.paths import filename_with_size

from icloudpd import exif_datetime
# Must import the constants object so that we can mock values in tests.
from icloudpd import constants
from icloudpd.counter import Counter

from PIL import Image, ExifTags
from pillow_heif import register_heif_opener
import piexif
from future.moves.urllib.parse import urlencode
import base64
from pyicloud_ipd.services.photos import PhotoAlbum
from collections import namedtuple

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

Todos = namedtuple("Todos", "photos_to_delete photos_to_link")

@click.command(context_settings=CONTEXT_SETTINGS, options_metavar="<options>")
# @click.argument(
@click.option(
    "-d", "--directory",
    help="Local directory that should be used for download",
    type=click.Path(exists=True),
    metavar="<directory>")
@click.option(
    "-u", "--username",
    help="Your iCloud username or email address",
    metavar="<username>",
    prompt="iCloud username/email",
)
@click.option(
    "-p", "--password",
    help="Your iCloud password "
         "(default: use PyiCloud keyring or prompt for password)",
    metavar="<password>",
)
@click.option(
    "--cookie-directory",
    help="Directory to store cookies for authentication "
         "(default: ~/.pyicloud)",
    metavar="</cookie/directory>",
    default="~/.pyicloud",
)
@click.option(
    "--size",
    help="Image size to download (default: original)",
    type=click.Choice(["original", "medium", "thumb"]),
    default="original",
)
@click.option(
    "--live-photo-size",
    help="Live Photo video size to download (default: original)",
    type=click.Choice(["original", "medium", "thumb"]),
    default="original",
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
    "-a", "--album",
    help="Album to download (default: All Photos)",
    metavar="<album>",
    default="All Photos",
)
@click.option(
    "-l", "--list-albums",
    help="Lists the available albums",
    is_flag=True,
)
@click.option(
    "--skip-videos",
    help="Don't download any videos (default: Download all photos and videos)",
    is_flag=True,
)
@click.option(
    "--skip-live-photos",
    help="Don't download any live photos (default: Download live photos)",
    is_flag=True,
)
@click.option(
    "--force-size",
    help="Only download the requested size "
         + "(default: download original if size is not available)",
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
    "--folder-structure",
    help="Folder structure (default: {:%Y/%m/%d}). "
         "If set to 'none' all photos will just be placed into the download directory",
    metavar="<folder_structure>",
    default="{:%Y/%m/%d}",
)
@click.option(
    "--set-exif-datetime",
    help="Write the DateTimeOriginal exif tag from file creation date, " +
         "if it doesn't exist.",
    is_flag=True,
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
    "--notification-script",
    type=click.Path(),
    help="Runs an external script when two factor authentication expires. "
         "(path required: /path/to/my/script.sh)",
)
@click.option(
    "--log-level",
    help="Log level (default: debug)",
    type=click.Choice(["debug", "info", "error"]),
    default="debug",
)
@click.option("--no-progress-bar",
              help="Disables the one-line progress bar and prints log messages on separate lines "
                   "(Progress bar is disabled by default if there is no tty attached)",
              is_flag=True,
              )
@click.version_option()
# pylint: disable-msg=too-many-arguments,too-many-statements
# pylint: disable-msg=too-many-branches,too-many-locals
def main(
        directory,
        username,
        password,
        cookie_directory,
        size,
        live_photo_size,
        recent,
        until_found,
        album,
        list_albums,
        skip_videos,
        skip_live_photos,
        force_size,
        auto_delete,
        only_print_filenames,
        folder_structure,
        set_exif_datetime,
        smtp_username,
        smtp_password,
        smtp_host,
        smtp_port,
        smtp_no_tls,
        notification_email,
        log_level,
        no_progress_bar,
        notification_script, # pylint: disable=W0613
):
    """Download all iCloud photos to a local directory"""

    verify_options(list_albums, directory)

    icloud = authenticate_or_handle_2fa_required(
        username=username,
        password=password,
        cookie_directory=cookie_directory,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_no_tls=smtp_no_tls,
        notification_email=notification_email,
        notification_script=notification_script
    )

    logger = setup_and_configure_logger(
        only_print_filenames=only_print_filenames,
        log_level=log_level)

    local_library = FileSystemPhotosLibrary(base_directory=Path(directory))
    icloud_photos_library = ICloudPhotosLibrary(icloud=icloud)
    photos_downloader = ICloudPhotosDownloader(icloud=icloud,icloud_photos_library=icloud_photos_library, logger=logger)


    if list_albums:
        photos_downloader.list_albums()
        album_titles = [str(a) for a in albums]
        print(*album_titles, sep="\n")
    # else:
    # photos_downloader.download_and_save_album(
    #     album_name=album,
    #     photos_directory=photos_directory,
    #     skip_videos=skip_videos,
    #     recent=recent,
    #     until_found=until_found,
    #     size=size,
    #     only_print_filenames=only_print_filenames,
    #     no_progress_bar=no_progress_bar,
    #     force_size=force_size,
    #     set_exif_datetime=set_exif_datetime,
    #     skip_live_photos=skip_live_photos,
    #     live_photo_size=live_photo_size,
    #     delete_after_download=delete_after_download)

    if only_print_filenames:
        sys.exit(0)
    albums = photos_downloader.get_albums()
    for album in albums:
        handle_album(photos_downloader=photos_downloader, photos_directory=photos_directory, library_directory=library_directory, album=album, size=size,
                     photos_service=icloud.photos, logger=logger)

    logger.info("All photos have been downloaded!")

    if auto_delete:
        autodelete_photos(icloud, photos_directory)


def handle_album(photos_downloader, photos_directory, library_directory, album, size, logger, photos_service):
    try:
        album_dir = os.path.join(library_directory, album.name)
        todos = determine_todos(album_directory=album_dir, album=album, size=size)

        link_album(photos_directory=photos_directory, album_directory=album_dir,
                                     photos_to_link=todos.photos_to_link, size=size)

        delete_files(files=todos.photos_to_delete)
    except Exception as e:
        logger.error(e)
    try:
        if album.list_type == 'CPLContainerRelationLiveByAssetDate':
            sub_albums = collect_sub_albums(album, photos_service)
            upd_library_dir = os.path.join(library_directory, album.name)
            for sub_album in sub_albums:
                handle_album(photos_downloader=photos_downloader, photos_directory=photos_directory,
                             library_directory=upd_library_dir, album=sub_album, size=size, logger=logger,
                             photos_service=photos_service)
    except Exception as e:
        logger.error(e)

def determine_todos(album_directory, album, size):
    photos_to_link = []
    photos_to_delete = []


    photos_iterator = iter(album)
    local_photos = dict.fromkeys(filter(lambda p: p.is_file(), album_directory.iterdir()))
    
    for photo in photos_iterator:
        photos_directory_path = local_download_path(photo, size, album_directory)
        if photos_directory_path in local_photos:
            del local_photos[photos_directory_path]
        else:
            photos_to_link.append(photo)

    # all remaining local files are no longer part of the album
    photos_to_delete = local_photos
    
    return Todos(photos_to_link, photos_to_delete)

def delete_files(files):
    for file in files:
        file.unlink()

def link_album(photos_directory, album_directory, photos_to_link, size):
    # TODO: handle progress bar stuff -> see download method
    album_directory_exists = os.path.exists(album_directory)
    if not album_directory_exists:
        # Create a new directory because it does not exist
        os.makedirs(album_directory)
    register_heif_opener()

    photos_iterator = iter(photos_to_link)
    while True:
        try:
            item = next(photos_iterator)
            photo = item
            photos_directory_path = local_download_path(photo, size, photos_directory)

            img = Image.open(photos_directory_path)
            exif_dict_all = piexif.load(img.info["exif"])
            exif_dict = exif_dict_all["Exif"]
            if exif_dict is None:
                created_date = 'no_exif'
            elif piexif.ExifIFD.DateTimeOriginal in exif_dict:
                created_date = exif_dict[piexif.ExifIFD.DateTimeOriginal]
                created_date = created_date.decode('utf-8')
                # sanitize for windows ->  use ISO 8601 format, without separators
                created_date = created_date.replace(' ', 'T')
                created_date = created_date.replace(':', '_')
            else:
                created_date = ''

            # if photo.item_type_extension.lower() == 'heic':
            #     f = open(photos_directory_path, 'rb')
            #     tags = exifread.process_file(f)
            #     print(tags)

            library_name = filename_with_size(photo, size)
            if created_date:
                library_name = f"{created_date} - {library_name}"
            library_path = os.path.join(library_directory_path, library_name)

            link_exists = os.path.islink(library_path)
            if not link_exists:
                os.symlink(photos_directory_path, library_path)
        except StopIteration:
            break




def setup_and_configure_logger(only_print_filenames, log_level):
    logger = setup_logger()
    configure_logger(logger, only_print_filenames, log_level)
    return logger


def configure_logger(logger, only_print_filenames, log_level):
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


def verify_options(list_albums, directory):
    # check required directory param only if not list albums
    if not list_albums and not directory:
        print('--directory or --list-albums are required')
        sys.exit(2)


def authenticate_or_handle_2fa_required(
        username,
        password,
        cookie_directory,
        smtp_username,
        smtp_password,
        smtp_host,
        smtp_port,
        smtp_no_tls,
        notification_email,
        notification_script):
    raise_error_on_2sa = (
            smtp_username is not None
            or notification_email is not None
            or notification_script is not None
    )
    try:
        icloud = authenticate(
            username,
            password,
            cookie_directory,
            raise_error_on_2sa,
            client_id=os.environ.get("CLIENT_ID"),
        )
        return icloud
    except TwoStepAuthRequiredError:
        if notification_script is not None:
            subprocess.call([notification_script])
        if smtp_username is not None or notification_email is not None:
            send_2sa_notification(
                smtp_username,
                smtp_password,
                smtp_host,
                smtp_port,
                smtp_no_tls,
                notification_email,
            )
        sys.exit(1)
