## ADDED Requirements

### Requirement: Package content segmentation
The system SHALL split document content by package before per-package analysis.

#### Scenario: Detect package boundary by section title
- **WHEN** a section title contains "第X包" or "包X" pattern
- **THEN** the system SHALL assign that section and its children to the corresponding package

#### Scenario: Detect package boundary by table annotation
- **WHEN** a table contains "(采购包X)" or "（包X）" annotation in any cell
- **THEN** the system SHALL assign that table to the corresponding package

#### Scenario: Handle common/shared content
- **WHEN** content does not match any package boundary pattern
- **THEN** the system SHALL mark it as "shared" (公共) content

#### Scenario: Shared content replication
- **WHEN** a qualification requirement is marked as "三包共用" or "各包共用"
- **THEN** the system SHALL replicate it to each package's qualification list with a "shared" flag

### Requirement: Per-package qualification scanning
The system SHALL independently scan eligibility requirements for each package.

#### Scenario: Extract package-specific qualifications
- **WHEN** a paragraph contains "采购包X" or "包X" followed by a qualification requirement
- **THEN** the system SHALL associate that requirement only with package X

#### Scenario: Distinguish shared vs. specific requirements
- **WHEN** building the per-package qualification list
- **THEN** shared qualifications SHALL be marked with `scope: "shared"`
- **THEN** package-specific qualifications SHALL be marked with `scope: "specific"`

### Requirement: Per-package parameter statistics
The system SHALL independently count technical parameters for each package.

#### Scenario: Count starred (★) clauses per package
- **WHEN** processing package X's content sections
- **THEN** the system SHALL count ★ clauses occurring within those sections

#### Scenario: Count important (▲) clauses per package
- **WHEN** processing package X's content sections
- **THEN** the system SHALL count ▲ clauses occurring within those sections

#### Scenario: Identify core products per package
- **WHEN** a package's content mentions "核心产品" or "主要产品"
- **THEN** the system SHALL extract the product names into the package's core_products list

### Requirement: Per-package strategic analysis
The system SHALL generate differentiated strategic recommendations for each package.

#### Scenario: Assess per-package competition level
- **WHEN** a package has supplier_count >= 5
- **THEN** competition SHALL be rated "high"
- **WHEN** a package has supplier_count between 2-4
- **THEN** competition SHALL be rated "medium"
- **WHEN** a package has supplier_count == 1
- **THEN** competition SHALL be rated "low"

#### Scenario: Assess per-package difficulty based on qualifications
- **WHEN** a package requires special permits (危险化学品经营许可证, 医疗器械注册证, etc.)
- **THEN** difficulty SHALL be rated "high"
- **WHEN** a package only requires standard business licenses
- **THEN** difficulty SHALL be rated "low"

#### Scenario: Generate per-package writing focus
- **WHEN** a package's scoring has high-weight subjective dimensions
- **THEN** the writing_focus SHALL include those dimensions as key areas to address

#### Scenario: Output differentiated strategy per package
- **WHEN** building the strategy output
- **THEN** each package SHALL have its own strategy block with: difficulty, competition, focus, risk

### Requirement: Cross-package analysis
The system SHALL perform cross-package analysis to identify shared patterns and strategic opportunities.

#### Scenario: Identify overlapping qualifications
- **WHEN** two or more packages share the same qualification requirement
- **THEN** the system SHALL note the overlap in a cross_package.overlapping_qualifications list

#### Scenario: Identify highest-value package
- **WHEN** comparing packages by priority_score (budget + scoring_count - difficulty_penalty)
- **THEN** the system SHALL mark the package with highest score as "highest_value"

#### Scenario: Identify lowest-risk package
- **WHEN** comparing packages by risk_factors count and starred_count
- **THEN** the system SHALL mark the package with fewest risk factors as "lowest_risk"

### Requirement: Package-aware scoring
The system SHALL associate scoring dimensions with specific packages when applicable.

#### Scenario: Link scoring dimension to package
- **WHEN** a scoring table row references a specific package (e.g., "采购包1" within scoring criteria)
- **THEN** the dimension SHALL be tagged with applicable_packages: [1]
- **WHEN** a scoring dimension applies to all packages
- **THEN** the dimension SHALL be tagged with applicable_packages: ["all"]

#### Scenario: Package-specific total score
- **WHEN** dimensions are filtered by package
- **THEN** the system SHALL compute package-specific total_score as the sum of applicable dimensions
SPEC_EOF

cat > /Users/wangjun/Desktop/work/erp/code/erp-bidding/openspec/changes/cleanup-v2-enhance-fields/specs/document-classifier/spec.md << 'SPEC3_EOF'
## ADDED Requirements

### Requirement: Document type classification
The system SHALL classify the procurement document type before metadata extraction.

