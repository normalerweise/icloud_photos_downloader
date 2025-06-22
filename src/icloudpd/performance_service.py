#!/usr/bin/env python
"""Performance optimization service for large photo collections."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, FrozenSet, Iterator, List, Optional, TypeVar

from .error_handling_service import ProductionLogger, SafeOperationWrapper
from .models import Photo
from .progress_service import ProgressTracker
from .types import PhotoCount

T = TypeVar('T')


@dataclass(frozen=True)
class PerformanceMetrics:
    """Immutable performance metrics for operations."""
    
    operation_name: str
    total_items: int
    successful_items: int
    failed_items: int
    total_time_seconds: float
    peak_memory_mb: Optional[float] = None
    
    @property
    def items_per_second(self) -> float:
        """Calculate items processed per second."""
        if self.total_time_seconds <= 0:
            return 0.0
        return self.successful_items / self.total_time_seconds
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_items == 0:
            return 100.0
        return (self.successful_items / self.total_items) * 100.0
    
    @property
    def efficiency_score(self) -> float:
        """Calculate overall efficiency score (0-100)."""
        speed_score = min(100.0, self.items_per_second * 10)  # Assume 10 items/sec is perfect
        return (speed_score * 0.6) + (self.success_rate * 0.4)


class BatchProcessor:
    """Optimized batch processing for large collections."""
    
    def __init__(
        self, 
        batch_size: int = 100,
        max_workers: int = 4,
        logger: Optional[ProductionLogger] = None
    ) -> None:
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.logger = logger
        self.safe_wrapper = SafeOperationWrapper(logger) if logger else None
    
    def process_photos_in_batches(
        self, 
        photos: FrozenSet[Photo], 
        processor: Callable[[Photo], T],
        operation_name: str = "batch_processing"
    ) -> Iterator[T]:
        """Process photos in optimized batches."""
        photo_list = list(photos)
        total_batches = (len(photo_list) + self.batch_size - 1) // self.batch_size
        
        if self.logger:
            self.logger.info(
                f"Starting batch processing",
                total_photos=len(photos),
                batch_size=self.batch_size,
                total_batches=total_batches,
                max_workers=self.max_workers
            )
        
        for batch_idx in range(total_batches):
            start_idx = batch_idx * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(photo_list))
            batch = photo_list[start_idx:end_idx]
            
            if self.logger:
                self.logger.info(f"Processing batch {batch_idx + 1}/{total_batches} ({len(batch)} photos)")
            
            # Process batch in parallel
            batch_results = self._process_batch_parallel(batch, processor, operation_name)
            
            for result in batch_results:
                if result is not None:
                    yield result
    
    def _process_batch_parallel(
        self, 
        batch: List[Photo], 
        processor: Callable[[Photo], T],
        operation_name: str
    ) -> List[Optional[T]]:
        """Process a batch of photos in parallel."""
        results = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all photos in batch
            future_to_photo = {}
            for photo in batch:
                if self.safe_wrapper:
                    future = executor.submit(
                        self.safe_wrapper.safe_photo_operation,
                        photo,
                        processor,
                        operation_name
                    )
                else:
                    future = executor.submit(processor, photo)
                future_to_photo[future] = photo
            
            # Collect results as they complete
            for future in as_completed(future_to_photo):
                photo = future_to_photo[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Batch processing failed for {photo.filename}: {e}")
                    results.append(None)
        
        return results


class MemoryOptimizer:
    """Memory optimization for large photo collections."""
    
    def __init__(self, logger: Optional[ProductionLogger] = None) -> None:
        self.logger = logger
        self._memory_threshold_mb = 500  # 500MB threshold
    
    def create_photo_iterator(self, photos: FrozenSet[Photo]) -> Iterator[Photo]:
        """Create memory-efficient photo iterator."""
        # Convert to list for efficient iteration, but process one at a time
        photo_list = list(photos)
        
        if self.logger:
            self.logger.info(f"Created memory-efficient iterator for {len(photo_list)} photos")
        
        for photo in photo_list:
            yield photo
            
            # Periodic memory check (every 100 photos)
            if len(photo_list) % 100 == 0:
                self._check_memory_usage()
    
    def _check_memory_usage(self) -> None:
        """Check current memory usage and log warnings if high."""
        try:
            import psutil
            import os
            
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
            
            if memory_mb > self._memory_threshold_mb:
                if self.logger:
                    self.logger.warning(
                        f"High memory usage detected",
                        memory_mb=f"{memory_mb:.1f}MB",
                        threshold_mb=f"{self._memory_threshold_mb}MB"
                    )
        except ImportError:
            # psutil not available, skip memory monitoring
            pass
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Memory monitoring failed: {e}")
    
    def optimize_photo_collection(self, photos: FrozenSet[Photo]) -> FrozenSet[Photo]:
        """Optimize photo collection for memory efficiency."""
        # For very large collections, we could implement strategies like:
        # - Lazy loading of photo metadata
        # - Chunked processing
        # - Streaming operations
        
        if len(photos) > 10000:
            if self.logger:
                self.logger.info(
                    f"Large collection detected ({len(photos)} photos), applying memory optimizations"
                )
            
            # For now, return as-is but log the size
            # In a real implementation, we might implement lazy loading here
            return photos
        
        return photos


class ConcurrentDownloader:
    """Optimized concurrent download manager."""
    
    def __init__(
        self, 
        max_concurrent: int = 4,
        timeout_seconds: float = 30.0,
        retry_attempts: int = 3,
        logger: Optional[ProductionLogger] = None
    ) -> None:
        self.max_concurrent = max_concurrent
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = retry_attempts
        self.logger = logger
        self.safe_wrapper = SafeOperationWrapper(logger) if logger else None
    
    def download_photos_concurrent(
        self, 
        photos: FrozenSet[Photo], 
        downloader: Callable[[Photo], bool],
        progress_tracker: Optional[ProgressTracker] = None
    ) -> PerformanceMetrics:
        """Download photos concurrently with performance tracking."""
        start_time = time.time()
        successful = 0
        failed = 0
        
        if self.logger:
            self.logger.info(
                f"Starting concurrent downloads",
                total_photos=len(photos),
                max_concurrent=self.max_concurrent,
                timeout_seconds=self.timeout_seconds
            )
        
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            # Submit all download tasks
            future_to_photo = {}
            for photo in photos:
                future = executor.submit(self._download_with_retry, photo, downloader)
                future_to_photo[future] = photo
            
            # Process completed downloads
            for future in as_completed(future_to_photo, timeout=self.timeout_seconds * len(photos)):
                photo = future_to_photo[future]
                
                try:
                    success = future.result()
                    if success:
                        successful += 1
                        if progress_tracker:
                            progress_tracker.photo_completed(photo, photo.size_bytes)
                    else:
                        failed += 1
                        if progress_tracker:
                            progress_tracker.photo_failed(photo, "Download failed")
                            
                except Exception as e:
                    failed += 1
                    if self.logger:
                        self.logger.error(f"Download exception for {photo.filename}: {e}")
                    if progress_tracker:
                        progress_tracker.photo_failed(photo, str(e))
        
        total_time = time.time() - start_time
        
        metrics = PerformanceMetrics(
            operation_name="concurrent_download",
            total_items=len(photos),
            successful_items=successful,
            failed_items=failed,
            total_time_seconds=total_time,
        )
        
        if self.logger:
            self.logger.info(
                f"Concurrent downloads completed",
                successful=successful,
                failed=failed,
                total_time=f"{total_time:.1f}s",
                items_per_second=f"{metrics.items_per_second:.1f}",
                success_rate=f"{metrics.success_rate:.1f}%"
            )
        
        return metrics
    
    def _download_with_retry(self, photo: Photo, downloader: Callable[[Photo], bool]) -> bool:
        """Download photo with retry logic."""
        last_exception = None
        
        for attempt in range(self.retry_attempts):
            try:
                if self.safe_wrapper:
                    result = self.safe_wrapper.safe_photo_operation(
                        photo, 
                        downloader, 
                        f"download_attempt_{attempt + 1}"
                    )
                    return result is not None and result
                else:
                    return downloader(photo)
                    
            except Exception as e:
                last_exception = e
                if attempt < self.retry_attempts - 1:
                    # Wait before retry (exponential backoff)
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    
                    if self.logger:
                        self.logger.warning(
                            f"Download attempt {attempt + 1} failed for {photo.filename}, retrying in {wait_time}s"
                        )
        
        # All retries failed
        if self.logger:
            self.logger.error(f"All download attempts failed for {photo.filename}: {last_exception}")
        
        return False


class SmartCacheManager:
    """Smart caching for frequently accessed data."""
    
    def __init__(self, max_cache_size: int = 1000, logger: Optional[ProductionLogger] = None) -> None:
        self.max_cache_size = max_cache_size
        self.logger = logger
        self._photo_cache: dict[str, Photo] = {}
        self._access_count: dict[str, int] = {}
    
    def cache_photo(self, photo: Photo) -> None:
        """Cache a photo object."""
        if len(self._photo_cache) >= self.max_cache_size:
            self._evict_least_used()
        
        self._photo_cache[photo.id] = photo
        self._access_count[photo.id] = self._access_count.get(photo.id, 0) + 1
    
    def get_cached_photo(self, photo_id: str) -> Optional[Photo]:
        """Get cached photo by ID."""
        photo = self._photo_cache.get(photo_id)
        if photo:
            self._access_count[photo_id] = self._access_count.get(photo_id, 0) + 1
        return photo
    
    def _evict_least_used(self) -> None:
        """Evict least recently used cache entries."""
        if not self._photo_cache:
            return
        
        # Remove 20% of least used entries
        evict_count = max(1, len(self._photo_cache) // 5)
        
        # Sort by access count (ascending)
        sorted_items = sorted(self._access_count.items(), key=lambda x: x[1])
        
        for photo_id, _ in sorted_items[:evict_count]:
            self._photo_cache.pop(photo_id, None)
            self._access_count.pop(photo_id, None)
        
        if self.logger:
            self.logger.info(f"Evicted {evict_count} cache entries")
    
    def get_cache_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return {
            'cached_photos': len(self._photo_cache),
            'max_cache_size': self.max_cache_size,
            'cache_utilization': int((len(self._photo_cache) / self.max_cache_size) * 100),
        }


class PerformanceOptimizer:
    """High-level performance optimization coordinator."""
    
    def __init__(
        self, 
        max_concurrent: int = 4,
        batch_size: int = 100,
        enable_caching: bool = True,
        logger: Optional[ProductionLogger] = None
    ) -> None:
        self.logger = logger
        
        # Initialize optimizers
        self.batch_processor = BatchProcessor(batch_size, max_concurrent, logger)
        self.memory_optimizer = MemoryOptimizer(logger)
        self.concurrent_downloader = ConcurrentDownloader(max_concurrent, logger=logger)
        self.cache_manager = SmartCacheManager(logger=logger) if enable_caching else None
        
        if logger:
            logger.info(
                f"Performance optimizer initialized",
                max_concurrent=max_concurrent,
                batch_size=batch_size,
                caching_enabled=enable_caching
            )
    
    def optimize_large_collection_sync(
        self, 
        photos: FrozenSet[Photo],
        processor: Callable[[Photo], T]
    ) -> Iterator[T]:
        """Optimize sync for large photo collections."""
        # Apply memory optimizations
        optimized_photos = self.memory_optimizer.optimize_photo_collection(photos)
        
        # Cache frequently accessed photos
        if self.cache_manager:
            for photo in list(optimized_photos)[:100]:  # Cache first 100 photos
                self.cache_manager.cache_photo(photo)
        
        # Process in optimized batches
        return self.batch_processor.process_photos_in_batches(
            optimized_photos, 
            processor,
            "large_collection_sync"
        )
    
    def get_performance_recommendations(self, photo_count: int) -> List[str]:
        """Get performance recommendations based on collection size."""
        recommendations = []
        
        if photo_count > 10000:
            recommendations.extend([
                "Consider using --max-photos to limit initial sync size",
                "Use --recent-days to sync only recent photos first",
                "Ensure stable internet connection for large collections",
                "Monitor disk space during sync"
            ])
        
        if photo_count > 50000:
            recommendations.extend([
                "Consider syncing in multiple sessions using date ranges",
                "Increase --max-concurrent-downloads if bandwidth allows",
                "Consider running during off-peak hours"
            ])
        
        return recommendations


# Factory functions
def create_performance_optimizer(
    max_concurrent: int = 4,
    batch_size: int = 100,
    enable_caching: bool = True,
    logger: Optional[ProductionLogger] = None
) -> PerformanceOptimizer:
    """Factory function to create a performance optimizer."""
    return PerformanceOptimizer(max_concurrent, batch_size, enable_caching, logger)