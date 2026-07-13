## ADDED Requirements

### Requirement: System detects two-choice placeholders in template text

The system SHALL detect binary-choice patterns of the form `（X、Y）` or `(X/Y)` in template text during document generation, where X and Y are short (1-4 Chinese characters) opposite-meaning options.

- Pattern MUST match fullwidth and halfwidth parentheses: `（`/`)` and `(`/`)`
- Pattern MUST use Chinese comma `、` or forward slash `/` as separator
- Pattern MUST capture only 1-4 character options (to exclude descriptions, lists)
- Detection MUST be purely regex-based, no LLM dependency

#### Scenario: Detect 有/无 pattern
- **WHEN** template text contains `（有、无）`
- **THEN** system recognizes it as a two-choice placeholder

#### Scenario: Detect 是/否 pattern
- **WHEN** template text contains `（是、否）`
- **THEN** system recognizes it as a two-choice placeholder

#### Scenario: Skip non-two-choice brackets
- **WHEN** template text contains `（描述：多个要求）`
- **THEN** system does NOT treat it as a two-choice placeholder (too long)

### Requirement: System fills two-choice placeholders with positive option

The system SHALL replace each detected two-choice placeholder with the option that has positive/affirmative connotation for the bidder.

- Positive option inference rules:
  - If options include `有`/`无`: choose `有` (positive topic) or `无` (negative topic), semantics-aware
  - If options include `是`/`否`: choose `是`
  - For unknown option pairs: choose the option with positive semantic connotation
- The selected option MUST be rendered in **bold** within the text
- The parentheses and separator MUST be removed in the output
- If neither option is clearly positive, default to the first option

#### Scenario: Replace 有/无 with bold 无 for negative context
- **WHEN** template text is `我公司（有、无）记入诚信档案且在有效期内的失信行为`
- **THEN** output is `我公司**无**记入诚信档案且在有效期内的失信行为`

#### Scenario: Replace 是/否 with bold 是 for affirmative context
- **WHEN** template text is `投标公司（是、否）存在相关资质`
- **THEN** output is `投标公司**是**存在相关资质`

#### Scenario: Replace 有/无 with bold 有 for positive context
- **WHEN** template text is `投标公司（有、无）相关资质证书`
- **THEN** output is `投标公司**有**相关资质证书`

### Requirement: Positive option is context-aware

The system SHALL use a configurable keyword-based heuristic to determine if the context of a `（有、无）` placeholder is negative (undesirable topic) or positive (desirable topic).

- Negative keywords include: 犯罪, 失信, 处罚, 违规, 违法, 不良, 诉讼, 纠纷, 处罚, 处分, 处分, 黑名单, 惩戒, 禁止
- Positive keywords include: 资质, 能力, 许可, 认证, 通过, 符合, 具备
- If negative keywords found in surrounding text: choose `无`
- If positive keywords found: choose `有`
- If no keywords found: default to `有` (optimistic default for bidder)

#### Scenario: Negative keyword triggers 无
- **WHEN** text before/after contains `记入诚信档案且在有效期内的失信行为`
- **THEN** system identifies negative context and selects `无`

#### Scenario: Positive keyword triggers 有
- **WHEN** text before/after contains `相关资质证书`
- **THEN** system identifies positive context and selects `有`

#### Scenario: Unknown context defaults to 有
- **WHEN** no clear positive or negative keywords detected
- **THEN** system selects `有` as optimistic default

### Requirement: Integration with existing template fill pipeline

The system SHALL apply two-choice filling as a post-processing step after the existing `_fill_template` function completes, before final document assembly.

- Two-choice fill MUST NOT interfere with existing placeholder detection (`_identify_placeholders_via_llm`) or field mapping (`_fill_template`)
- Two-choice fill operates on plain text strings (not XML/DOM)
- Bold rendering uses markdown-style `**text**` notation within the text pipeline, translated to Word bold at rendering time

#### Scenario: Post-processing after standard fill
- **WHEN** `_fill_template` completes and returns filled text
- **THEN** two-choice fill scans the result and replaces any remaining patterns

#### Scenario: No interference with standard placeholders
- **WHEN** text contains both `XXX（公司名称）` and `（有、无）`
- **THEN** `XXX（公司名称）` is handled by existing fill logic, `（有、无）` is handled by two-choice fill

### Requirement: ContentBlock rendering path integration

The system SHALL apply two-choice filling in the ContentBlock rendering path (template_binder output), not just in the legacy TEXT_TEMPLATE path.

- During `_build_docx_bytes`, ContentBlock paragraphs from template_binder MUST be processed through `_fill_two_choice_placeholders` before being written to the document
- The `two_choice_placeholders` from `analysis_data` (containing `text_snippet` for precise matching) MUST be accessible in the rendering scope
- ContentBlock table cells MUST also be processed for two-choice patterns
- The analysis-phase `two_choice_placeholders` with `section_key` matching takes priority over runtime keyword guessing

#### Scenario: ContentBlock paragraph with 有/无 is filled
- **GIVEN** a chapter has a commitment letter template containing `我公司（有、无）记入诚信档案且在有效期内的失信行为`
- **AND** `analysis_data.two_choice_placeholders` has an entry with `section_key` matching this chapter
- **WHEN** the ContentBlock paragraph is rendered in `_build_docx_bytes`
- **THEN** the `（有、无）` is replaced with `**无**` (based on analysis-phase selection)
- **AND** the `**无**` is rendered as bold text in the output document
