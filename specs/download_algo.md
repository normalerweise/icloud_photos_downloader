I want to do a bigger refactoring of the download logic now.
Help me to come to a proper design.
Below I have specified the Target directory hierarchy
the download algorithm. For now we focus on downloading photo assets to the _data directory. we will take care of the Library and Albums folders in a later iteration.
I've created a list of affected cli parameters as well.
Ask me step by step until you have all infomation available to propose a proper design.



## Target directory hierarchy:

/base_directory/
├── _metadata.sqlite
├── _data/                    # Actual photo files
│   ├── 3453453.jpg
│   ├── dafsdfsdf.heic
│   └── asdasd32.mov         # Live photo video component
├── Library/                 # Date-based organization
│   ├── 2024/
│   │   ├── 01/
│   │   │   └── IMG_1234.jpg -> ../../_Data/IMG_1234.jpg
│   │   └── 03/
│   │       ├── IMG_1235.heic -> ../../_Data/IMG_1235.heic
│   │       └── IMG_1235.mov -> ../../_Data/IMG_1235.mov
└── Albums/                  # Album-based organization
    ├── Vacation 2024/
    │   ├── IMG_1234.jpg
    │   └── IMG_1235.heic
    ├── Work/
    │   ├── Event 1/
    │   │   ├── IMG_1234.jpg
    │   │   └── IMG_1235.heic
    │   └── Event 2/
    │       ├── IMG_1235.mov
    │       └── IMG_1234.jpg
    └── Family/
        ├── IMG_1235.mov
        └── IMG_1234.jpg

/base_directory/ corresponds to the CLI parameter: "--directory" (help="Local directory that should be used for download")


## Download algorithm (incomplete, interim state):

1. List all photo assets (i.e. videos, photos...) from the given icloud library.

2. Apply filters according to cli parameters (e.g. last <number> photos, created after <date>) -> this is the "to process set"

3. For each asset in the to process set check the `_data/` directory whether the asset exists in all required versions (original + adjusted if available + alternative if available)

4. if the asset does not exist or is missing download all missing versions.
   all versions are downloaded to the `_data/` directory. the file name consists of the icloud asset id to ensure uniqueness (add version suffix to ensure unique file names across multiple versions) 

## Implementation details

* keep track of each asset in a local sqlite database
* there should be a photo_assets table in the database
* keep track of the metadata available per photo asset. E.g. file name, available versions, type (i.e. photo, video...)

## Affected cli parameters

*  "--size",
    help="Image size to download. `medium` and `thumb` will always be added as suffixes to filenames, `adjusted` and `alternative` only if conflicting, `original` - never. If `adjusted` or `alternative` specified and is missing, then `original` is used.",
    => obsolete our algorithm and specification fixes what we download
*  "--live-photo-size",
    help="Live Photo video size to download",
    => obsolete our algorithm and specification fixes what we download
*   "--album",
    help="Album to download or whole collection if not specified",
    => obsolete, as we work towards our fixed structure and download the _data directory only
*   "--skip-videos",
    help="Don't download any videos (default: Download all photos and videos)",
    is_flag=True,
    => obsolete we always download all types unless filtered by time or recency...
*   "--skip-live-photos",
    help="Don't download any live photos (default: Download live photos)",
    is_flag=True,
    => obsolete we always download all types unless filtered by time or recency...
*  "--force-size",
    help="Only download the requested size (`adjusted` and `alternate` will not be forced)
    => obsolete 
*   "--folder-structure",
    help="Folder structure (default: {:%Y/%m/%d}). "
    => obsolete, the stucture is explicitly specified
*   "--live-photo-mov-filename-policy",
    "lp_filename_generator",
    help="How to produce filenames for video portion of live photos: `suffix` will add _HEVC suffix and `original` will keep filename as it is.",
    => review against specification, behaviour might be right still, remove as cli parameter
*  "--align-raw",
    "raw_policy",
    help="For photo assets with raw and jpeg, treat raw always in the specified size: `original` (raw+jpeg), `alternative` (jpeg+raw), or unchanged (as-is). It matters when choosing sizes to download",
    type=click.Choice(["as-is", "original", "alternative"], case_sensitive=False),
    => review against specification, behaviour might be right still, remove as cli parameter
*   "--file-match-policy",
    "file_match_policy",
    help="Policy to identify existing files and de-duplicate. `name-size-dedup-with-suffix` appends file size to deduplicate. `name-id7` adds asset id from iCloud to all file names and does not de-duplicate.",
    type=click.Choice(["name-size-dedup-with-suffix", "name-id7"], case_sensitive=False),
    => obsolete as we switch to asset id based naming 