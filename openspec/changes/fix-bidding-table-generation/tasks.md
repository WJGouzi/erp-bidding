## 1. Fix merge_cells extraction in template tables

- [ ] 1.1 Modify `_extract_template_tables` in `phase1_5_format.py` to include `merge_cells` from ContentBlock in the returned table dict
- [ ] 1.2 Verify that `_backfill_merge_cells_from_tables` in `analysis_v3/__init__.py` correctly sets `block.merge_cells` on ContentBlock objects
- [ ] 1.3 Verify merge_cells survive JSON serialization through `analysis_data` into `format_requirements.template_content`

## 2. Fix chapter title matching algorithm

- [ ] 2.1 Create `_strict_title_match(title, candidate)` helper in `helpers.py` with 4-level priority matching
- [ ] 2.2 Replace the substring-only title match in separator page child loop (line ~4405) with `_strict_title_match`
- [ ] 2.3 Replace the substring-only title match in `_write_outline_item` format_requirements fallback (line ~4118) with `_strict_title_match`
- [ ] 2.4 Add duplicate write prevention: track which chapter tables have been written in the separator page child loop

## 3. Fix CHROMA file re-analysis reliability

- [ ] 3.1 Fix `start_analyze_v3` CHROMA path: only delete DocParseCache when `_get_file_payload` returns valid bytes
- [ ] 3.2 Add fallback: when CHROMA file has no MinIO backup, read from cache without deleting it first
- [ ] 3.3 Fix `if not doc:` fallback control flow to not execute after `else` branch already raised

## 4. Fix _extract_analysis_context NoneType error

- [ ] 4.1 Add type check in `_extract_analysis_context` raw_tables loop: skip non-dict entries even if not None
- [ ] 4.2 Change `if not rt: continue` to `if not isinstance(rt, dict): continue`

## 5. Remove all LOCAL storage references

- [ ] 5.1 Remove `local_path` column from `FileStorage` model in `domain/models.py`
- [ ] 5.2 Remove `local_path` from `FileStorage` serialization (to_dict)
- [ ] 5.3 Search and remove all `storage_provider == "LOCAL"` condition branches in business logic
- [ ] 5.4 Make `_get_file_payload` raise RuntimeError for non-MINIO storage providers instead of returning None
- [ ] 5.5 Make `StorageService.read_bytes` raise RuntimeError for non-MINIO file records

## 6. Verify and test

- [ ] 6.1 Re-analyze a test task and verify merge_cells are populated in analysis_data
- [ ] 6.2 Generate a test bid document and verify tables appear in correct chapters
- [ ] 6.3 Verify tables have correct merge cells in the generated DOCX
- [ ] 6.4 Verify old CHROMA-only file re-analysis works (cache path)
- [ ] 6.5 Verify MinIO-backed file re-analysis works (re-parse path)
