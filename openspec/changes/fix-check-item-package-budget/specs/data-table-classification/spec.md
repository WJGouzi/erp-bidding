## ADDED Requirements

### Requirement: Data table classification by header patterns

The system SHALL classify docx tables into three categories based on header row patterns: technical requirement tables, product list tables, and scoring tables.

#### Scenario: Classify technical requirement table
- **WHEN** a table header contains both "标的名称" AND "规格型号" (or "规格型号及技术要求")
- **THEN** the table SHALL be classified as `TECH_REQUIREMENT`
- **AND** its structured data SHALL be extracted to `table_classification.tech_requirements[]`

#### Scenario: Classify product list table
- **WHEN** a table header contains "标的名称" AND ("单价限价" OR "★单价限价")
- **THEN** the table SHALL be classified as `PRODUCT_LIST`
- **AND** its structured data SHALL be extracted to `table_classification.product_lists[]`

#### Scenario: Classify scoring table
- **WHEN** a table header contains "评分因素" AND "分值" AND "评分标准"
- **THEN** the table SHALL be classified as `SCORING`
- **AND** its structured data SHALL be extracted to `table_classification.scoring`

#### Scenario: No overlap with format_requirements
- **WHEN** a table is part of the bidding document format templates (第三章 投标文件格式)
- **THEN** it SHALL NOT be classified by the data table classifier
- **AND** the format_requirements extraction path SHALL remain unchanged

### Requirement: Comprehensive assembly from classified tables

The `_comprehensive` assembler SHALL read data from `table_classification` to populate technical requirements, products, and scoring dimensions.

#### Scenario: Technical requirements from classified tables
- **WHEN** `table_classification.tech_requirements` has items
- **THEN** each item's "标的名称" and "规格型号及技术要求" SHALL be merged into `_comprehensive.technical_requirements[]`
- **AND** duplicates SHALL be removed using a seen-set

#### Scenario: Products from classified tables
- **WHEN** `table_classification.product_lists` has items
- **THEN** each item SHALL be added to `_comprehensive.products[]`

#### Scenario: Scoring dimensions from classified tables
- **WHEN** `table_classification.scoring` has dimensions
- **THEN** any dimension not already present in `_comprehensive.scoring.dimensions` SHALL be appended

### Requirement: Scoring dimension completeness

The `scoring.technical` array in check-items response SHALL contain all scoring dimensions from the original document's scoring table.

#### Scenario: All scoring dimensions present
- **WHEN** the original docx scoring table has N scoring dimensions
- **THEN** `scoring.technical` SHALL contain exactly N items (no omissions)
- **AND** each dimension SHALL include `name`, `score`, `weight`, `criteria`, and `type` fields

### Requirement: No modification to format requirements

The `format_requirements` extraction path and `phase1_5_format.py` SHALL NOT be modified by this change.

#### Scenario: Qualification templates unaffected
- **WHEN** data table classification is restored
- **THEN** all qualification/format templates from `format_requirements.required_sections` SHALL remain identical to current output
