## ADDED Requirements

### Requirement: Cover template_content sourced from format_requirements

The system SHALL read the cover's `template_content` from `format_requirements.section_lookup` in the analysis data, not from outline items. The outline item's title SHALL be used as the lookup key (cleaned via `_clean_section_title`).

#### Scenario: Cover found in section_lookup
- **WHEN** the outline item has `is_cover=True` with a non-empty title
- **THEN** the system SHALL look up the cleaned title in `format_requirements.section_lookup`
- **WHEN** a matching section is found
- **THEN** the section's `template_content` SHALL be used for rendering

#### Scenario: Cover not found in section_lookup
- **WHEN** the outline item has `is_cover=True` but no matching section in `section_lookup`
- **THEN** the system SHALL fall back to the outline item's own `template_content` (if any)
- **THEN** if no template_content is found in either location, the system SHALL use the default fallback (render "投标文件")

### Requirement: No LLM filling for cover content

The system SHALL NOT use LLM to fill or modify cover template content. Only deterministic placeholder replacement (simple string substitution for known patterns like `（项目名称）`, `（项目编号）`, `XXX`) is allowed.

#### Scenario: LLM fill function removed
- **WHEN** `_build_docx_bytes` renders cover content
- **THEN** the `_llm_fill_cover_blocks` function SHALL NOT be called
- **THEN** cover text SHALL only be modified through `_fill_placeholder_text` deterministic substitutions

### Requirement: Second+ cover starts on new page

The system SHALL ensure each second or subsequent cover starts on a new page, preceded by `document.add_page_break()`.

#### Scenario: Second cover preceded by page break
- **WHEN** the main loop encounters a cover with `is_cover=True` and the first cover has already been skipped
- **THEN** the system SHALL insert `document.add_page_break()` BEFORE rendering the cover content
- **THEN** the system SHALL render the full cover content (title + template_content)
- **THEN** the system SHALL insert `document.add_page_break()` AFTER the cover content
