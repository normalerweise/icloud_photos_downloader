from local_photos_library import LocalLibrary

class FileSystemPhotosLibrary(LocalLibrary):

    def __init__(self, base_directory: Path):
        self.base_directory = base_directory
        self.data_directory = directory_path / '_Data'
        self.library_directory = directory_path / 'Library'

    @abstractmethod
    def build_id(self, album, photo, size) -> str:
        pass
    
    @abstractmethod
    def exists(self, album, photo, size):
        pass

    @abstractmethod
    def get_saveable(self, album, photo, size)-> Saveable:
        pass

    @abstractmethod
    def list_existing_ids(album) -> list[str]:
        pass


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



class FileBasedSaveable(Saveable):

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.tmp_path = file_paths.parent / f"{file_path.name}.part"
        self.file_obj = open(tmp_path, "wb")


    @abstractmethod
    def add_chunk(chunk) -> Unit:
         self.file_obj.write(chunk)

    @abstractmethod
    def finish() -> Unit:
        self.file_obj.close()
        os.rename(self.temp_download_path, self.download_path)
        update_mtime(photo, self.download_path)