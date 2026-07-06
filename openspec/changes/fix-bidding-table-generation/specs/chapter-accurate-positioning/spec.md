## ADDED Requirements

### Requirement: Title matching uses strict-priority algorithm
The chapter title matching in `_write_outline_item` and separator page child loop SHALL use a four-level strict-priority algorithm:
  1. Exact match (after whitespace normalization)
  2. Exact match after removing Chinese ordinal prefix (e.g., "一、", "1.", "（一）")
  3. Word token intersection (shared tokens between both titles)
  4. Substring containment (current behavior, as fallback)

#### Scenario: Exact match succeeds
- **WHEN** outline title is "供应商基本情况表" and required_sections title is "供应商基本情况表"
- **THEN** level 1 exact match SHALL succeed

#### Scenario: Ordinal prefix mismatch is handled
- **WHEN** outline title is "一、供应商基本情况表" and required_sections title is "供应商基本情况表"
- **THEN** level 2 after prefix removal SHALL match

#### Scenario: Different ordinals but same content
- **WHEN** outline title is "1. 供应商基本情况表" and required_sections title is "（一）供应商基本情况表"
- **THEN** level 3 token intersection SHALL match on "供应商基本情况表"

#### Scenario: No match found
- **WHEN** outline title is "其他材料" and no required_sections title contains related content
- **THEN** no match SHALL be made and the section SHALL use its default content (desc or empty)

### Requirement: Tables render in correct chapter
Tables extracted from `format_requirements.template_content` SHALL be written to the chapter matching their `title` field, not to a default/wrong chapter.

#### Scenario: Table follows separator page to correct chapter
- **WHEN** a separator page's child has title "一、供应商基本情况表" and format_requirements has a section with matching title containing a template table
- **THEN** the table SHALL be rendered under that chapter heading, not under "其他材料" or any other chapter

### Requirement: Duplicate table prevention
The separator page child loop and `_write_outline_item` SHALL NOT write the same table twice. If one path writes the table, the other SHALL skip it.

#### Scenario: Separator page child writes table, _write_outline_item skips
- **WHEN** the separator page child loop writes a template table for "一、供应商基本情况表"
- **THEN** `_write_outline_item` called for the same child SHALL detect the table was already written and skip it

#### Scenario: _write_outline_item writes table, separator page child skips
- **WHEN** `_write_outline_item` writes content_blocks for a chapter
- **THEN** the separator page child loop SHALL skip that chapter's table
