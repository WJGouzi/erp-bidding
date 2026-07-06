## ADDED Requirements

### Requirement: Extract merge_cells from ContentBlock tables
`_extract_template_tables` SHALL extract `merge_cells` from each `ContentBlock` with type "table" and include it in the returned table dict.

#### Scenario: Table with horizontal merge cells is extracted
- **WHEN** a ContentBlock has type "table", headers=["A","B","C"], rows=[["val1","","val3"]], and merge_cells=[{"type":"horizontal","row":0,"col":1,"span":2}]
- **THEN** the extracted table dict SHALL contain `"merge_cells": [{"type":"horizontal","row":0,"col":1,"span":2}]`

#### Scenario: Table without merge cells is extracted
- **WHEN** a ContentBlock has type "table" but no merge_cells
- **THEN** the extracted table dict SHALL contain `"merge_cells": []`

#### Scenario: Table with vertical merge cells is extracted
- **WHEN** a ContentBlock has type "table" and merge_cells=[{"type":"vertical","row":0,"col":0,"span":2}]
- **THEN** the extracted table dict SHALL contain `"merge_cells": [{"type":"vertical","row":0,"col":0,"span":2}]`

### Requirement: Propagate merge_cells through format_requirements pipeline
The `format_requirements` injected into `analysis_data` SHALL preserve `merge_cells` in every `template_content` entry of type "table".

#### Scenario: Merge cells survive analysis_data JSON serialization
- **WHEN** start_analyze_v3 returns format_requirements with a table containing merge_cells
- **THEN** the serialized analysis_data JSON in `BiddingAnalysisResult` SHALL contain the same merge_cells data

### Requirement: Apply merge_cells when writing tables in generated docx
`_write_outline_item` and the separator page child loop SHALL apply `merge_cells` when rendering tables to the DOCX document.

#### Scenario: Horizontal merge is applied in docx table
- **WHEN** a ContentBlock table has merge_cells=[{"type":"horizontal","row":1,"col":0,"span":2}]
- **THEN** the generated DOCX SHALL have cells at row 1, columns 0-1 merged horizontally

#### Scenario: Vertical merge is applied in docx table
- **WHEN** a ContentBlock table has merge_cells=[{"type":"vertical","row":0,"col":0,"span":2}]
- **THEN** the generated DOCX SHALL have cells at rows 0-1, column 0 merged vertically
