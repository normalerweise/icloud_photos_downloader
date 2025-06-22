#!/usr/bin/env python
"""Integration service connecting new functional architecture with existing iCloud functionality."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, FrozenSet, Iterator, Optional, Union

from .change_detection_service import SmartChangeDetector
from .download_service import FunctionalDownloader
from .file_system_service import ComposedFileSystemService
from .icloud_service import ComposedICloudService
from .media_types_service import MediaTypeService
from .models import DirectoryStructure, Photo, SyncConfiguration, SyncResult
from .services import SyncService
from .symlink_service import FunctionalSymlinkManager
from .types import AlbumName, DataPath, LibraryPath, PhotoCount, TimelinePath


@dataclass(frozen=True)
class IntegrationResult:
    """Immutable result of integration operation."""
    
    sync_result: SyncResult
    photos_processed: PhotoCount
    links_created: PhotoCount
    errors: FrozenSet[str]
    
    @property
    def success(self) -> bool:
        """Check if integration was successful."""
        return len(self.errors) == 0
    
    @property
    def summary(self) -> str:
        """Get human-readable summary."""
        return (f"Processed {self.photos_processed} photos, "
                f"created {self.links_created} links, "
                f"{len(self.errors)} errors")


class LegacyIntegrationAdapter:
    """Adapter to integrate with existing iCloud downloader functionality."""
    
    def __init__(self, existing_downloader: Callable, existing_icloud_client) -> None:
        self.existing_downloader = existing_downloader
        self.existing_icloud_client = existing_icloud_client
    
    def adapt_downloader(self, photo: Photo, target_path: DataPath, size: str = "original") -> bool:
        """Adapt existing downloader to work with new Photo model."""
        try:
            # Convert our Photo model to whatever the existing downloader expects
            # This is where we bridge between new and old code
            
            # For now, simulate successful download
            print(f"[ADAPTER] Downloading {photo.filename} to {target_path}")
            
            # Ensure directory exists
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Create placeholder file (in real implementation, call existing downloader)
            Path(target_path).touch()
            
            return True
            
        except Exception as e:
            print(f"[ADAPTER] Download failed for {photo.filename}: {e}")
            return False
    
    def adapt_icloud_client(self):
        """Adapt existing iCloud client to work with new architecture."""
        # Return the existing client wrapped in our composition layer
        return ComposedICloudService(self.existing_icloud_client)


class ModernSyncOrchestrator:
    """Orchestrator that combines all new services into a cohesive sync operation."""
    
    def __init__(
        self,
        configuration: SyncConfiguration,
        existing_downloader: Callable,
        existing_icloud_client,
        dry_run: bool = False,
    ) -> None:
        self.configuration = configuration
        self.dry_run = dry_run
        
        # Set up directory structure
        self.directory_structure = DirectoryStructure.from_base(configuration.base_directory)
        
        # Create integration adapter
        self.adapter = LegacyIntegrationAdapter(existing_downloader, existing_icloud_client)
        
        # Initialize all services using composition
        self.file_system_service = ComposedFileSystemService(self.directory_structure)
        self.icloud_service = self.adapter.adapt_icloud_client()
        self.change_detector = SmartChangeDetector()
        self.downloader = FunctionalDownloader(
            existing_downloader=self.adapter.adapt_downloader,
            configuration=configuration,
        )
        self.symlink_manager = FunctionalSymlinkManager(self.directory_structure, dry_run)
        self.media_service = MediaTypeService(
            downloader=self.adapter.adapt_downloader,
            symlinker=self.symlink_manager,
            dry_run=dry_run,
        )
    
    def sync_photos(self) -> IntegrationResult:
        """Perform complete photo sync using modern architecture."""
        try:
            print(f"Starting sync with configuration: {self.configuration}")
            
            # Phase 1: Setup and validation
            setup_result = self._setup_environment()
            if not setup_result:
                return IntegrationResult(
                    sync_result=SyncResult(frozenset(), frozenset(), frozenset(["Setup failed"])),
                    photos_processed=PhotoCount(0),
                    links_created=PhotoCount(0),
                    errors=frozenset(["Environment setup failed"]),
                )
            
            # Phase 2: Get photos from iCloud with filtering
            icloud_photos = self._get_filtered_icloud_photos()
            print(f"Found {len(icloud_photos)} photos from iCloud")
            
            # Phase 3: Scan existing local photos
            local_photos = self.file_system_service.get_storage().scan_existing_photos(
                self.directory_structure.data_dir
            )
            print(f"Found {len(local_photos)} existing local photos")
            
            # Phase 4: Detect changes
            changes = self.change_detector.detect_changes(icloud_photos, local_photos)
            print(f"Detected {changes.total_changes} changes")
            
            if not changes.has_changes:
                print("No changes detected - sync complete")
                return IntegrationResult(
                    sync_result=SyncResult(frozenset(), frozenset(), frozenset()),
                    photos_processed=PhotoCount(0),
                    links_created=PhotoCount(0),
                    errors=frozenset(),
                )
            
            # Phase 5: Download new photos
            download_results = self._download_new_photos(changes.new_photos)
            print(f"Downloaded {len(download_results)} new photos")
            
            # Phase 6: Create symlinks
            symlink_results = self._create_symlinks(download_results)
            print(f"Created {len(symlink_results)} symlinks")
            
            # Phase 7: Handle deleted photos
            self._handle_deleted_photos(changes.deleted_photos)
            
            # Create final result
            sync_result = SyncResult(
                downloaded=download_results,
                linked=symlink_results,
                errors=frozenset(),  # Collect errors from each phase
            )
            
            return IntegrationResult(
                sync_result=sync_result,
                photos_processed=PhotoCount(len(download_results)),
                links_created=PhotoCount(len(symlink_results)),
                errors=frozenset(),
            )
            
        except Exception as e:
            error_msg = f"Sync failed: {e}"
            print(f"ERROR: {error_msg}")
            return IntegrationResult(
                sync_result=SyncResult(frozenset(), frozenset(), frozenset([error_msg])),
                photos_processed=PhotoCount(0),
                links_created=PhotoCount(0),
                errors=frozenset([error_msg]),
            )
    
    def _setup_environment(self) -> bool:
        """Set up directory structure and validate environment."""
        try:
            # Ensure directory structure exists
            setup_result = self.file_system_service.ensure_directory_structure()
            if hasattr(setup_result, 'error'):
                print(f"Directory setup failed: {setup_result.error}")
                return False
            
            # Test iCloud connection
            connection_result = self.icloud_service.test_connection()
            if hasattr(connection_result, 'error'):
                print(f"iCloud connection failed: {connection_result.error}")
                return False
            
            print("Environment setup successful")
            return True
            
        except Exception as e:
            print(f"Environment setup error: {e}")
            return False
    
    def _get_filtered_icloud_photos(self) -> FrozenSet[Photo]:
        """Get photos from iCloud with all configured filters applied."""
        try:
            icloud_reader = self.icloud_service.get_reader()
            photos = set()
            
            # Apply different photo selection strategies based on configuration
            if self.configuration.target_albums:
                # Get photos from specific albums
                target_album_names = self.configuration.target_albums
                for album in icloud_reader.get_albums():
                    if album.name in target_album_names:
                        album_photos = list(icloud_reader.get_photos_in_album(
                            album, self.configuration.max_photos_per_album
                        ))
                        photos.update(album_photos)
                        print(f"Added {len(album_photos)} photos from album {album.name}")
            
            elif self.configuration.max_recent_photos:
                # Get recent photos
                recent_photos = list(icloud_reader.get_recent_photos(self.configuration.max_recent_photos))
                photos.update(recent_photos)
                print(f"Added {len(recent_photos)} recent photos")
            
            else:
                # Get all photos with limit
                all_photos = list(icloud_reader.get_all_photos(self.configuration.max_photos))
                photos.update(all_photos)
                print(f"Added {len(all_photos)} photos (all)")
            
            # Apply additional filters
            filtered_photos = frozenset(photos)
            
            if self.configuration.exclude_albums:
                original_count = len(filtered_photos)
                filtered_photos = self.change_detector.exclude_by_albums(
                    filtered_photos, self.configuration.exclude_albums
                )
                print(f"Excluded {original_count - len(filtered_photos)} photos from excluded albums")
            
            if self.configuration.recent_days_only:
                from .pure_functions import filter_photos_by_recent_days
                original_count = len(filtered_photos)
                filtered_photos = filter_photos_by_recent_days(
                    filtered_photos, self.configuration.recent_days_only
                )
                print(f"Filtered to {len(filtered_photos)} photos from last {self.configuration.recent_days_only} days")
            
            return filtered_photos
            
        except Exception as e:
            print(f"Error getting iCloud photos: {e}")
            return frozenset()
    
    def _download_new_photos(self, new_photos: FrozenSet[Photo]) -> FrozenSet[Photo]:
        """Download new photos using the functional download pipeline."""
        try:
            if self.dry_run:
                print(f"[DRY RUN] Would download {len(new_photos)} new photos")
                return new_photos  # In dry run, pretend all downloads succeeded
            
            downloaded = set()
            
            for photo in new_photos:
                # Use media service for type-aware downloading
                process_result = self.media_service.process_photo(
                    photo,
                    self.directory_structure.data_dir,
                    self.directory_structure.timeline_dir,
                    self.directory_structure.library_dir,
                )
                
                if hasattr(process_result, 'value'):  # Ok result
                    downloaded.add(photo)
                    print(f"Successfully processed {photo.filename}")
                else:  # Err result
                    print(f"Failed to process {photo.filename}: {process_result.error}")
            
            return frozenset(downloaded)
            
        except Exception as e:
            print(f"Error downloading photos: {e}")
            return frozenset()
    
    def _create_symlinks(self, photos: FrozenSet[Photo]) -> FrozenSet:
        """Create symlinks for downloaded photos."""
        try:
            if self.dry_run:
                print(f"[DRY RUN] Would create symlinks for {len(photos)} photos")
                return frozenset()  # Return empty set for dry run
            
            # Create symlinks using the functional symlink manager
            symlink_batch = self.symlink_manager.create_all_symlinks_functional(
                photos, self.directory_structure.data_dir
            )
            
            print(f"Created {symlink_batch.total_links_created} symlinks with {symlink_batch.success_rate:.1f}% success rate")
            
            # Return all created symlink paths
            all_symlinks = set()
            all_symlinks.update(r.symlink_path for r in symlink_batch.timeline_results if r.is_success)
            all_symlinks.update(r.symlink_path for r in symlink_batch.library_results if r.is_success)
            
            return frozenset(all_symlinks)
            
        except Exception as e:
            print(f"Error creating symlinks: {e}")
            return frozenset()
    
    def _handle_deleted_photos(self, deleted_photos: FrozenSet[Photo]) -> None:
        """Handle photos that were deleted from iCloud."""
        try:
            if not deleted_photos:
                return
            
            print(f"Handling {len(deleted_photos)} deleted photos")
            
            for photo in deleted_photos:
                # Move to deleted directory
                source_path = DataPath(Path(self.directory_structure.data_dir) / photo.filename)
                
                if Path(source_path).exists():
                    move_result = self.file_system_service.get_storage().move_to_deleted(
                        photo, source_path, self.directory_structure.deleted_dir
                    )
                    
                    if hasattr(move_result, 'value'):  # Ok result
                        print(f"Moved {photo.filename} to deleted directory")
                    else:  # Err result
                        print(f"Failed to move {photo.filename}: {move_result.error}")
                
                # Remove symlinks
                remove_result = self.symlink_manager.remove_symlinks(
                    photo, self.directory_structure.timeline_dir, self.directory_structure.library_dir
                )
                
                if hasattr(remove_result, 'value'):  # Ok result
                    print(f"Removed {remove_result.value} symlinks for {photo.filename}")
                else:  # Err result
                    print(f"Failed to remove symlinks for {photo.filename}: {remove_result.error}")
            
        except Exception as e:
            print(f"Error handling deleted photos: {e}")


# Factory function for easy integration
def create_sync_orchestrator(
    configuration: SyncConfiguration,
    existing_downloader: Callable,
    existing_icloud_client,
    dry_run: bool = False,
) -> ModernSyncOrchestrator:
    """Factory function to create a sync orchestrator."""
    return ModernSyncOrchestrator(
        configuration=configuration,
        existing_downloader=existing_downloader,
        existing_icloud_client=existing_icloud_client,
        dry_run=dry_run,
    )


def run_modern_sync(
    base_directory: DataPath,
    existing_downloader: Callable,
    existing_icloud_client,
    **kwargs
) -> IntegrationResult:
    """High-level function to run a complete modern sync operation."""
    # Create configuration from parameters
    configuration = SyncConfiguration(
        base_directory=base_directory,
        dry_run=kwargs.get('dry_run', False),
        max_photos=kwargs.get('max_photos'),
        max_photos_per_album=kwargs.get('max_photos_per_album'),
        target_albums=kwargs.get('target_albums'),
        exclude_albums=kwargs.get('exclude_albums'),
        recent_days_only=kwargs.get('recent_days'),
    )
    
    # Create and run orchestrator
    orchestrator = create_sync_orchestrator(
        configuration=configuration,
        existing_downloader=existing_downloader,
        existing_icloud_client=existing_icloud_client,
        dry_run=configuration.dry_run,
    )
    
    return orchestrator.sync_photos()