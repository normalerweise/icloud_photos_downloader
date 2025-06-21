"""Handles file downloads with retries and error handling"""

import os
import socket
import time
import logging
from tzlocal import get_localzone
from requests.exceptions import ConnectionError  # pylint: disable=redefined-builtin
from pyicloud_ipd.exceptions import PyiCloudAPIResponseError
from icloudpd.logger import setup_logger

# Import the constants object so that we can mock WAIT_SECONDS in tests
from icloudpd import constants



class ICloudPhotosDownloader:
    def __init__(self, icloud,
    icloud_photos_library, local_photos_library, logger):
        self.icloud = icloud
        self.icloud_photos_library = icloud_photos_library
        self.local_photos_library = local_photos_library
        self.logger = logger

    def download_and_save_album(self, album_name, photos_directory, skip_videos, recent,
                                until_found, size, only_print_filenames, no_progress_bar,
                                force_size, set_exif_datetime,
                                skip_live_photos, live_photo_size, delete_after_download):

        album = self.icloud_photos_library.find_album(album_name)

        photos_count = len(album)

        # Optional: Only download the x most recent photos.
        if recent is not None:
            photos_count = recent
            album = itertools.islice(album, recent)

        tqdm_kwargs = {"total": photos_count}

        if until_found is not None:
            del tqdm_kwargs["total"]
            photos_count = "???"
            # ensure photos iterator doesn't have a known length
            album = (p for p in album)

        plural_suffix = "" if photos_count == 1 else "s"
        video_suffix = ""
        photos_count_str = "the first" if photos_count == 1 else photos_count
        if not skip_videos:
            video_suffix = " or video" if photos_count == 1 else " and videos"
        self.logger.info(
            "Downloading %s %s photo%s%s to %s ...",
            photos_count_str,
            size,
            plural_suffix,
            video_suffix,
            photos_directory,
        )

        # Use only ASCII characters in progress bar
        tqdm_kwargs["ascii"] = True

        # Skip the one-line progress bar if we're only printing the filenames,
        # or if the progress bar is explicitly disabled,
        # or if this is not a terminal (e.g. cron or piping output to file)
        if not os.environ.get("FORCE_TQDM") and (
                only_print_filenames or no_progress_bar or not sys.stdout.isatty()
        ):
            photos_enumerator = album
            self.logger.set_tqdm(None)
        else:
            photos_enumerator = tqdm(album, **tqdm_kwargs)
            self.logger.set_tqdm(photos_enumerator)

        consecutive_files_found = Counter(0)

        should_break = self.__build_should_break(until_found)
        download_photo = self.__build_download_photo(
            skip_videos=skip_videos,
            photos_directory=photos_directory,
            size=size,
            force_size=force_size,
            only_print_filenames=only_print_filenames,
            set_exif_datetime=set_exif_datetime,
            skip_live_photos=skip_live_photos,
            live_photo_size=live_photo_size)

        photos_iterator = iter(photos_enumerator)
        while True:
            try:
                if should_break(consecutive_files_found):
                    self.logger.tqdm_write(
                        f"Found {until_found} consecutive previously downloaded photos. Exiting"
                    )
                    break
                photo = next(photos_iterator)
                download_photo(consecutive_files_found, album, photo)
                # if delete_after_download:
                # TODO: kill we don´t want to delete by accident
                # delete_photo(item)
            except StopIteration:
                break



    def __build_should_break(self, until_found):
        def should_break(counter):
            """Exit if until_found condition is reached"""
            return until_found is not None and counter.value() >= until_found

        return should_break

    def __build_download_photo(
            self, skip_videos, photos_directory, size,
            force_size, only_print_filenames, set_exif_datetime, skip_live_photos, live_photo_size):
        def download_photo(counter, album, photo):
            """internal function for actually downloading the photos"""
            if skip_videos and photo.item_type != "image":
                self.logger.set_tqdm_description(
                    f"Skipping {photo.filename}, only downloading photos."
                )
                return
            if photo.item_type not in ("image", "movie"):
                self.logger.set_tqdm_description(
                    f"Skipping {photo.filename}, only downloading photos and videos. "
                    f"(Item type was: {photo.item_type})"
                )
                return
            try:
                created_date = photo.created.astimezone(get_localzone())
            except (ValueError, OSError):
                self.logger.set_tqdm_description(
                    f"Could not convert photo created date to local timezone ({photo.created})",
                    logging.ERROR)
                created_date = photo.created

            # disabled folder_structure here as we create the library structure
            download_size = size

            try:
                versions = photo.versions
            except KeyError as ex:
                print(
                    f"KeyError: {ex} attribute was not found in the photo fields!"
                )
                with open(file='icloudpd-photo-error.json', mode='w', encoding='utf8') as outfile:
                    # pylint: disable=protected-access
                    json.dump({
                        "master_record": photo._master_record,
                        "asset_record": photo._asset_record
                    }, outfile)
                    # pylint: enable=protected-access
                print("icloudpd has saved the photo record to: "
                      "./icloudpd-photo-error.json")
                print("Please create a Gist with the contents of this file: "
                      "https://gist.github.com")
                print(
                    "Then create an issue on GitHub: "
                    "https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues")
                print(
                    "Include a link to the Gist in your issue, so that we can "
                    "see what went wrong.\n")
                return

            if size not in versions and size != "original":
                if force_size:
                    filename = photo.filename.encode(
                        "utf-8").decode("ascii", "ignore")
                    self.logger.set_tqdm_description(
                        f"{size} size does not exist for {filename}. Skipping...", logging.ERROR, )
                    return
                download_size = "original"

            exists_locally = self.local_photos_library.exists(album, photo, download_size)

            if not exists_locally:
                counter.reset()
                if only_print_filenames:
                    # TODO: proper relative file name
                    print(self.local_photos_library.build_id(album, photo, download_size))
                else:
                    truncated_path = truncate_middle(download_path, 96)
                    self.logger.set_tqdm_description(
                        f"Downloading {truncated_path}"
                    )

                    download_result = download.download_media(
                        self.icloud, self.local_photos_library, album, photo, download_size
                    )

                    if download_result:
                        if set_exif_datetime and photo.filename.lower().endswith(
                                (".jpg", ".jpeg")) and not exif_datetime.get_photo_exif(download_path):
                            # %Y:%m:%d looks wrong, but it's the correct format
                            date_str = created_date.strftime(
                                "%Y-%m-%d %H:%M:%S%z")
                            self.logger.debug(
                                "Setting EXIF timestamp for %s: %s",
                                download_path,
                                date_str,
                            )
                            exif_datetime.set_photo_exif(
                                download_path,
                                created_date.strftime("%Y:%m:%d %H:%M:%S"),
                            )
                        download.set_utime(download_path, created_date)

            # Also download the live photo if present
            if not skip_live_photos:
                lp_size = live_photo_size + "Video"
                if lp_size in photo.versions:
                    version = photo.versions[lp_size]
                    filename = version["filename"]
                    if live_photo_size != "original":
                        # Add size to filename if not original
                        filename = filename.replace(
                            ".MOV", f"-{live_photo_size}.MOV"
                        )
                    lp_download_path = os.path.join(photos_directory, filename)

                    lp_file_exists = os.path.isfile(lp_download_path)

                    if only_print_filenames and not lp_file_exists:
                        print(lp_download_path)
                    else:
                        if lp_file_exists:
                            lp_file_size = os.stat(lp_download_path).st_size
                            lp_photo_size = version["size"]
                            if lp_file_size != lp_photo_size:
                                lp_download_path = (f"-{lp_photo_size}.").join(
                                    lp_download_path.rsplit(".", 1)
                                )
                                self.logger.set_tqdm_description(
                                    f"{truncate_middle(lp_download_path, 96)} deduplicated."
                                )
                                lp_file_exists = os.path.isfile(
                                    lp_download_path)
                            if lp_file_exists:
                                self.logger.set_tqdm_description(
                                    f"{truncate_middle(lp_download_path, 96)} already exists."

                                )
                        if not lp_file_exists:
                            truncated_path = truncate_middle(
                                lp_download_path, 96)
                            self.logger.set_tqdm_description(
                                f"Downloading {truncated_path}")
                            download.download_media(
                                self.icloud, photo, lp_download_path, lp_size
                            )

        return download_photo





