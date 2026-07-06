## ADDED Requirements

### Requirement: FileStorage model removes local_path field
The `FileStorage` model SHALL remove the `local_path` column. All storage operations SHALL use MINIO exclusively.

#### Scenario: FileStorage no longer has local_path
- **WHEN** querying a FileStorage record's columns
- **THEN** `local_path` SHALL NOT exist as a column or attribute

### Requirement: No LOCAL storage_provider fallback paths
All code paths that check `storage_provider == "LOCAL"` or reference `local_path` SHALL be removed or replaced with MINIO-only logic.

#### Scenario: storage_provider LOCAL is never checked
- **WHEN** searching the codebase for `"LOCAL"` in `.py` files
- **THEN** there SHALL be zero matches in business logic (exception: model migration)

### Requirement: CHROMA storage_provider is read-only
Files with `storage_provider == "CHROMA"` SHALL only be read from `DocParseCache`; `_get_file_payload` SHALL raise a clear error for CHROMA files instead of returning None.

#### Scenario: _get_file_payload called on CHROMA file
- **WHEN** `_get_file_payload` is called with a file_record having `storage_provider == "CHROMA"`
- **THEN** it SHALL raise `RuntimeError` with a message indicating the file has no raw bytes available

### Requirement: StorageService.read_bytes only supports MINIO
`StorageService.read_bytes` SHALL raise `RuntimeError` for non-MINIO file records, instead of silently returning empty bytes.

#### Scenario: read_bytes on CHROMA file raises error
- **WHEN** `read_bytes` is called with a CHROMA file_record
- **THEN** it SHALL raise `RuntimeError`
