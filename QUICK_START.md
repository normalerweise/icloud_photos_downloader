# Quick Start Guide - iCloud Photos Downloader

*Get up and running with the modern architecture in 15 minutes*

## 🚀 Quick Setup

### 1. **Modern Environment Setup with uv**
```bash
# Clone and navigate to project
cd icloud_photos_downloader

# Option A: Automated setup (recommended)
./scripts/uv_setup

# Option B: Manual setup
# Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies and dev tools
uv sync --all-extras

# Option C: Legacy pip setup (not recommended)
# pip install -r requirements.txt
```

### 2. **Run the Test Suite**
```bash
# Test Phase 3: Functional Integration
uv run python test_phase3.py

# Test Phase 4: Immutable State Management  
uv run python test_phase4.py

# Test Phase 5: Production Features
uv run python test_phase5_integration.py

# Run full pytest suite
uv run pytest
```

### 3. **Basic Usage Example**
```python
from src.icloudpd.models import SyncConfiguration
from src.icloudpd.types import DataPath, AlbumName, PhotoCount
from src.icloudpd.integration_service import run_modern_sync
from pathlib import Path

# Configure sync operation
config = SyncConfiguration(
    base_directory=DataPath(Path("/your/photos/directory")),
    recent_days_only=7,           # Only photos from last 7 days
    max_photos=PhotoCount(50),    # Limit to 50 photos for testing
    target_albums=frozenset([AlbumName("Recent"), AlbumName("Vacation")]),
    dry_run=True                  # Safe testing mode
)

# Run sync (with mock iCloud client for testing)
result = run_modern_sync(
    config.base_directory,
    mock_downloader,
    mock_icloud_client,
    **config.__dict__
)

print(f"Sync completed: {result.summary}")
```

## 🏗️ Architecture at a Glance

### **Core Principle: Everything is Immutable and Type-Safe**

```python
# ✅ Modern approach: Domain types + immutable data
@dataclass(frozen=True)
class Photo:
    id: PhotoId                    # Not str - PhotoId type
    filename: Filename             # Not str - Filename type  
    creation_date: CreationDate    # Not datetime - CreationDate type
    albums: FrozenSet[AlbumName]   # Immutable set

# ✅ Pure functions with clear contracts
def calculate_timeline_path(photo: Photo, base: TimelinePath) -> TimelinePath:
    # Same inputs = same outputs, no side effects
    
# ✅ Result types instead of exceptions for control flow
def download_photo(photo: Photo) -> Result[DataPath, str]:
    # Returns either Ok(path) or Err(error_message)
```

### **Directory Structure Created**
```
/your/photos/directory/
├── _Data/                 # 📁 Actual photo files (flat structure)
│   ├── IMG_1234.jpg
│   ├── IMG_1235.heic
│   └── IMG_1235.mov      # Live Photo video component
├── Timeline/             # 📅 Date-based symlinks  
│   ├── 2024/
│   │   ├── 01/           # January 2024
│   │   └── 03/           # March 2024
│   └── 2023/
└── Library/              # 📚 Album-based symlinks
    ├── All Photos/
    ├── Vacation 2024/
    └── Family/
```

## 🔧 Common Development Patterns

### **1. Creating New Services**
```python
# Follow the composition pattern
class MyNewService:
    def __init__(self, 
                 dependency: SomeDependencyProtocol,  # Use protocols, not inheritance
                 logger: ProductionLogger):           # Always include logging
        self.dependency = dependency
        self.safe_wrapper = SafeOperationWrapper(logger)  # Automatic error handling
    
    def my_operation(self, photo: Photo) -> Optional[MyResult]:
        """Use safe wrapper for all operations that might fail."""
        return self.safe_wrapper.safe_photo_operation(
            photo, 
            self._do_operation,
            "my_operation",
            recovery_suggestion="Try clearing cache and retry"
        )
    
    def _do_operation(self, photo: Photo) -> MyResult:
        # Pure business logic here
        pass
```

### **2. Adding New Types**
```python
# Always use domain-specific types in types.py
MyNewId = NewType("MyNewId", str)
MyConfigValue = NewType("MyConfigValue", int)

# Add to enums for controlled vocabularies  
class MyNewCategory(Enum):
    OPTION_A = "option_a"
    OPTION_B = "option_b"
```

### **3. Working with Results**
```python
# Pattern for handling Result types
result = some_operation_that_returns_result()

if hasattr(result, 'value'):  # Ok result
    success_value = result.value
    # Continue with success path
else:  # Err result  
    error_message = result.error
    # Handle error appropriately
```

