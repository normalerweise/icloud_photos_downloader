"""Path functions"""
import os

def library_link_path(directory, folder_structure, created_date):
    """Returns the library directory path, considering the folder structure template"""
    try:
        if folder_structure.lower() == "none" or created_date is None:
            date_path = ""
        else:
            date_path = folder_structure.format(created_date)
    except ValueError:  # pragma: no cover
        # This error only seems to happen in Python 2
        self.logger.set_tqdm_description(
            f"Photo created date was not valid fir lib dir ({created_date})", logging.ERROR)
        date_path = ''
    
    library_link_path = os.path.normpath(os.path.join(directory, date_path))
    
    return library_link_path

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



