"""Asset processing and filtering logic."""

import base64
import logging
from typing import List, Dict, Any

from pyicloud_ipd.services.photos import PhotoAsset
from pyicloud_ipd.version_size import VersionSize

from .database import ICloudAssetRecord, AssetVersionMetadata, SyncStatus

logger = logging.getLogger(__name__)


class PhotoAssetRecordMapper:
    """Maps a PhotoAsset to separate database records for the new architecture."""

    @staticmethod
    def _encode_asset_id(asset_id: str) -> str:
        """Encode asset_id as URL-safe base64."""
        return base64.urlsafe_b64encode(asset_id.encode()).decode().rstrip("=")

    @staticmethod
    def map_icloud_metadata(asset: PhotoAsset) -> ICloudAssetRecord:
        """Map PhotoAsset to ICloudAssetMetadata."""
        # Extract asset versions as dictionary with version_size as key
        asset_versions = {}
        
        for version_size in asset.versions:
            version_data = asset.versions[version_size]
            asset_versions[version_size.value] = {
                "filename": version_data.filename,
                "size": version_data.size,
                "url": version_data.url,
                "type": version_data.type,
                "file_extension": version_data.file_extension
            }
        
        return ICloudAssetRecord(
            asset_id=asset.id,
            filename=asset.filename,
            asset_type=asset.item_type.value if asset.item_type else None,
            created_date=asset.created.isoformat() if asset.created else None,
            added_date=asset.added_date.isoformat() if asset.added_date else None,
            width=asset.dimensions[0] if asset.dimensions else None,
            height=asset.dimensions[1] if asset.dimensions else None,
            asset_subtype=PhotoAssetRecordMapper._determine_subtype(asset),
            asset_versions=asset_versions,
            master_record=asset._master_record,
            asset_record=asset._asset_record,
        )

    @staticmethod
    def map_asset_versions(asset: PhotoAsset) -> List[AssetVersionMetadata]:
        """Map PhotoAsset versions to AssetVersionMetadata list."""
        versions = []
        
        for version_size in asset.versions:
            version_data = asset.versions[version_size]
            versions.append(AssetVersionMetadata(
                asset_id=asset.id,
                version_type=version_size.value,
                version_size=version_size.value,
                file_extension=version_data.file_extension.lower(),
                file_size=version_data.size,  # AssetVersion has 'size' not 'file_size'
                checksum=None,  # TODO: Calculate checksum if needed
                download_url=None,  # Will be set when downloading
            ))
        
        return versions

    @staticmethod
    def map_sync_statuses(asset: PhotoAsset) -> List[SyncStatus]:
        """Map PhotoAsset to initial SyncStatus for each version."""
        statuses = []
        for version_size in asset.versions:
            statuses.append(SyncStatus(
                asset_id=asset.id,
                version_type=version_size.value,
                sync_status="pending",
                retry_count=0,
            ))
        return statuses

    @staticmethod
    def _determine_subtype(asset: PhotoAsset) -> str | None:
        """Determine asset subtype based on iCloud metadata."""
        # Check for live photo - look for video companion in versions
        if any('VidCompl' in str(v) for v in asset.versions.keys()):
            return "live_photo"
        
        # Check for burst - look in asset record fields
        asset_fields = asset._asset_record.get('fields', {})
        if 'burstId' in asset_fields and asset_fields['burstId'].get('value'):
            return "burst"
        
        # Check for HDR - look in asset record fields
        if 'assetHDRType' in asset_fields and asset_fields['assetHDRType'].get('value'):
            return "hdr"
        
        # Check for panorama - look in asset record fields
        if 'assetSubtype' in asset_fields:
            subtype_value = asset_fields['assetSubtype'].get('value')
            if subtype_value == 3:  # Panorama subtype
                return "panorama"
        
        return None

    # Legacy compatibility method
    @staticmethod
    def map(asset: PhotoAsset) -> dict:
        """Legacy method that returns combined data in old format."""
        metadata = PhotoAssetRecordMapper.map_icloud_metadata(asset)
        versions = PhotoAssetRecordMapper.map_asset_versions(asset)
        sync_statuses = PhotoAssetRecordMapper.map_sync_statuses(asset)
        
        # Convert to old format for compatibility
        available_versions = [f"{v.version_type}_{v.version_size}" for v in versions]
        
        # Determine overall sync status from individual statuses
        overall_status = "pending"
        if sync_statuses:
            # If any status is failed, overall is failed
            if any(s.sync_status == "failed" for s in sync_statuses):
                overall_status = "failed"
            # If all are completed, overall is completed
            elif all(s.sync_status == "completed" for s in sync_statuses):
                overall_status = "completed"
            # If any are downloading, overall is downloading
            elif any(s.sync_status == "downloading" for s in sync_statuses):
                overall_status = "downloading"
            # If any are metadata_processed, overall is metadata_processed
            elif any(s.sync_status == "metadata_processed" for s in sync_statuses):
                overall_status = "metadata_processed"

        return {
            "asset_id": metadata.asset_id,
            "filename": metadata.filename,
            "asset_type": metadata.asset_type,
            "created_date": metadata.created_date,
            "added_date": metadata.added_date,
            "width": metadata.width,
            "height": metadata.height,
            "available_versions": available_versions,
            "downloaded_versions": [],
            "failed_versions": [],
            "sync_status": overall_status,
            "master_record": metadata.master_record,
            "asset_record": metadata.asset_record,
        }
