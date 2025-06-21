from abc import ABC, abstractmethod

class LocalPhotosLibrary(ABC):

    @abstractmethod
    def get_album(album) -> LocalAlbum:
        pass


class LocalAlbum(ABC):

    @abstractmethod
    def get_sub_album(album) -> LocalAlbum:
        pass

    @abstractmethod
    def build_id(self, photo, size) -> str:
        pass
    
    @abstractmethod
    def exists(self, photo, size):
        pass

    @abstractmethod
    def get_saveable(self, photo, size)-> Saveable:
        pass

    @abstractmethod
    def list_existing_ids() -> list[str]:
        pass


class Saveable(ABC):

    @abstractmethod
    def write(chunk) -> Unit:
        pass

    @abstractmethod
    def finish() -> Unit:
        pass