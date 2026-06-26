## ADDED Requirements

### Requirement: 知识库引用回溯校验
When the generation phase produces content that references ("cites") knowledge base materials, the system SHALL verify that the referenced content actually exists in the specified knowledge base and SHALL confirm the引用 is factually accurate.

#### Scenario: 验证知识库引用真实性
- **WHEN** the generation LLM outputs text that includes "根据知识库资料显示..." or similar引用 phrasing
- **THEN** the system SHALL perform a verification step: search the referenced knowledge base Chroma collection with the cited content as query
- **AND** if the cited content does not match any knowledge base entry above a similarity threshold, the system SHALL flag the引用 as UNVERIFIED

#### Scenario: 验证通过后引用
- **WHEN** the verification confirms the cited content exists in the knowledge base
- **THEN** the引用 SHALL be marked as VERIFIED and included in the generated output

### Requirement: 引用置信度标识
Each piece of content in the generated bid that references external sources SHALL be annotated with the source type and confidence level (TENDER_ORIGINAL / KNOWLEDGE_BASE_VERIFIED / KNOWLEDGE_BASE_UNVERIFIED / AI_REASONED).

#### Scenario: 内容溯源标注
- **WHEN** generating the bid document
- **THEN** each section SHALL have a metadata tag indicating the source of its core content
- **AND** the coverage snapshot SHALL include source provenance for each requirement item
