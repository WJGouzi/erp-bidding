## 1. Fix content_blocks rendering path (overlapping merges)

- [x] 1.1 In `helpers.py` `_write_outline_item` content_blocks table section (~line 4190-4210): restructure merge application to group merge_cells by `(row, col)` and combine overlapping horizontal+vertical merges into rectangular merges
- [x] 1.2 Verify horizontal-only merges still work correctly
- [x] 1.3 Verify vertical-only merges still work correctly
- [x] 1.4 Verify combined rectangular merges work (vertical + horizontal on same cell)

## 2. Fix template_content rendering path (missing vertical merges)

- [x] 2.1 In `helpers.py` `_write_outline_item` separator page child loop table section (~line 4479-4495): extend merge processing to handle both horizontal and vertical merge types with the same rectangular merge strategy
- [x] 2.2 Verify vertical merges are correctly rendered in template_content path
- [x] 2.3 Verify overlapping merges are correctly rendered as rectangles in template_content path

## 3. Verify

- [x] 3.1 Re-generate a test bid document and verify "供应商基本情况表" renders without merge errors
- [x] 3.2 Verify all other tables with merge cells (技术、服务应答表, 商务应答表, etc.) render correctly
- [x] 3.3 Verify generated DOCX opens without errors in Word/WPS

## 4. Fix text deduplication on merge

- [x] 4.1 Clear consumed cell text before merge in content_blocks path
- [x] 4.2 Clear consumed cell text before merge in template_content path
- [x] 4.3 Regenerate and verify no duplicate text in merged cells

## 5. Fix column widths matching original document

- [x] 5.1 Set tblGrid column widths from column_widths data in content_blocks path
- [x] 5.2 Add column_widths handling (tblGrid, tblW, tblLayout) in template_content path
- [x] 5.3 Verify all 12 column widths match original [1620, 1080, 180, ...]
- [x] 5.4 EMU→twips conversion in tblGrid and tblW
- [ ] 5.5 [用户操作] 重启服务后重新生成验证