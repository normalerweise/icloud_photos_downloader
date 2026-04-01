import abc
from datetime import datetime
from typing import Iterator, cast

from pyicloud_ipd.services.photos import PhotoAsset, PhotosService


class PhotosToSync(abc.ABC):
    @abc.abstractmethod
    def __iter__(self) -> Iterator[PhotoAsset]: ...

    @abc.abstractmethod
    def __len__(self) -> int: ...

    @property
    @abc.abstractmethod
    def covers_full_library(self) -> bool: ...


class RecentPhotosStrategy(PhotosToSync):
    covers_full_library = False

    def __init__(self, photos_service: PhotosService, count: int):
        self.photos_service = photos_service
        self.recent = count

    def __iter__(self) -> Iterator[PhotoAsset]:
        from itertools import islice

        # iCloud API returns photos newest-first (ASCENDING startRank, rank 0 = most recent)
        return islice(self.photos_service.all, self.recent)

    def __len__(self) -> int:
        # For recent photos, we know the exact count
        return self.recent


class SinceDateStrategy(PhotosToSync):
    covers_full_library = False

    def __init__(self, photos_service: PhotosService, since: datetime):
        self.photos_service = photos_service
        self.since = since
        self._cached_count: int | None = None

    def __iter__(self) -> Iterator[PhotoAsset]:
        # iCloud API returns photos newest-first (ASCENDING startRank, rank 0 = most recent)
        for p in self.photos_service.all:
            created = getattr(p, "created", None)
            since = self.since
            if isinstance(created, datetime) and isinstance(since, datetime):
                created_dt = cast(datetime, created)
                since_dt = cast(datetime, since)
                if created_dt >= since_dt:  # type: ignore[operator]
                    yield p

    def __len__(self) -> int:
        # Use total library count as upper-bound estimate (lightweight API call).
        # Exact filtered count is only known after iteration.
        if self._cached_count is None:
            self._cached_count = len(self.photos_service.all)
        return self._cached_count


class NoOpStrategy(PhotosToSync):
    covers_full_library = True

    def __init__(self, photos_service: PhotosService):
        # iCloud API returns photos newest-first (ASCENDING startRank, rank 0 = most recent)
        self.all_photos_album = photos_service.all

    def __iter__(self) -> Iterator[PhotoAsset]:
        return self.all_photos_album.__iter__()

    def __len__(self) -> int:
        return len(self.all_photos_album)