def update_mtime(photo, download_path):
    """Set the modification time of the downloaded file to the photo creation date"""
    if photo.created:
        created_date = None
        try:
            created_date = photo.created.astimezone(
                get_localzone())
        except (ValueError, OSError):
            # We already show the timezone conversion error in base.py,
            # when generating the download directory.
            # So just return silently without touching the mtime.
            return
        set_utime(download_path, created_date)

def set_utime(download_path, created_date):
    """Set date & time of the file"""
    ctime = time.mktime(created_date.timetuple())
    os.utime(download_path, (ctime, ctime))

def download_media(icloud, local_photos_library, album, photo, size):
    """Download the photo to path, with retries and error handling"""
    logger = setup_logger()

    for retries in range(constants.MAX_RETRIES):
        try:
            photo_response = photo.download(size)
            if photo_response:
                saveable = local_photos_library.get_saveable(album, photo, size)
                temp_download_path = download_path + ".part"
                with open(temp_download_path, "wb") as file_obj:
                    for chunk in photo_response.iter_content(chunk_size=1024):
                        if chunk:
                            saveable.write(chunk)
                saveable.finish()
                return True

            logger.tqdm_write(
                f"Could not find URL to download {photo.filename} for size {size}!",
                logging.ERROR,
            )
            break

        except (ConnectionError, socket.timeout, PyiCloudAPIResponseError) as ex:
            if "Invalid global session" in str(ex):
                logger.tqdm_write(
                    "Session error, re-authenticating...",
                    logging.ERROR)
                if retries > 0:
                    # If the first re-authentication attempt failed,
                    # start waiting a few seconds before retrying in case
                    # there are some issues with the Apple servers
                    time.sleep(constants.WAIT_SECONDS)

                icloud.authenticate()
            else:
                # you end up here when p.e. throttling by Apple happens
                wait_time = (retries + 1) * constants.WAIT_SECONDS
                logger.tqdm_write(
                    f"Error downloading {photo.filename}, retrying after {wait_time} seconds...",
                    logging.ERROR,
                )
                time.sleep(wait_time)

        except IOError:
            logger.error(
                "IOError while writing file to %s! "
                "You might have run out of disk space, or the file "
                "might be too large for your OS. "
                "Skipping this file...", download_path
            )
            break
    else:
        logger.tqdm_write(
            f"Could not download {photo.filename}! Please try again later."
        )

    return False
