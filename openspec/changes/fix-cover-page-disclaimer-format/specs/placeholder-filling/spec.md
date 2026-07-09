## ADDED Requirements

### Requirement: Cover placeholder "投标日期" SHALL use bidding document bid opening time
The system SHALL replace the "投标日期" placeholder on the cover page with the bid opening time (bid_open_time) extracted from the original bidding document, formatted as "YYYY年MM月DD日".

#### Scenario: Bid open time available in metadata
- **WHEN** the bidding document analysis contains bid_open_time (e.g., "2026-07-10 10:00")
- **THEN** the cover page "投标日期" placeholder SHALL be replaced with "2026年07月10日"

#### Scenario: Bid open time from key_dates.bid_opening
- **WHEN** bid_open_time is not in the top-level metadata but key_dates.bid_opening is available
- **THEN** the cover page "投标日期" placeholder SHALL use key_dates.bid_opening value

#### Scenario: No bid open time available falls back to current time
- **WHEN** no bid open time is found in the analysis data or metadata
- **THEN** the cover page "投标日期" SHALL use the current system time formatted as "YYYY年MM月DD日"

### Requirement: Cover placeholder "项目名称" and "项目编号" SHALL use analysis data
The system SHALL replace "（项目名称）" placeholder with the project_name from bidder_notice or metadata, and "（项目编号）" placeholder with the project_no/project_code from bidder_notice or metadata.

#### Scenario: Project name filled from bidder_notice
- **WHEN** bidder_notice.project_name is "2025年政府采购试剂耗材第一批"
- **THEN** the cover page "（项目名称）" SHALL be replaced with the project name

#### Scenario: Project number filled from bidder_notice
- **WHEN** bidder_notice.project_no is "N5101082025000125"
- **THEN** the cover page "（项目编号）" SHALL be replaced with the project number

#### Scenario: Placeholder unchanged when data unavailable
- **WHEN** project_name or project_no is empty
- **THEN** the placeholder SHALL remain in its original form (no replacement)

### Requirement: Cover page rendering order SHALL be: disclaimer → cover → content
The generated docx SHALL have the following page order: page 1 = disclaimer, page 2... = cover page, then table of contents and chapter content.

#### Scenario: Document structure preserved
- **WHEN** a complete bid document is generated
- **THEN** the disclaimer appears first, followed by the cover page, then table of contents and chapter content, without extra blank pages between them
