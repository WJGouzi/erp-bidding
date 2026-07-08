## ADDED Requirements

### Requirement: Business requirements multi-source merge
`assemble_business()` SHALL merge business requirements from data sources in priority order:
1. `analysis_data._comprehensive.business_requirements` — structured v3 segment extraction
2. `analysis_data.table_classification.business_requirements` — business requirement tables
3. `analysis_data.table_classification.service_requirements` — service requirement tables (classified as business)
4. `analysis_data.metadata.extra.*` — metadata extra fields (delivery_location, payment_terms, etc.)
5. `result.business_requirements` — DB flat text column (fallback)

Duplicate content across sources SHALL be deduplicated by text content.
When all structured sources are empty, the system MUST fall back to `result.business_requirements`.

#### Scenario: All structured sources available
- **WHEN** `analysis_data._comprehensive.business_requirements` has 5 items, `table_classification.business_requirements` has 2 tables, `metadata.extra` has 3 populated fields
- **THEN** output items SHALL contain 10+ unique items merged from all sources, with no duplicates

#### Scenario: All structured sources empty
- **WHEN** `_comprehensive`, `table_classification`, and `metadata.extra` are all empty
- **THEN** output SHALL fall back to content from `result.business_requirements`

#### Scenario: Cross-source deduplication
- **WHEN** same text "付款方式：验收合格后30日内付款" appears in both `_comprehensive` and `table_classification`
- **THEN** output SHALL contain only 1 copy of that text

### Requirement: Technical requirements multi-source merge
`assemble_technical()` SHALL merge technical requirements from data sources in priority order:
1. `analysis_data._comprehensive.technical_requirements` — structured v3 segment extraction
2. `analysis_data.table_classification.tech_requirements` — technical parameter tables
3. `analysis_data.table_classification.product_lists` — product list tables with specifications
4. `result.technical_requirements` — DB flat text column (fallback)

Placeholder text patterns ("暂未提取到技术要求") SHALL be filtered out.
Duplicate content across sources SHALL be deduplicated.

#### Scenario: Technical requirements from product list
- **WHEN** `table_classification.product_lists` contains items with "产品名称" and "规格参数"
- **THEN** output SHALL include `"{产品名称}: {规格参数}"` formatted items

#### Scenario: Placeholder filtering
- **WHEN** `result.technical_requirements` contains only "暂未提取到技术要求。"
- **THEN** output SHALL be empty items list, even if DB column has content

### Requirement: EXTRA_LABELS key name alignment
`EXTRA_LABELS` in `analysis_schema.py` SHALL use the same key names as the metadata extraction output:
- `submission_location` → `bid_submission_location`
- `winner_count` → `winner_count_text`
- `submission_docs` → `submission_docs_summary`

#### Scenario: bid_submission_location available
- **WHEN** `metadata.extra.bid_submission_location` has value "送达地点：XX路XX号"
- **THEN** `computed_business_requirements` SHALL include "递交地点：送达地点：XX路XX号"

### Requirement: Output schema unchanged
The output schema of both `assemble_business()` and `assemble_technical()` MUST remain `{"items": [...], "raw": ""}`.
The `items` entries MUST have `content` and `source_section` fields.

#### Scenario: Frontend compatibility
- **WHEN** frontend calls `GET /bidding/tasks/:id/check-items`
- **THEN** the `business` and `technical` sections SHALL have the same JSON structure as before
