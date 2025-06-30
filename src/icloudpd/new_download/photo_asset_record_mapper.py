"""Asset processing and filtering logic."""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Iterator
from pathlib import Path

from .constants import DOWNLOAD_VERSIONS
from .database import PhotoDatabase, PhotoAssetRecord
from pyicloud_ipd.services.photos import PhotoAsset

logger = logging.getLogger(__name__)


class PhotoAssetRecordMapper:
    """Maps a PhotoAsset to a PhotoAssetRecord for database storage."""
    @staticmethod
    def map(asset: PhotoAsset) -> PhotoAssetRecord:
        return PhotoAssetRecord(
            asset_id=asset.id,
            filename=asset.filename,
            asset_type=asset.item_type.value if asset.item_type else None,
            created_date=asset.created.isoformat() if asset.created else None,
            added_date=asset.added_date.isoformat() if asset.added_date else None,
            width=asset.dimensions[0] if asset.dimensions else None,
            height=asset.dimensions[1] if asset.dimensions else None,
            location_latitude=None, # TODO: later -> not contained in icloud metadata? read from exif data later?
            location_longitude=None, # TODO: later -> not contained in icloud metadata? read from exif data later?
            location_altitude=None, # TODO: later -> not contained in icloud metadata? read from exif data later?
            available_versions=PhotoAssetRecordMapper._get_available_versions(asset),
            downloaded_versions=[],
            failed_versions=[],
            master_record=asset._master_record,
            asset_record=asset._asset_record,
        )

    @staticmethod
    def merge(record: PhotoAssetRecord, asset: PhotoAsset) -> PhotoAssetRecord:
        return PhotoAssetRecord(
            asset_id=asset.id,
            filename=asset.filename,
            asset_type=asset.item_type.value if asset.item_type else None,
            created_date=asset.created.isoformat() if asset.created else None,
            added_date=asset.added_date.isoformat() if asset.added_date else None,
            width=asset.dimensions[0] if asset.dimensions else None,
            height=asset.dimensions[1] if asset.dimensions else None,
            location_latitude=record.location_latitude,
            location_longitude=record.location_longitude,
            location_altitude=record.location_altitude,
            available_versions=PhotoAssetRecordMapper._get_available_versions(asset),
            downloaded_versions=record.downloaded_versions,
            failed_versions=record.failed_versions,
            master_record=asset._master_record,
            asset_record=asset._asset_record
        )



    @staticmethod
    def _get_available_versions(asset: PhotoAsset) -> List[str]:
        available_versions = [version_size.value for version_size in asset.versions]
        return available_versions
