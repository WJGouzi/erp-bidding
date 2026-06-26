## ADDED Requirements

### Requirement: 分析阶段接收招标文件全文
`_extract_structured_analysis_with_llm` SHALL receive the complete cleaned text of the tender document, not a truncated excerpt. The structured extraction SHALL cover content from all sections of the tender, including attachments.

#### Scenario: 200页招标文件全文分析
- **WHEN** a 200-page tender document (approx. 100,000 characters) is uploaded
- **THEN** the structured analysis result SHALL contain extracted information from sections across the entire document, not just the first 6000 characters
- **AND** the `analysis_data` JSON SHALL include business/technical requirements, qualification criteria, and scoring items from all parts of the document

### Requirement: 生成Prompt注入招标文件全文相关片段
`_generate_chapter_content` SHALL NOT use `effective_text[:3000]` as the sole tender text source. Instead, it SHALL retrieve relevant excerpts from the full tender document via Chroma semantic search for each chapter being generated.

#### Scenario: 章节生成时检索招标文件
- **WHEN** generating a chapter titled "技术参数响应"
- **THEN** the system SHALL query the `"bidding"` Chroma collection with the chapter title and description
- **AND** include the top-K semantically relevant chunks in the generation Prompt

### Requirement: 生成阶段启用招标文件Chroma向量检索
The generation phase SHALL query the `"bidding"` Chroma collection (where the tender document is stored) in addition to knowledge base and product library collections. This ensures the full tender text is accessible via semantic search during content generation.

#### Scenario: 检索招标文件Chroma向量
- **WHEN** generating content for any chapter
- **THEN** `_build_knowledge_base_context` or a new context builder SHALL also query the `CHROMA_COLLECTION` (configured as `"bidding"`) 
- **AND** include relevant tender document snippets in the generation context

### Requirement: 知识库检索不截断
The knowledge base context builder SHALL NOT truncate retrieved snippets to 500 characters. It SHALL provide sufficiently complete context for the generation LLM to accurately reference knowledge base materials.

#### Scenario: 知识库长文本检索
- **WHEN** a knowledge base document chunk exceeds 500 characters
- **THEN** the snippet SHALL include the full chunk text (as stored in Chroma)
- **AND** the `top_k` SHALL be increased beyond 5 to ensure broader coverage

### Requirement: 产品库检索不截断
The product library context builder SHALL NOT truncate retrieved snippets to 300 characters.

#### Scenario: 产品库长文本检索
- **WHEN** a product library document chunk exceeds 300 characters
- **THEN** the snippet SHALL include the full chunk text
- **AND** `top_k` SHALL be increased beyond 3
