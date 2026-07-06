## ADDED Requirements

### Requirement: CHROMA file re-analysis preserves cache
When `start_analyze_v3` processes a file with `storage_provider == "CHROMA"` and `_get_file_payload` returns None (no MinIO backup), the function SHALL NOT delete the `DocParseCache` and SHALL use cached data.

#### Scenario: CHROMA file without MinIO backup is re-analyzed
- **WHEN** a file has storage_provider="CHROMA" and _get_file_payload returns None
- **THEN** the DocParseCache SHALL NOT be deleted
- **AND** the function SHALL fall back to _get_structured_doc_from_cache

#### Scenario: CHROMA file with MinIO backup is fully re-parsed
- **WHEN** a file has storage_provider="CHROMA" but _get_file_payload returns valid bytes (file also stored in MinIO)
- **THEN** the DocParseCache SHALL be deleted
- **AND** the function SHALL re-parse the file from raw bytes

### Requirement: _extract_analysis_context handles None in raw_tables
`_extract_analysis_context` in `helpers.py` SHALL handle `None` entries in `raw_tables` list without raising `AttributeError`.

#### Scenario: raw_tables contains None entry
- **WHEN** `table_classification.raw_tables` contains a `None` entry
- **THEN** the function SHALL skip the None entry and continue processing remaining entries

### Requirement: Analysis failure raises clear error
When `start_analyze_v3` cannot obtain a structured document, it SHALL raise a clear `RuntimeError` with the file_id and task_id, rather than silently returning None.

#### Scenario: No file found returns None
- **WHEN** shared_resource.tender_file_id is None
- **THEN** the function SHALL raise RuntimeError with a descriptive message

#### Scenario: FileStorage record not found
- **WHEN** FileStorage.query.get returns None for tender_file_id
- **THEN** the function SHALL raise RuntimeError with a descriptive message
