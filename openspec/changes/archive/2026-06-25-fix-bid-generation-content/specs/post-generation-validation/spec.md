## ADDED Requirements

### Requirement: 生成后对标验证
After all chapters are generated, the system SHALL perform an automated verification that compares the generated bid content against the original tender document's requirements, and output a coverage report.

#### Scenario: 对标报告生成
- **WHEN** the生成阶段 completes (`_complete_generate` finishes assembling the DOCX)
- **THEN** the system SHALL read the generated chapter contents and the original tender document
- **AND** SHALL produce a coverage report listing: each requirement from the tender, whether it is addressed in the generated bid, and the location where it appears

#### Scenario: 遗漏要求告警
- **WHEN** the coverage analysis finds a tender requirement that is NOT addressed in any chapter of the generated bid
- **THEN** the system SHALL flag this as a MISSING requirement in the coverage report
- **AND** the task-level error_message SHALL include a warning about uncovered requirements

### Requirement: 对标报告存储与查看
The coverage report SHALL be persisted as part of the task's analysis result data (`analysis_data`), and SHALL be accessible via the existing task detail API.

#### Scenario: 查看对标报告
- **WHEN** a user queries the task detail after generation
- **THEN** the response SHALL include a `coverage_report` field with the full coverage analysis
