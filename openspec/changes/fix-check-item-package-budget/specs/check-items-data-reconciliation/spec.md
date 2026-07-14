## ADDED Requirements

### Requirement: Technical data source diagnostic logging

The system SHALL log the number of items collected from each data source during `assemble_technical()` for debugging and monitoring purposes.

#### Scenario: Source count logging on each request
- **WHEN** `assemble_technical()` completes
- **THEN** SHALL log at INFO level: "technical: comprehensive=N, table_tech=N, product_list=N, db_fallback=N items"

#### Scenario: Warning when comprehensive source is empty
- **WHEN** `_comprehensive.technical_requirements` is empty or absent
- **THEN** a WARNING SHALL be logged indicating the comprehensive data source provided no technical requirements

### Requirement: Scoring.technical and Technical disambiguation (no changes needed)

The `scoring.technical` dimensions and the `technical.items` list represent different semantic concepts. No code changes are required.

#### Scenario: Verify no overlap between scoring.technical and technical.items
- **WHEN** `scoring.technical` contains scoring dimensions (e.g., `{"name": "规格型号及技术要求", "score": 30}`)
- **AND** `technical.items` contains technical specification requirements (e.g., `{"content": "新型冠状病毒核酸检测试剂盒: 规格50人份/盒"}`)
- **THEN** these SHALL NOT be merged, deduplicated, or considered overlapping
- **AND** the `source_section` field SHALL continue to identify each data source
