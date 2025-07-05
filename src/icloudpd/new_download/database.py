"""SQLite database operations for photo asset tracking."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from .constants import DATABASE_FILENAME

# TODO: Rework database columns to -> store asset_id in base64 to map file names more easily, store local file names per version explicitly, store version metadata from icloud explicitly, subtype -> e.g. live photo,.
@dataclass
class PhotoAssetRecord:
    asset_id: str
    filename: str
    asset_type: str | None
    created_date: str | None
    added_date: str | None
    width: int | None
    height: int | None
    location_latitude: float | None
    location_longitude: float | None
    location_altitude: float | None
    available_versions: List[str] = field(default_factory=list)
    downloaded_versions: List[str] = field(default_factory=list)
    failed_versions: List[str] = field(default_factory=list)
    last_sync_date: str | None = None
    master_record: Dict[str, Any] = field(default_factory=dict)
    asset_record: Dict[str, Any] = field(default_factory=dict)


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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS photo_assets (
                    asset_id TEXT PRIMARY KEY,
                    filename TEXT,
                    asset_type TEXT,
                    created_date DATETIME,
                    added_date DATETIME,
                    width INTEGER,
                    height INTEGER,
                    location_latitude REAL,
                    location_longitude REAL,
                    location_altitude REAL,
                    available_versions TEXT,
                    downloaded_versions TEXT,
                    failed_versions TEXT,
                    last_sync_date DATETIME,
                    master_record TEXT,
                    asset_record TEXT
                )
            """)
            conn.commit()
    
    def insert_asset(self, asset_data: PhotoAssetRecord) -> None:
        """Insert a new photo asset into the database.
        
        Args:
            asset_data: PhotoAssetRecord instance
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO photo_assets (
                    asset_id, filename, asset_type, created_date, added_date,
                    width, height, location_latitude, location_longitude, location_altitude,
                    available_versions, downloaded_versions, failed_versions,
                    last_sync_date, master_record, asset_record
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                asset_data.asset_id,
                asset_data.filename,
                asset_data.asset_type,
                asset_data.created_date,
                asset_data.added_date,
                asset_data.width,
                asset_data.height,
                asset_data.location_latitude,
                asset_data.location_longitude,
                asset_data.location_altitude,
                json.dumps(asset_data.available_versions),
                json.dumps(asset_data.downloaded_versions),
                json.dumps(asset_data.failed_versions),
                datetime.now().isoformat(),
                json.dumps(asset_data.master_record),
                json.dumps(asset_data.asset_record)
            ))
            conn.commit()
    
    def get_asset(self, asset_id: str) -> Optional[PhotoAssetRecord]:
        """Retrieve an asset from the database.
        
        Args:
            asset_id: The iCloud asset ID
            
        Returns:
            PhotoAssetRecord or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT * FROM photo_assets WHERE asset_id = ?
            """, (asset_id,))
            row = cursor.fetchone()
            
            if row is None:
                return None
            
            columns = [desc[0] for desc in cursor.description]
            asset_data = dict(zip(columns, row))
            
            # Parse JSON fields
            for field in ['available_versions', 'downloaded_versions', 'failed_versions', 'master_record', 'asset_record']:
                if asset_data[field]:
                    asset_data[field] = json.loads(asset_data[field])
                else:
                    asset_data[field] = [] if field.endswith('_versions') else {}
            
            return PhotoAssetRecord(**asset_data)
    
    def update_download_status(self, asset_id: str, downloaded_versions: List[str], failed_versions: List[str]) -> None:
        """Update download status for an asset.
        
        Args:
            asset_id: The iCloud asset ID
            downloaded_versions: List of successfully downloaded versions
            failed_versions: List of failed download versions
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE photo_assets 
                SET downloaded_versions = ?, failed_versions = ?, last_sync_date = ?
                WHERE asset_id = ?
            """, (
                json.dumps(downloaded_versions),
                json.dumps(failed_versions),
                datetime.now().isoformat(),
                asset_id
            ))
            conn.commit()
    
    def get_assets_needing_download(self) -> List[PhotoAssetRecord]:
        """Get all assets that need downloading (not fully downloaded).
        
        Returns:
            List of PhotoAssetRecord that need downloading
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT * FROM photo_assets 
                WHERE downloaded_versions != available_versions
                   OR downloaded_versions IS NULL
                   OR available_versions IS NULL
            """)
            
            assets = []
            for row in cursor.fetchall():
                columns = [desc[0] for desc in cursor.description]
                asset_data = dict(zip(columns, row))
                
                # Parse JSON fields
                for field in ['available_versions', 'downloaded_versions', 'failed_versions', 'master_record', 'asset_record']:
                    if asset_data[field]:
                        asset_data[field] = json.loads(asset_data[field])
                    else:
                        asset_data[field] = [] if field.endswith('_versions') else {}
                
                assets.append(PhotoAssetRecord(**asset_data))
            
            return assets
    
    def get_asset_count(self) -> int:
        """Get total number of assets in database.
        
        Returns:
            Total count of assets
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM photo_assets")
            return cursor.fetchone()[0]
    
    def get_downloaded_count(self) -> int:
        """Get count of fully downloaded assets.
        
        Returns:
            Count of fully downloaded assets
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM photo_assets 
                WHERE downloaded_versions = available_versions
                  AND downloaded_versions IS NOT NULL
            """)
            return cursor.fetchone()[0] 