### **4. Testing with Immutable Data**
```python
def test_my_feature():
    # Create immutable test data
    test_photo = Photo(
        id=PhotoId("test_photo"),
        filename=Filename("test.jpg"),
        creation_date=CreationDate(datetime(2024, 1, 15)),
        modification_date=ModificationDate(datetime(2024, 1, 15)),
        size_bytes=FileSizeBytes(1024000),
        format=PhotoFormat.JPEG,
        photo_type=PhotoType.STANDARD,
        albums=frozenset([AlbumName("Test Album")])
    )
    
    # Test with pure functions
    result = my_pure_function(test_photo)
    
    # Assert on immutable results
    assert result.photo_count == PhotoCount(1)
    assert result.success == True
```

## 🔍 Debugging Guide

### **Enable Detailed Logging**
```python
from src.icloudpd.error_handling_service import create_production_logger

# Create logger with debug level
logger = create_production_logger("debug_session", "debug")

# See all operations and their results
logger.info("Starting debug session")
# ... your code
error_summary = logger.get_error_summary()
print(error_summary.summary)
```

### **Check Performance**
```python
from src.icloudpd.performance_service import create_performance_optimizer

optimizer = create_performance_optimizer(logger=logger)

# Get recommendations for your collection size
recommendations = optimizer.get_performance_recommendations(photo_count=5000)
for rec in recommendations:
    print(f"💡 {rec}")
```

### **Monitor Progress**
```python
from src.icloudpd.progress_service import create_progress_tracker

tracker = create_progress_tracker(
    total_photos=PhotoCount(100),
    total_bytes=FileSizeBytes(100 * 1024 * 1024),  # 100MB
    logger=logger
)

tracker.start_operation("My Operation")
# ... do work
tracker.photo_completed(photo, photo.size_bytes)
final_stats = tracker.finish()
```

## 🧪 Testing Your Changes

### **Unit Tests** (Pure Functions)
```bash
# Test individual functions
uv run python -c "
from src.icloudpd.pure_functions import calculate_timeline_path
from src.icloudpd.models import Photo
# ... create test photo
result = calculate_timeline_path(test_photo, TimelinePath('/timeline'))
print(f'Timeline path: {result}')
"
```

### **Integration Tests** (Service Composition)
```bash
# Run existing integration tests
uv run python test_phase3.py   # Tests album filtering and media types
uv run python test_phase4.py   # Tests change detection and filtering  
uv run python test_phase5_integration.py  # Tests all production features
```

### **Performance Tests** (Large Collections)
```python
# Create large test collection
from test_phase5_integration import create_large_test_collection

large_collection = create_large_test_collection()  # 500 photos
print(f"Created {len(large_collection)} test photos")

# Test your feature with large data
result = your_feature(large_collection)
```

## 🚨 Common Pitfalls to Avoid

### **❌ Don't Mutate Immutable Data**
```python
# BAD: Trying to modify frozen dataclass
photo.albums.add(AlbumName("New Album"))  # ❌ Error!

# GOOD: Create new instance  
new_albums = photo.albums | {AlbumName("New Album")}
updated_photo = photo.with_albums(new_albums)  # ✅
```

### **❌ Don't Use Primitive Types in Function Signatures**
```python
# BAD: Primitive obsession
def download_photo(id: str, path: str) -> bool:  # ❌

# GOOD: Domain-specific types
def download_photo(id: PhotoId, path: DataPath) -> Result[DataPath, str]:  # ✅
```

### **❌ Don't Ignore Result Types**
```python
# BAD: Assuming success
path = download_photo(photo)  # ❌ Might be Err type!
create_symlink(path)  # Will fail

# GOOD: Handle both success and failure
result = download_photo(photo)
if hasattr(result, 'value'):
    create_symlink(result.value)  # ✅
else:
    handle_error(result.error)
```

### **❌ Don't Skip Error Handling**
```python
# BAD: Raw operations
try:
    risky_operation()  # ❌ Manual exception handling
except Exception as e:
    # Handle manually...

# GOOD: Use safe wrapper
result = safe_wrapper.safe_execute(
    risky_operation,
    "operation_name", 
    ErrorCategory.NETWORK,
    recovery_suggestion="Check connection"
)  # ✅ Automatic categorization and logging
```

## 📚 Key Files to Understand

1. **`src/icloudpd/types.py`** - All domain-specific types (start here!)
2. **`src/icloudpd/models.py`** - Core immutable data structures  
3. **`src/icloudpd/protocols.py`** - Contracts for services (no inheritance!)
4. **`src/icloudpd/pure_functions.py`** - Stateless utility functions
5. **`src/icloudpd/integration_service.py`** - How everything connects together

## 🎯 Next Steps

1. **Read** `ARCHITECTURE.md` for deep understanding
2. **Run** all test files to see the system in action
3. **Experiment** with the configuration options in test files
4. **Create** a small feature following the patterns above
5. **Ask** questions about anything that's unclear!

**Remember**: The goal is **safety, type safety, and immutability**. When in doubt, use more specific types and make data immutable! 🛡️

---

*Ready to build awesome features? Let's go! 🚀*