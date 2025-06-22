#!/usr/bin/env python
"""Directory management for dual hierarchy structure."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from .models import DirectoryStructure
from .types import DataPath, ErrorCode


class DirectoryManager:
    """Manages creation and validation of the dual hierarchy directory structure."""
    
    def __init__(self, structure: DirectoryStructure) -> None:
        self.structure = structure
    
    def create_directories(self) -> Union["Ok[None]", "Err[str]"]:
        """Create all required directories for the dual hierarchy structure.
        
        Returns:
            Ok(None) if successful, Err(error_message) if failed
        """
        try:
            # Create main directories
            directories_to_create = [
                self.structure.data_dir,
                self.structure.library_dir, 
                self.structure.timeline_dir,
                self.structure.deleted_dir,
            ]
            
            for directory in directories_to_create:
                path = Path(directory)
                path.mkdir(parents=True, exist_ok=True)
                
            return Ok(None)
            
        except OSError as e:
            return Err(f"Failed to create directories: {e}")
        except Exception as e:
            return Err(f"Unexpected error creating directories: {e}")
    
    def validate_directories(self) -> Union["Ok[None]", "Err[str]"]:
        """Validate that all required directories exist and are writable.
        
        Returns:
            Ok(None) if valid, Err(error_message) if validation failed
        """
        try:
            directories_to_check = [
                ("Data directory", self.structure.data_dir),
                ("Library directory", self.structure.library_dir),
                ("Timeline directory", self.structure.timeline_dir),
                ("Deleted directory", self.structure.deleted_dir),
            ]
            
            for name, directory in directories_to_check:
                path = Path(directory)
                
                if not path.exists():
                    return Err(f"{name} does not exist: {path}")
                
                if not path.is_dir():
                    return Err(f"{name} is not a directory: {path}")
                
                if not os.access(path, os.W_OK):
                    return Err(f"{name} is not writable: {path}")
                    
            return Ok(None)
            
        except Exception as e:
            return Err(f"Error validating directories: {e}")
    
    def ensure_structure(self) -> Union["Ok[DirectoryStructure]", "Err[str]"]:
        """Ensure directory structure exists and is valid.
        
        Returns:
            Ok(DirectoryStructure) if successful, Err(error_message) if failed
        """
        # Create directories
        create_result = self.create_directories()
        if isinstance(create_result, Err):
            return create_result
        
        # Validate directories
        validate_result = self.validate_directories()
        if isinstance(validate_result, Err):
            return validate_result
            
        return Ok(self.structure)


# Simple Result type implementation for this module
class Ok:
    """Success result."""
    def __init__(self, value):
        self.value = value

class Err:
    """Error result."""
    def __init__(self, error):
        self.error = error


def create_directory_structure(base_directory: DataPath) -> Union["Ok[DirectoryStructure]", "Err[str]"]:
    """Create and validate the dual hierarchy directory structure.
    
    Args:
        base_directory: Base directory path for the structure
        
    Returns:
        Ok(DirectoryStructure) if successful, Err(error_message) if failed
    """
    structure = DirectoryStructure.from_base(base_directory)
    manager = DirectoryManager(structure)
    return manager.ensure_structure()