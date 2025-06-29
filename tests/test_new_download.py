"""Tests for the new download architecture."""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest

from src.icloudpd.new_download.database import PhotoDatabase
from src.icloudpd.new_download.file_manager import FileManager
from src.icloudpd.new_download.asset_processor import AssetProcessor
from src.icloudpd.new_download.sync_manager import SyncManager


class TestPhotoDatabase:
    """Test database operations."""
    
    def setup_method(self):
        """Set up test database."""
        self.temp_dir = tempfile.mkdtemp()
        self.db = PhotoDatabase(Path(self.temp_dir))
    
    def teardown_method(self):
        """Clean up test database."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_insert_and_get_asset(self):
        """Test inserting and retrieving an asset."""
        asset_data = {
            'asset_id': 'test123',
            'filename': 'test.jpg',
            'asset_type': 'photo',
            'created_date': '2024-01-01T00:00:00',
            'added_date': '2024-01-01T00:00:00',
            'width': 1920,
            'height': 1080,
            'available_versions': ['original', 'adjusted'],
            'downloaded_versions': [],
            'failed_versions': [],
            'master_record': {'test': 'data'},
            'asset_record': {'test': 'data'}
        }
        
        self.db.insert_asset(asset_data)
        retrieved = self.db.get_asset('test123')
        
        assert retrieved is not None
        assert retrieved['asset_id'] == 'test123'
        assert retrieved['filename'] == 'test.jpg'
        assert retrieved['available_versions'] == ['original', 'adjusted']
    
    def test_update_download_status(self):
        """Test updating download status."""
        asset_data = {
            'asset_id': 'test123',
            'filename': 'test.jpg',
            'asset_type': 'photo',
            'available_versions': ['original', 'adjusted'],
            'downloaded_versions': [],
            'failed_versions': [],
            'master_record': {},
            'asset_record': {}
        }
        
        self.db.insert_asset(asset_data)
        self.db.update_download_status('test123', ['original'], ['adjusted'])
        
        retrieved = self.db.get_asset('test123')
        assert retrieved['downloaded_versions'] == ['original']
        assert retrieved['failed_versions'] == ['adjusted']


class TestFileManager:
    """Test file operations."""
    
    def setup_method(self):
        """Set up test file manager."""
        self.temp_dir = tempfile.mkdtemp()
        self.file_manager = FileManager(Path(self.temp_dir))
    
    def teardown_method(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_get_file_path(self):
        """Test file path generation."""
        path = self.file_manager.get_file_path('test123', 'original', 'test.jpg')
        expected = Path(self.temp_dir) / '_data' / 'test123-original.jpg'
        assert path == expected
    
    def test_save_and_check_file(self):
        """Test saving and checking file existence."""
        content = b'test file content'
        success = self.file_manager.save_file('test123', 'original', 'test.jpg', content)
        
        assert success is True
        assert self.file_manager.file_exists('test123', 'original', 'test.jpg') is True
    
    def test_list_downloaded_files(self):
        """Test listing downloaded files."""
        # Create some test files
        self.file_manager.save_file('test123', 'original', 'test.jpg', b'content1')
        self.file_manager.save_file('test123', 'adjusted', 'test.jpg', b'content2')
        
        downloaded = self.file_manager.list_downloaded_files('test123')
        assert 'original' in downloaded
        assert 'adjusted' in downloaded


class TestAssetProcessor:
    """Test asset processing."""
    
    def setup_method(self):
        """Set up test asset processor."""
        self.temp_dir = tempfile.mkdtemp()
        self.db = PhotoDatabase(Path(self.temp_dir))
        self.processor = AssetProcessor(self.db)
    
    def teardown_method(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_process_asset(self):
        """Test processing a mock asset."""
        # Create a mock asset
        mock_asset = Mock()
        mock_asset.id = 'test123'
        mock_asset.filename = 'test.jpg'
        mock_asset.created = '2024-01-01T00:00:00'
        mock_asset.added = '2024-01-01T00:00:00'
        mock_asset.dimensions = (1920, 1080)
        mock_asset.location = None
        mock_asset.versions = []
        mock_asset.master_record = {'test': 'data'}
        mock_asset.asset_record = {'test': 'data'}
        
        processed = self.processor.process_asset(mock_asset)
        
        assert processed['asset_id'] == 'test123'
        assert processed['filename'] == 'test.jpg'
        assert processed['asset_type'] == 'photo'
        assert processed['width'] == 1920
        assert processed['height'] == 1080


class TestSyncManager:
    """Test sync manager."""
    
    def setup_method(self):
        """Set up test sync manager."""
        self.temp_dir = tempfile.mkdtemp()
        self.sync_manager = SyncManager(Path(self.temp_dir))
    
    def teardown_method(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self.temp_dir)
        self.sync_manager.cleanup()
    
    def test_get_sync_stats(self):
        """Test getting sync statistics."""
        stats = self.sync_manager._get_sync_stats()
        
        assert 'total_assets' in stats
        assert 'downloaded_assets' in stats
        assert 'failed_assets' in stats
        assert 'disk_usage_bytes' in stats
        assert stats['total_assets'] == 0
        assert stats['downloaded_assets'] == 0 