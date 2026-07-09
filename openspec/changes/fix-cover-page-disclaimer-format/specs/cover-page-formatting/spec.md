## ADDED Requirements

### Requirement: Cover page font formatting SHALL use original document metadata
The system SHALL render cover page template content using the exact font metadata (font_name, font_size, alignment, bold) extracted from the original bidding document, without falling back to default values unless the metadata is absent.

#### Scenario: Cover paragraph rendered with original font name and size
- **WHEN** a cover page template_content block has type "paragraph" or "text" with font metadata font_name="黑体" and font_size=22.0
- **THEN** the rendered paragraph SHALL use font_name="黑体" and font_size=22.0 (Pt)

#### Scenario: Cover table cell rendered with original font properties
- **WHEN** a cover page template_content block has type "table" with font metadata font_name="宋体" and font_size=12.0
- **THEN** the rendered table cells SHALL use font_name="宋体" and font_size=12.0 (Pt)

#### Scenario: Missing font metadata falls back gracefully
- **WHEN** a cover page template_content block has no font_name or font_size in its font dict
- **THEN** the rendered text SHALL use the document default font (仿宋, 12pt) without error

#### Scenario: Font alignment is preserved
- **WHEN** a cover page paragraph block has font.alignment="center"
- **THEN** the rendered paragraph SHALL have center alignment

### Requirement: "正本" mark SHALL use specified format
The cover page "正本" mark SHALL be rendered as: 宋体, 三号(16pt), 黑色(black), positioned at the top-right area of the cover page.

#### Scenario: "正本" rendered with correct font and color
- **WHEN** cover page is generated
- **THEN** the "正本" text SHALL have font.name="宋体", font.size=16, font.color.rgb=RGBColor(0,0,0)

#### Scenario: "正本" positioned at top-right
- **WHEN** cover page is generated
- **THEN** the "正本" SHALL appear above the main cover content, positioned at the top-right portion of the page using table absolute positioning (tblpPr)

### Requirement: Disclaimer page SHALL not be followed by extra blank page
The disclaimer page SHALL render on its own page without an additional blank page inserted between the disclaimer and the cover page.

#### Scenario: No blank page after disclaimer
- **WHEN** the disclaimer content fits within one page
- **THEN** the cover page SHALL start on the page immediately following the disclaimer, with no blank page in between
