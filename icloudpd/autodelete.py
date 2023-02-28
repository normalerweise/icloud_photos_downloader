"""
Delete any files found in "Recently Deleted"
"""
import os
from icloudpd.logger import setup_logger
from icloudpd.paths import local_download_path


def autodelete_photos(icloud, directory):
    """
    Scans the "Recently Deleted" folder and deletes any matching files
    from the download directory.
    (I.e. If you delete a photo on your phone, it's also deleted on your computer.)
    """
    logger = setup_logger()
    logger.info("Deleting any files found in 'Recently Deleted'...")

    recently_deleted = icloud.photos.albums["Recently Deleted"]

    for media in recently_deleted:
        created_date = media.created
        # disabled folder_structure as we are building the Library structure
        download_dir = directory

        for size in [None, "original", "medium", "thumb"]:
            path = os.path.normpath(
                local_download_path(
                    media, size, download_dir))
            if os.path.exists(path):
                logger.info("Deleting %s!", path)
                os.remove(path)
