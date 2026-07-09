## ADDED Requirements

### Requirement: Cover renders title before template_content

The system SHALL render the cover section's `title` as a separate paragraph before the `template_content` blocks. The title SHALL use the font information from the first `template_content` block (or default to 宋体 22pt bold if no blocks exist).

#### Scenario: First cover renders title with template font
- **WHEN** the first cover section has `template_content` blocks with font info
- **THEN** the title paragraph SHALL be rendered first, using `font_name`, `font_size`, `bold` from the first block
- **THEN** the `template_content` blocks SHALL be rendered after the title

#### Scenario: First cover renders title with default font
- **WHEN** the first cover section has no `template_content` blocks
- **THEN** the title SHALL default to 宋体 22pt bold, centered

### Requirement: Cover page margins match original document

The system SHALL apply the original document's page margins (captured in `format_requirements.page_margins`) to the cover page before rendering cover content. After the cover is complete (before page break to TOC), margins SHALL be reset to defaults.

#### Scenario: Cover uses margins from analysis data
- **WHEN** `format_requirements.page_margins` contains top/bottom/left/right values
- **THEN** the cover section SHALL use those margin values

#### Scenario: Cover uses default margins when no margin data
- **WHEN** `format_requirements.page_margins` is missing or empty
- **THEN** the cover page SHALL use default margins (top/bottom: 2.54cm, left/right: 3.17cm)

### Requirement: "正本" label renders horizontally on cover page

The "正本" label SHALL be rendered as a floating 1x1 table on the cover page. The table width SHALL be 3.0cm to ensure the two Chinese characters render horizontally (not stacked vertically). The table SHALL be absolutely positioned 2.5cm from the page top edge and 2.5cm from the page right edge.

#### Scenario: "正本" positioned at top-right with horizontal text
- **WHEN** the first cover is rendered (whether via template or fallback)
- **THEN** a 1x1 table with black solid borders containing "正本" in 宋体 16pt bold SHALL be created
- **THEN** the cell width SHALL be set to 3.0cm
- **THEN** the table SHALL be absolutely positioned with `vertAnchor="page"`, `horzAnchor="page"`, `tblpX=5580000` (15.5cm from left), `tblpY=900000` (2.5cm from top)

#### Scenario: "正本" not created when no cover rendered
- **WHEN** no cover template is found and no LLM fallback is used
- **THEN** the "正本" table SHALL NOT be created
