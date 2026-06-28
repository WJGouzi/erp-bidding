## ADDED Requirements

### Requirement: Table type identification
The system SHALL identify the type of each table in a DOCX document before attempting extraction.

#### Scenario: Identify preliminary table
- **WHEN** a table has 3 columns and headers contain both "内容" and "说明与要求"
- **THEN** the system SHALL classify it as PRELIMINARY_TABLE type

#### Scenario: Identify scoring table by header keywords
- **WHEN** a table's headers contain "评分"/"得分"/"分数"/"分值" keywords
- **THEN** the system SHALL classify it as SCORING_TABLE type

#### Scenario: Identify product list table
- **WHEN** a table's headers contain "品名"/"规格"/"数量"/"品牌" keywords
- **THEN** the system SHALL classify it as PRODUCT_LIST type

#### Scenario: Identify qualification table
- **WHEN** a table's headers contain "供应商"/"注册地址"/"法定代表人"/"营业执照" keywords
- **THEN** the system SHALL classify it as QUALIFICATION_TABLE type

#### Scenario: Identify response format table
- **WHEN** a table's headers contain "比选文件条目号"/"响应文件的应答"/"招标文件要求" keywords
- **THEN** the system SHALL classify it as RESPONSE_FORMAT type

#### Scenario: Fallback for unknown table type
- **WHEN** a table does not match any known type pattern
- **THEN** the system SHALL classify it as GENERIC_TABLE type and flatten to text only

### Requirement: Preliminary table extraction
The system SHALL extract key-value pairs from preliminary (前附表) tables.

#### Scenario: Extract standard key-value rows
- **WHEN** a table is classified as PRELIMINARY_TABLE with columns [序号, 内容, 说明与要求]
- **THEN** the system SHALL map each row's "内容" cell to key and "说明与要求" cell to value

#### Scenario: Merge multi-line cell content
- **WHEN** a "说明与要求" cell contains multi-line text (e.g., "采购包1:5家\n采购包2:3家\n采购包3:3家")
- **THEN** the system SHALL preserve all lines as a single value with newlines

#### Scenario: Extract numeric values from preliminary table
- **WHEN** a value cell contains a number followed by units (e.g., "600元", "3家")
- **THEN** the system SHALL extract both the numeric value and the unit separately

#### Scenario: Extract evaluation method from preliminary table
- **WHEN** a row's "内容" cell contains "比选方法"/"评标方法"/"评审方法" and "说明与要求" contains "综合评分法"
- **THEN** the system SHALL set metadata.evaluation_method to "综合评分法" with source="table:preliminary"

#### Scenario: Extract consortium rule from preliminary table
- **WHEN** a row's "内容" cell contains "联合体" and "说明与要求" contains "不允许"/"不接受"
- **THEN** the system SHALL set metadata.allow_consortium to false

#### Scenario: Table extraction result overrides regex
- **WHEN** a value is extracted from both table parsing and regex text scanning
- **THEN** the table-parsed value SHALL take precedence (higher confidence)

### Requirement: Scoring table enhancement
The system SHALL enhance scoring table parsing with sub-dimension extraction and scoring standard preservation.

#### Scenario: Detect sub-dimensions under a main scoring dimension
- **WHEN** a scoring table has grouped rows (e.g., "报价" → "参与报价20分" + "供货20分" as sub-rows)
- **THEN** the system SHALL create a main dimension "报价" with sub_dimensions list containing all sub-rows

#### Scenario: Preserve original scoring standard text
- **WHEN** a scoring cell contains detailed evaluation criteria (e.g., "供应商应按时按照采购人需求参与每次报价...")
- **THEN** the system SHALL preserve the full text in a "scoring_standard" field of the dimension

#### Scenario: Detect scoring method from table
- **WHEN** a scoring table contains a row with "评标方法" or "评审办法" in any cell
- **THEN** the system SHALL extract the scoring method value from the adjacent cell

### Requirement: Product list extraction
The system SHALL extract structured product data from product list tables.

#### Scenario: Map product table headers
- **WHEN** a PRODUCT_LIST table has columns with headers like "品名"/"名称", "规格"/"规格型号", "数量", "单位", "单价"
- **THEN** the system SHALL map them to normalized fields: name, spec, qty, unit, unit_price

#### Scenario: Handle merged header cells
- **WHEN** a product table has merged header cells spanning multiple columns (common in DOCX)
- **THEN** the system SHALL correctly expand merged cells so each data row has the same number of columns

#### Scenario: Output product summary statistics
- **WHEN** a product table is successfully parsed
- **THEN** the system SHALL output summary statistics: total_items, distinct_categories, has_pricing

### Requirement: Table extraction result integration
The system SHALL integrate table extraction results into the metadata and analysis pipelines.

#### Scenario: Merge table results into metadata
- **WHEN** table extraction completes
- **THEN** the results SHALL be merged into metadata dict, with _source and _confidence fields

#### Scenario: Table data visible in analysis_data JSON
- **WHEN** the final analysis_data JSON is assembled
- **THEN** product list data SHALL be stored under metadata.tables.product_lists
- **THEN** preliminary table data SHALL be stored under metadata.tables.preliminary
