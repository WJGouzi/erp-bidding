## ADDED Requirements

### Requirement: 标书类型差异化生成提示
`_get_bid_type_prompt_profile` SHALL return meaningful, differentiated prompt directives for each of the three bid types (GOODS, SERVICE, ENGINEERING), rather than the current empty fallback.

#### Scenario: 货物类标书提示
- **WHEN** generating content for a GOODS-type bid
- **THEN** the generation prompt SHALL include guidance specific to goods procurement: product specifications, delivery terms, quality inspection, warranty terms

#### Scenario: 服务类标书提示
- **WHEN** generating content for a SERVICE-type bid
- **THEN** the generation prompt SHALL include guidance specific to service procurement: service scope, team composition, SLA commitments, quality assurance

#### Scenario: 工程类标书提示
- **WHEN** generating content for an ENGINEERING-type bid
- **THEN** the generation prompt SHALL include guidance specific to engineering: construction plan, resource allocation, safety measures, schedule management
