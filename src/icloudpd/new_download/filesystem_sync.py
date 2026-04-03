"""Phase 5: Sync database state to browsable filesystem via symlinks."""

import base64
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import ALBUMS_DIRECTORY, DATA_DIRECTORY, LIBRARY_DIRECTORY
from .database import (
    AlbumRecord,
    FolderRecord,
    ICloudAssetRecord,
    LocalFileRecord,
    PhotoDatabase,
)

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')


def _sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters with underscore."""
    sanitized = _UNSAFE_CHARS.sub("_", name)
    sanitized = sanitized.strip(". ")
    return sanitized or "_"


def _short_id(asset_id: str) -> str:
    """Return first 5 chars of base64-encoded asset_id for disambiguation."""
    return base64.urlsafe_b64encode(asset_id.encode()).decode()[:5]


def _parse_date(iso_date: str | None) -> datetime | None:
    """Parse an ISO 8601 date string, returning None on failure."""
    if not iso_date:
        return None
    try:
        return datetime.fromisoformat(iso_date)
    except (ValueError, TypeError):
        return None


class FilesystemSync:
    """Project database state onto the filesystem as symlink trees."""

    def __init__(self, base_directory: Path, database: PhotoDatabase):
        self.base_directory = Path(base_directory)
        self.database = database
        self.library_dir = self.base_directory / LIBRARY_DIRECTORY
        self.albums_dir = self.base_directory / ALBUMS_DIRECTORY
        self.data_dir = self.base_directory / DATA_DIRECTORY

    def sync_filesystem(self) -> dict[str, Any]:
        """Delta sync: converge Library/ and Albums/ to match DB state."""
        logger.info("Phase 5: Starting filesystem sync...")

        asset_data = self.database.get_all_downloaded_assets()
        albums = self.database.get_all_non_deleted_albums()
        folders = self.database.get_all_non_deleted_folders()

        desired_library = self._compute_library_links(asset_data)
        desired_albums = self._compute_album_links(asset_data, albums, folders)
        desired = {**desired_library, **desired_albums}

        existing = self._scan_existing_symlinks()

        stats = self._converge(desired, existing)

        self._prune_empty_dirs(self.library_dir)
        self._prune_empty_dirs(self.albums_dir)

        logger.info(
            f"Phase 5 completed: {stats['created']} created, "
            f"{stats['removed']} removed, {stats['updated']} updated, "
            f"{stats['unchanged']} unchanged"
        )
        return stats

    # -- Compute desired state --------------------------------------------------

    def _compute_library_links(
        self,
        asset_data: list[tuple[ICloudAssetRecord, list[LocalFileRecord]]],
    ) -> dict[Path, Path]:
        """Compute desired Library/YYYY/MM/name -> _data/file mappings."""
        buckets: dict[tuple[str, str], list[tuple[str, ICloudAssetRecord, LocalFileRecord]]] = (
            defaultdict(list)
        )

        for asset, local_files in asset_data:
            dt = _parse_date(asset.created_date) or _parse_date(asset.added_date)
            year = str(dt.year) if dt else "Unknown"
            month = f"{dt.month:02d}" if dt else "00"

            for local_file in local_files:
                name = self._compute_symlink_name(asset, local_file)
                buckets[(year, month)].append((name, asset, local_file))

        desired: dict[Path, Path] = {}
        for (year, month), entries in buckets.items():
            dir_path = self.library_dir / year / month
            resolved = self._disambiguate_collisions(entries)
            for final_name, _asset, local_file in resolved:
                symlink_path = dir_path / final_name
                target = self._relative_target(symlink_path, local_file.file_path)
                desired[symlink_path] = target

        return desired

    def _compute_album_links(
        self,
        asset_data: list[tuple[ICloudAssetRecord, list[LocalFileRecord]]],
        albums: list[AlbumRecord],
        folders: list[FolderRecord],
    ) -> dict[Path, Path]:
        """Compute desired Albums/[Folder/]Album/name -> _data/file mappings."""
        folder_map = {f.folder_id: f for f in folders}
        asset_map = {asset.asset_id: (asset, files) for asset, files in asset_data}

        desired: dict[Path, Path] = {}

        for album in albums:
            if album.album_type == "smart":
                continue

            folder_path = self._resolve_folder_path(album.folder_id, folder_map)
            album_dir = self.albums_dir / folder_path / _sanitize_filename(album.album_name)

            member_ids = self.database.get_album_assets(album.album_id)

            entries: list[tuple[str, ICloudAssetRecord, LocalFileRecord]] = []
            for asset_id in member_ids:
                if asset_id not in asset_map:
                    continue
                asset, local_files = asset_map[asset_id]
                for local_file in local_files:
                    name = self._compute_symlink_name(asset, local_file)
                    entries.append((name, asset, local_file))

            resolved = self._disambiguate_collisions(entries)
            for final_name, _asset, local_file in resolved:
                symlink_path = album_dir / final_name
                target = self._relative_target(symlink_path, local_file.file_path)
                desired[symlink_path] = target

        return desired

    # -- Scan existing state ----------------------------------------------------

    def _scan_existing_symlinks(self) -> dict[Path, Path]:
        """Walk Library/ and Albums/, collect all symlinks and their targets."""
        existing: dict[Path, Path] = {}
        for root_dir in (self.library_dir, self.albums_dir):
            if not root_dir.exists():
                continue
            for path in root_dir.rglob("*"):
                if path.is_symlink():
                    existing[path] = Path(os.readlink(path))
        return existing

    # -- Converge ---------------------------------------------------------------

    def _converge(
        self,
        desired: dict[Path, Path],
        existing: dict[Path, Path],
    ) -> dict[str, int]:
        """Create missing symlinks, remove stale ones, fix wrong targets."""
        created = 0
        removed = 0
        updated = 0
        unchanged = 0

        desired_keys = set(desired.keys())
        existing_keys = set(existing.keys())

        for path in existing_keys - desired_keys:
            path.unlink()
            removed += 1

        for path in desired_keys - existing_keys:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(desired[path])
            created += 1

        for path in desired_keys & existing_keys:
            if existing[path] != desired[path]:
                path.unlink()
                path.symlink_to(desired[path])
                updated += 1
            else:
                unchanged += 1

        return {
            "created": created,
            "removed": removed,
            "updated": updated,
            "unchanged": unchanged,
        }

    def _prune_empty_dirs(self, root: Path) -> None:
        """Bottom-up removal of empty directories."""
        if not root.exists():
            return
        for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
            path = Path(dirpath)
            if path == root:
                continue
            if not any(path.iterdir()):
                path.rmdir()

    # -- Helpers ----------------------------------------------------------------

    def _compute_symlink_name(
        self, asset: ICloudAssetRecord, local_file: LocalFileRecord
    ) -> str:
        """Compute symlink filename with datetime prefix and version suffix.

        Examples:
            20240115_143022_IMG_7409.JPG          (original)
            20240115_143022_IMG_7409-adjusted.JPG  (adjusted)
            20240115_143022_IMG_7409.MOV           (live_photo, extension from local file)
        """
        dt = _parse_date(asset.created_date) or _parse_date(asset.added_date)
        prefix = dt.strftime("%Y%m%d_%H%M%S") if dt else "00000000_000000"

        original_stem, original_ext = os.path.splitext(asset.filename)
        local_ext = os.path.splitext(local_file.local_filename)[1]

        version = local_file.version_type
        if version == "original":
            return f"{prefix}_{original_stem}{original_ext}"
        if version == "live_photo":
            return f"{prefix}_{original_stem}{local_ext}"
        return f"{prefix}_{original_stem}-{version}{original_ext}"

    def _relative_target(self, symlink_path: Path, data_file_relative: str) -> Path:
        """Compute relative symlink target from symlink location to data file."""
        target_absolute = self.base_directory / data_file_relative
        return Path(os.path.relpath(target_absolute, symlink_path.parent))

    def _resolve_folder_path(
        self, folder_id: str | None, folder_map: dict[str, FolderRecord]
    ) -> Path:
        """Walk parent chain to build relative path (e.g. Outer/Inner)."""
        if folder_id is None:
            return Path(".")

        parts: list[str] = []
        visited: set[str] = set()
        current = folder_id

        while current and current not in visited and len(parts) < 50:
            visited.add(current)
            folder = folder_map.get(current)
            if not folder:
                break
            parts.append(_sanitize_filename(folder.folder_name))
            current = folder.parent_folder_id

        parts.reverse()
        return Path(*parts) if parts else Path(".")

    def _disambiguate_collisions(
        self,
        entries: list[tuple[str, ICloudAssetRecord, LocalFileRecord]],
    ) -> list[tuple[str, ICloudAssetRecord, LocalFileRecord]]:
        """Detect duplicate names and append _XXXXX suffix to colliders.

        Sorted by asset_id for determinism. First occurrence keeps clean name.
        """
        entries.sort(key=lambda e: (e[0], e[1].asset_id))

        name_counts: dict[str, int] = defaultdict(int)
        for name, _, _ in entries:
            name_counts[name] += 1

        result: list[tuple[str, ICloudAssetRecord, LocalFileRecord]] = []
        seen: dict[str, int] = defaultdict(int)

        for name, asset, local_file in entries:
            if name_counts[name] <= 1:
                result.append((name, asset, local_file))
            elif seen[name] == 0:
                result.append((name, asset, local_file))
                seen[name] += 1
            else:
                stem, ext = os.path.splitext(name)
                suffix = _short_id(asset.asset_id)
                result.append((f"{stem}_{suffix}{ext}", asset, local_file))
                seen[name] += 1

        return result
