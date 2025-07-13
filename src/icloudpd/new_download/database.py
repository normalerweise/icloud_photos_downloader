"""SQLite database operations for photo asset tracking."""

import base64
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .constants import DATABASE_FILENAME


@dataclass
class ICloudAssetRecord:
    """iCloud metadata for an asset (remote information)."""
    asset_id: str  # Original iCloud asset ID
    filename: str  # Original filename from iCloud
    asset_type: Optional[str] = None  # e.g., "image", "movie"
    asset_subtype: Optional[str] = None  # e.g., "live_photo", "burst", "hdr"
    created_date: Optional[str] = None
    added_date: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    asset_versions: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # Dict with version_size as key, AssetVersion object as value
    master_record: Dict[str, Any] = field(default_factory=dict)
    asset_record: Dict[str, Any] = field(default_factory=dict)
    last_metadata_update: Optional[str] = None


@dataclass
class AssetVersionMetadata:
    """Metadata for a specific asset version."""
    asset_id: str
    version_type: str  # e.g., "original", "adjusted", "alternative"
    version_size: str  # e.g., "original", "medium", "thumb"
    file_extension: str
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    download_url: Optional[str] = None


@dataclass
class LocalFileRecord:
    """Local file system information for downloaded files."""
    asset_id: str
    version_type: str  # e.g., "original", "adjusted", "alternative"
    local_filename: str  # Actual filename on disk
    file_path: str  # Full path relative to base directory
    file_size: int
    download_date: str
    checksum: Optional[str] = None


@dataclass
class SyncStatus:
    """Sync status and progress tracking per file."""
    asset_id: str
    version_type: str  # e.g., "original", "adjusted", "alternative"
    sync_status: str = "pending"  # pending, metadata_processed, downloading, completed, failed
    last_sync_date: Optional[str] = None
    retry_count: int = 0
    error_message: Optional[str] = None


