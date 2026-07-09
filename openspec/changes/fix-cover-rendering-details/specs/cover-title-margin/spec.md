## ADDED Requirements

### Requirement: Cover renders title and template_texts with correct font

The system SHALL render the cover section's `title` and `template_texts` in addition to `template_content` when generating the cover page. The title SHALL be rendered first, using the font from the first `template_content` block (or default: 宋体, 16pt, bold). `template_texts` SHALL be rendered only if they differ from the already-rendered `template_content` blocks (to avoid duplication).

#### Scenario: First cover renders title from section data
- **WHEN** `_build_docx_bytes` renders the first cover
- **THEN** it SHALL first render the section `title` ("资 格 性 响 应 文 件") as a centered paragraph with bold font matching the template style
- **THEN** it SHALL render the `template_content` blocks

#### Scenario: Cover title font matches template
- **WHEN** the cover section has `template_content` with font info
- **THEN** the title paragraph SHALL use `font_name`, `font_size`, `bold` from the first block's font
- **WHEN** no font info is available
- **THEN** the title SHALL default to 宋体, 16pt, bold, centered

### Requirement: Cover page margins match original document

The system SHALL apply page margins to the cover that match the original bidding document. The page setup (margin) for the cover page SHALL be set before rendering cover content.

#### Scenario: Cover uses margins from analysis data
- **WHEN** `format_requirements` contains page margin info (`margins.top`, `margins.bottom`, `margins.left`, `margins.right`)
- **THEN** the cover page SHALL use those margin values

#### Scenario: Cover uses default margins when no margin data
- **WHEN** the analysis data has no margin info
- **THEN** the cover page SHALL use default Word margins (top/bottom: 2.54cm, left/right: 3.17cm)

### Requirement: Analysis phase captures page margins

The analysis pipeline SHALL capture the page margin settings from the original document and store them in `format_requirements.page_margins`.

#### Scenario: Page margins extracted from document
- **WHEN** `phase1_5_format` extracts format requirements
- **THEN** it SHALL read the document's section page margins (`margin_top`, `margin_bottom`, `margin_left`, `margin_right` in EMU or cm)
- **THEN** it SHALL store them in `format_requirements.page_margins`

### Requirement: Second cover starts on new page

The system SHALL ensure each second+ cover starts on a new page, separated from the previous chapter.

#### Scenario: Second cover preceded by page break
- **WHEN** the main loop encounters a second cover (`is_cover=True` and `_cover_first_skipped=True`)
- **THEN** the system SHALL insert `document.add_page_break()` BEFORE rendering the cover content
- **THEN** the system SHALL render the cover content
- **THEN** the system SHALL insert another `document.add_page_break()` AFTER the cover content

### Requirement: "正本" label renders correctly on cover page

The "正本" label SHALL be rendered as a floating table on the cover page with absolute positioning. The table's top edge SHALL be 2.5cm from the page top, and its right edge SHALL be 2.5cm from the page right edge.

#### Scenario: "正本" positioned on cover
- **WHEN** the first cover is rendered
- **THEN** a 1x1 table with black border containing "正本" in 宋体 16pt bold is created
- **THEN** the table SHALL be absolutely positioned with `tblpX = 6120000 EMU` and `tblpY = 900000 EMU`
- **THEN** `document.add_page_break()` SHALL follow to separate cover from TOC

#### Scenario: "正本" not duplicated
- **WHEN** the first cover has no template content (no cover rendered)
- **THEN** the "正本" table SHALL NOT be created

### Requirement: Cover margin is restored after cover page

After the cover page, the system SHALL restore the document's default margins for the rest of the bidding document (TOC and body content).

#### Scenario: Margins reset after cover
- **WHEN** cover rendering is complete and `document.add_page_break()` is about to be added
- **THEN** the system SHALL reset page margins to the standard bidding document defaults (or the margins from the original document's body sections)
