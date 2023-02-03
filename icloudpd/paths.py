"""Path functions"""
import os


def local_dowload_dir(directory, folder_structure, created_date):
    """Returns the download directory, considering the folder structure template"""
    try:
        if folder_structure.lower() == "none":
            date_path = ""
        else:
            date_path = folder_structure.format(created_date)
    except ValueError:  # pragma: no cover
        # This error only seems to happen in Python 2
        self.logger.set_tqdm_description(
            f"Photo created date was not valid ({created_date})", logging.ERROR)
        # e.g. ValueError: year=5 is before 1900
        # (https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues/122)
        # Just use the Unix epoch
        created_date = datetime.datetime.fromtimestamp(0)
        date_path = folder_structure.format(created_date)
    
    download_dir = os.path.normpath(os.path.join(directory, date_path))
    
    return download_dir


def local_download_path(media, size, download_dir):
    """Returns the full download path, including size"""
    filename = filename_with_size(media, size)
    download_path = os.path.join(download_dir, filename)
    return download_path


def filename_with_size(media, size):
    """Returns the filename with size, e.g. IMG1234.jpg, IMG1234-small.jpg"""
    # Strip any non-ascii characters.
    filename = media.filename.encode("utf-8").decode("ascii", "ignore")
    if size == 'original':
        return filename
    return (f"-{size}.").join(filename.rsplit(".", 1))
