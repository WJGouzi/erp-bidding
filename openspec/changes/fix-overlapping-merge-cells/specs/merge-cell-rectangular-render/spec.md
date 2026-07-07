## ADDED Requirements

### Requirement: Merge overlapping horizontal+vertical cells as rectangle

When the merge_cells list contains both horizontal and vertical entries for the same `(row, col)`, the renderer SHALL combine them into a single rectangular merge operation: `cells[row][col].merge(cells[row + v_span - 1][col + h_span - 1])`.

#### Scenario: Vertical plus horizontal merge on same cell
- **WHEN** merge_cells contains `{"type":"vertical","row":1,"col":0,"span":3}` and `{"type":"horizontal","row":1,"col":0,"span":2}` for a table with 4+ rows and 2+ columns
- **THEN** the renderer SHALL produce a single rectangular merge: cells[1][0] through cells[3][1] (3 rows × 2 columns)

#### Scenario: Only horizontal merge, no overlap
- **WHEN** merge_cells contains only `{"type":"horizontal","row":0,"col":0,"span":2}`
- **THEN** the renderer SHALL perform a standard horizontal merge: cells[0][0] through cells[0][1]

#### Scenario: Only vertical merge, no overlap
- **WHEN** merge_cells contains only `{"type":"vertical","row":0,"col":0,"span":2}`
- **THEN** the renderer SHALL perform a standard vertical merge: cells[0][0] through cells[1][0]

#### Scenario: Multiple overlapping merges in complex table
- **WHEN** a table has 8+ rows and 5+ columns with 10+ interleaved horizontal and vertical merge_cells entries
- **THEN** all merges SHALL succeed without throwing `requested span not rectangular`

### Requirement: template_content rendering path handles vertical merges

The `template_content` rendering path in the separator page child loop SHALL handle both horizontal and vertical merge cells, using the same rectangular merge strategy.

#### Scenario: Vertical merge in template content table
- **WHEN** a template_content entry of type "table" has merge_cells with a vertical merge (`{"type":"vertical","row":0,"col":0,"span":2}`)
- **THEN** the generated DOCX table SHALL have cells at rows 0-1, column 0 merged vertically

#### Scenario: Overlapping merges in template content table
- **WHEN** a template_content entry of type "table" has merge_cells with both horizontal and vertical entries on the same (row,col)
- **THEN** the renderer SHALL combine them into a single rectangular merge, same as the content_blocks path