#### Scenario: Classify by filename keywords
- **WHEN** the filename contains "比选"
- **THEN** the system SHALL set a preliminary classification of SELECTION
- **WHEN** the filename contains "竞争性谈判"
- **THEN** the system SHALL set a preliminary classification of NEGOTIATION
- **WHEN** the filename contains "询价"
- **THEN** the system SHALL set a preliminary classification of INQUIRY
- **WHEN** the filename does not match any specific type
- **THEN** the system SHALL set a preliminary classification of TENDER (default)

#### Scenario: Confirm classification by body text
- **WHEN** the document body contains "比选公告" or "比选文件"
- **THEN** the system SHALL confirm the classification as SELECTION
- **WHEN** the document body contains "竞争性谈判公告" or "竞争性谈判文件"
- **THEN** the system SHALL confirm the classification as NEGOTIATION
- **WHEN** the document body contains "招标公告" or "招标文件" or "投标邀请"
- **THEN** the system SHALL confirm the classification as TENDER
- **WHEN** body text conflicts with filename classification
- **THEN** body text SHALL take precedence

#### Scenario: Compute classification confidence
- **WHEN** multiple body-text keywords match a single type
- **THEN** confidence SHALL be "high"
- **WHEN** only filename matches (no body text confirmation)
- **THEN** confidence SHALL be "medium"
- **WHEN** no clear match found
- **THEN** confidence SHALL be "low" and default to TENDER

#### Scenario: Store classification in metadata
- **WHEN** classification completes
- **THEN** the result SHALL be stored in metadata.document_type with fields: value, confidence, source

### Requirement: Type-specific rule loading
The system SHALL load different regex rule sets based on the classified document type.

#### Scenario: Load SELECTION-specific rules
- **WHEN** document_type == "SELECTION"
- **THEN** the system SHALL activate rules for terms: "比选人", "比选代理机构", "比选保证金", "比选申请书"

#### Scenario: Load TENDER-specific rules
- **WHEN** document_type == "TENDER"
- **THEN** the system SHALL activate rules for terms: "采购人", "招标代理机构", "投标保证金", "投标文件"

#### Scenario: Load NEGOTIATION-specific rules
- **WHEN** document_type == "NEGOTIATION"
- **THEN** the system SHALL activate rules for terms: "采购人", "谈判小组", "最终报价", "谈判文件"

#### Scenario: Inherit generic rules across all types
- **WHEN** loading type-specific rules
- **THEN** all generic rules SHALL remain active as the base set
- **THEN** type-specific rules SHALL override generic rules when they match the same field

### Requirement: Confidence and source annotation
The system SHALL annotate each extracted metadata field with confidence level and source identification.

#### Scenario: Annotate metadata fields with confidence
- **WHEN** a metadata field is extracted by a high-priority rule with clear match
- **THEN** confidence SHALL be set to "high"
- **WHEN** a metadata field is extracted by a low-priority fallback rule
- **THEN** confidence SHALL be set to "low" or "medium" based on rule reliability

#### Scenario: Annotate metadata fields with source
- **WHEN** a metadata field value comes from table parsing
- **THEN** _source SHALL be set to "table:<table_type>"
- **WHEN** a metadata field value comes from regex text scanning
- **THEN** _source SHALL be set to "text:regex"
- **WHEN** a metadata field value is a default (not extracted)
- **THEN** _source SHALL be set to "default"

#### Scenario: Source and confidence stored alongside value
- **WHEN** the final metadata dict is built
- **THEN** each scalar field SHALL have a corresponding `_fieldname_meta` dict containing value, confidence, source, matched_pattern

### Requirement: Death-line priority annotation
The system SHALL annotate extracted eligibility/disqualification items with a priority rating indicating actual risk level.

#### Scenario: Classify "true veto" items
- **WHEN** a disqualification item is explicitly marked with "★" or "★" or "实质性要求"
- **THEN** the system SHALL set severity to "fatal" and priority to "must_fix"

#### Scenario: Classify "recoverable" items
- **WHEN** a disqualification condition describes a process violation (e.g., "逾期送达" "未按要求密封")
- **THEN** the system SHALL set severity to "warning" and priority to "should_fix"

#### Scenario: Classify "informational" items
- **WHEN** a disqualification item describes post-award consequences (e.g., "中标无效" after discovery of fraud)
- **THEN** the system SHALL set severity to "info" and priority to "good_to_know"

### Requirement: Type-specific metadata default overrides
The system SHALL apply different default values for metadata fields based on document type.

#### Scenario: SELECTION defaults
- **WHEN** document_type is "SELECTION" and bid_security_required is not explicitly found
- **THEN** the default SHALL be false (比选 often doesn't have保证金)

#### Scenario: NEGOTIATION defaults
- **WHEN** document_type is "NEGOTIATION" and evaluation_method is not explicitly found
- **THEN** the default SHALL be "最低评标价法" (common for negotiations)
SPEC_EOF

echo "All 3 spec files created"