class PhotoDatabase:
    """SQLite database for tracking photo assets and download status."""

    def __init__(self, base_directory: Path):
        """Initialize database connection.

        Args:
            base_directory: Base directory where database will be stored
        """
        self.base_directory = Path(base_directory)
        self.db_path = self.base_directory / DATABASE_FILENAME
        self._init_database()

    def _init_database(self) -> None:
        """Initialize database and create tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            # iCloud metadata table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS icloud_assets (
                    asset_id TEXT PRIMARY KEY,
                    filename TEXT,
                    asset_type TEXT,
                    created_date DATETIME,
                    added_date DATETIME,
                    width INTEGER,
                    height INTEGER,
                    asset_subtype TEXT,
                    asset_versions TEXT,
                    master_record TEXT,
                    asset_record TEXT,
                    last_metadata_update DATETIME
                )
            """)

            # Asset version metadata table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS asset_versions (
                    asset_id TEXT,
                    version_type TEXT,
                    version_size TEXT,
                    file_extension TEXT,
                    file_size INTEGER,
                    checksum TEXT,
                    download_url TEXT,
                    PRIMARY KEY (asset_id, version_type, version_size),
                    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
                )
            """)

            # Local files table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_files (
                    asset_id TEXT,
                    version_type TEXT,
                    local_filename TEXT,
                    file_path TEXT,
                    file_size INTEGER,
                    download_date DATETIME,
                    checksum TEXT,
                    PRIMARY KEY (asset_id, version_type),
                    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
                )
            """)

            # Sync status table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_status (
                    asset_id TEXT,
                    version_type TEXT,
                    sync_status TEXT DEFAULT 'pending',
                    last_sync_date DATETIME,
                    retry_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    PRIMARY KEY (asset_id, version_type),
                    FOREIGN KEY (asset_id) REFERENCES icloud_assets(asset_id)
                )
            """)

            # Create indexes for better performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_status ON sync_status(sync_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_local_files_asset_id ON local_files(asset_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_versions_asset_id ON asset_versions(asset_id)")

            conn.commit()

    def _encode_asset_id(self, asset_id: str) -> str:
        """Encode asset_id as URL-safe base64."""
        return base64.urlsafe_b64encode(asset_id.encode()).decode().rstrip("=")

    def insert_icloud_metadata(self, metadata: ICloudAssetRecord) -> None:
        """Insert or update iCloud metadata for an asset.

        Args:
            metadata: ICloudAssetMetadata instance
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO icloud_assets (
                    asset_id, filename, asset_type, created_date, added_date,
                    width, height, asset_subtype, asset_versions,
                    master_record, asset_record, last_metadata_update
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    metadata.asset_id,
                    metadata.filename,
                    metadata.asset_type,
                    metadata.created_date,
                    metadata.added_date,
                    metadata.width,
                    metadata.height,
                    metadata.asset_subtype,
                    json.dumps(metadata.asset_versions),
                    json.dumps(metadata.master_record),
                    json.dumps(metadata.asset_record),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()

    def insert_asset_version(self, version: AssetVersionMetadata) -> None:
        """Insert or update asset version metadata.

        Args:
            version: AssetVersionMetadata instance
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO asset_versions (
                    asset_id, version_type, version_size, file_extension, file_size,
                    checksum, download_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    version.asset_id,
                    version.version_type,
                    version.version_size,
                    version.file_extension,
                    version.file_size,
                    version.checksum,
                    version.download_url,
                ),
            )
            conn.commit()

    def insert_local_file(self, local_file: LocalFileRecord) -> None:
        """Insert or update local file record.

        Args:
            local_file: LocalFileRecord instance
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO local_files (
                    asset_id, version_type, local_filename, file_path, file_size,
                    download_date, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    local_file.asset_id,
                    local_file.version_type,
                    local_file.local_filename,
                    local_file.file_path,
                    local_file.file_size,
                    local_file.download_date,
                    local_file.checksum,
                ),
            )
            conn.commit()

    def update_sync_status(self, sync_status: SyncStatus) -> None:
        """Update sync status for an asset.

        Args:
            sync_status: SyncStatus instance
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sync_status (
                    asset_id, version_type, sync_status, last_sync_date, retry_count, error_message
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    sync_status.asset_id,
                    sync_status.version_type,
                    sync_status.sync_status,
                    datetime.now().isoformat(),
                    sync_status.retry_count,
                    sync_status.error_message,
                ),
            )
            conn.commit()

    def get_icloud_metadata(self, asset_id: str) -> Optional[ICloudAssetRecord]:
        """Get iCloud metadata for an asset.

        Args:
            asset_id: The iCloud asset ID

        Returns:
            ICloudAssetMetadata or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM icloud_assets WHERE asset_id = ?",
                (asset_id,),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            columns = [desc[0] for desc in cursor.description]
            data = dict(zip(columns, row, strict=False))

            # Parse JSON fields
            for field in ["asset_versions", "master_record", "asset_record"]:
                if data[field]:
                    data[field] = json.loads(data[field])
                else:
                    data[field] = {}

            return ICloudAssetRecord(**data)

    def get_asset_versions(self, asset_id: str) -> List[AssetVersionMetadata]:
        """Get all versions for an asset.

        Args:
            asset_id: The iCloud asset ID

        Returns:
            List of AssetVersionMetadata
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ?",
                (asset_id,),
            )

            versions = []
            for row in cursor.fetchall():
                columns = [desc[0] for desc in cursor.description]
                data = dict(zip(columns, row, strict=False))
                versions.append(AssetVersionMetadata(**data))

            return versions

    def get_local_files(self, asset_id: str) -> List[LocalFileRecord]:
        """Get all local files for an asset.

        Args:
            asset_id: The iCloud asset ID

        Returns:
            List of LocalFileRecord
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM local_files WHERE asset_id = ?",
                (asset_id,),
            )

            files = []
            for row in cursor.fetchall():
                columns = [desc[0] for desc in cursor.description]
                data = dict(zip(columns, row, strict=False))
                files.append(LocalFileRecord(**data))

            return files

    def get_sync_status(self, asset_id: str, version_type: str) -> Optional[SyncStatus]:
        """Get sync status for a specific asset version.

        Args:
            asset_id: The iCloud asset ID
            version_type: The version type (e.g., "original", "adjusted")

        Returns:
            SyncStatus or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM sync_status WHERE asset_id = ? AND version_type = ?",
                (asset_id, version_type),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            columns = [desc[0] for desc in cursor.description]
            data = dict(zip(columns, row, strict=False))
            return SyncStatus(**data)

    def get_all_sync_statuses(self, asset_id: str) -> List[SyncStatus]:
        """Get all sync statuses for an asset.

        Args:
            asset_id: The iCloud asset ID

        Returns:
            List of SyncStatus for all versions of the asset
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM sync_status WHERE asset_id = ?",
                (asset_id,),
            )

            statuses = []
            for row in cursor.fetchall():
                columns = [desc[0] for desc in cursor.description]
                data = dict(zip(columns, row, strict=False))
                statuses.append(SyncStatus(**data))

            return statuses

    def get_assets_needing_metadata_sync(self) -> List[str]:
        """Get asset IDs that need metadata processing.

        Returns:
            List of asset IDs that need metadata sync
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT asset_id FROM sync_status 
                WHERE sync_status IN ('pending', 'failed')
                   OR sync_status IS NULL
            """)
            return [row[0] for row in cursor.fetchall()]

    def get_assets_needing_download(self) -> List[str]:
        """Get asset IDs that need downloading.

        Returns:
            List of asset IDs that need downloading
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT av.asset_id 
                FROM asset_versions av
                LEFT JOIN local_files lf ON av.asset_id = lf.asset_id 
                    AND av.version_type = lf.version_type
                LEFT JOIN sync_status ss ON av.asset_id = ss.asset_id 
                    AND av.version_type = ss.version_type
                WHERE lf.asset_id IS NULL
                  AND ss.sync_status = 'metadata_processed'
            """)
            return [row[0] for row in cursor.fetchall()]

    def get_asset_count(self) -> int:
        """Get total number of assets in database.

        Returns:
            Total count of assets
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM icloud_assets")
            return cursor.fetchone()[0]

    def get_downloaded_count(self) -> int:
        """Get count of fully downloaded assets.

        Returns:
            Count of fully downloaded assets
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(DISTINCT asset_id) FROM local_files
            """)
            return cursor.fetchone()[0]

    def get_metadata_processed_count(self) -> int:
        """Get count of assets with metadata processed.

        Returns:
            Count of assets with metadata processed
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(DISTINCT asset_id) FROM sync_status 
                WHERE sync_status = 'metadata_processed'
            """)
            return cursor.fetchone()[0]

    def get_pending_download_count(self) -> int:
        """Get count of assets pending download.

        Returns:
            Count of assets pending download
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(DISTINCT av.asset_id) 
                FROM asset_versions av
                LEFT JOIN local_files lf ON av.asset_id = lf.asset_id 
                    AND av.version_type = lf.version_type
                LEFT JOIN sync_status ss ON av.asset_id = ss.asset_id 
                    AND av.version_type = ss.version_type
                WHERE lf.asset_id IS NULL
                  AND ss.sync_status = 'metadata_processed'
            """)
            return cursor.fetchone()[0]

    # Legacy compatibility methods for transition
    def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """Legacy method to get asset data in old format.

        Args:
            asset_id: The iCloud asset ID

        Returns:
            Dictionary with combined asset data or None
        """
        metadata = self.get_icloud_metadata(asset_id)
        if not metadata:
            return None

        versions = self.get_asset_versions(asset_id)
        local_files = self.get_local_files(asset_id)
        sync_statuses = self.get_all_sync_statuses(asset_id)

        # Convert to old format for compatibility
        available_versions = list(metadata.asset_versions.keys())
        downloaded_versions = [f"{f.version_type}" for f in local_files]
        failed_versions = []  # Failed downloads are now tracked in SyncStatus

        # Determine overall sync status from individual statuses
        overall_status = "pending"
        last_sync_date = None
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
            
            # Get the most recent sync date
            sync_dates = [s.last_sync_date for s in sync_statuses if s.last_sync_date]
            if sync_dates:
                last_sync_date = max(sync_dates)

        return {
            "asset_id": metadata.asset_id,
            "filename": metadata.filename,
            "asset_type": metadata.asset_type,
            "created_date": metadata.created_date,
            "added_date": metadata.added_date,
            "width": metadata.width,
            "height": metadata.height,
            "available_versions": available_versions,
            "downloaded_versions": downloaded_versions,
            "failed_versions": failed_versions,
            "sync_status": overall_status,
            "last_sync_date": last_sync_date,
            "master_record": metadata.master_record,
            "asset_record": metadata.asset_record,
        }
