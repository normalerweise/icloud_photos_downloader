import abc
from datetime import datetime
from typing import Iterator, cast, Optional

from pyicloud_ipd.services.photos import PhotoAsset, PhotosService


class PhotosToSync(abc.ABC):
    @abc.abstractmethod
    def __iter__(self) -> Iterator[PhotoAsset]: ...
    
    @abc.abstractmethod
    def __len__(self) -> int: ...


class RecentPhotosStrategy(PhotosToSync):
    def __init__(self, photos_service: PhotosService, count: int):
        self.photos_service = photos_service
        self.recent = count

    def __iter__(self) -> Iterator[PhotoAsset]:
        from itertools import islice

        return islice(self.photos_service.all(descending=True), self.recent)
    
    def __len__(self) -> int:
        # For recent photos, we know the exact count
        return self.recent


class SinceDateStrategy(PhotosToSync):
    def __init__(self, photos_service: PhotosService, since: datetime):
        self.photos_service = photos_service
        self.since = since
        self._cached_count: Optional[int] = None

    def __iter__(self) -> Iterator[PhotoAsset]:
        for p in self.photos_service.all(descending=True):
            created = getattr(p, "created", None)
            since = self.since
            if isinstance(created, datetime) and isinstance(since, datetime):
                created_dt = cast(datetime, created)
                since_dt = cast(datetime, since)
                if created_dt >= since_dt:  # type: ignore[operator]
                    yield p
    
    def __len__(self) -> int:
        # Cache the count to avoid multiple iterations
        if self._cached_count is None:
            self._cached_count = sum(1 for _ in self)
        return self._cached_count


class NoOpStrategy(PhotosToSync):
    def __init__(self, photos_service: PhotosService):
        self.all_photos_album = photos_service.all(descending=True)

    def __iter__(self) -> Iterator[PhotoAsset]:
        return self.all_photos_album.__iter__()
    
    def __len__(self) -> int:
       return len(self.all_photos_album)
