from datetime import datetime
from typing import Iterator, cast
import abc
from pyicloud_ipd.services.photos import PhotosService, PhotoAsset

class PhotoFilterStrategy(abc.ABC):
    @abc.abstractmethod
    def __iter__(self) -> Iterator[PhotoAsset]:
        ...

class RecentPhotosStrategy(PhotoFilterStrategy):
    def __init__(self, photos_service: PhotosService, count: int):
        self.photos_service = photos_service
        self.recent = count
    def __iter__(self) -> Iterator[PhotoAsset]:
        from itertools import islice
        return islice(self.photos_service.all(descending=True), self.recent)

class SinceDateStrategy(PhotoFilterStrategy):
    def __init__(self, photos_service: PhotosService, since: datetime):
        self.photos_service = photos_service
        self.since = since
    def __iter__(self) -> Iterator[PhotoAsset]:
        for p in self.photos_service.all(descending=True):
            created = getattr(p, 'created', None)
            since = self.since
            if isinstance(created, datetime) and isinstance(since, datetime):
                created_dt = cast(datetime, created)
                since_dt = cast(datetime, since)
                if created_dt >= since_dt:  # type: ignore[operator]
                    yield p

class NoOpStrategy(PhotoFilterStrategy):
    def __init__(self, photos_service: PhotosService):
        self.photos_service = photos_service
    def __iter__(self) -> Iterator[PhotoAsset]:
        return iter(self.photos_service.all(descending=True)) 