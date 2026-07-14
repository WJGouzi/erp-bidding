## ADDED Requirements

### Requirement: Bidding_info budget with package budget fallback chain

When the bidding document has multiple packages and a package is selected, `bidding_info.budget.total` SHALL follow a three-level fallback chain: package budget → project total → empty.

#### Scenario: Package budget found in metadata.budget.packages
- **WHEN** `analysis_data.metadata.budget.packages` contains a key matching `str(selected_package_no)`
- **AND** the value is a non-empty string
- **THEN** `bidding_info.budget.total` SHALL be set to that value (e.g., `"274万元"`)

#### Scenario: Package budget not found, fallback to project total
- **WHEN** `budget.packages` is empty, or no matching key found
- **THEN** `bidding_info.budget.total` SHALL fall back to `metadata.budget.total`

#### Scenario: No budget data at all
- **WHEN** neither `budget.packages` nor `budget.total` has a value
- **THEN** `bidding_info.budget.total` SHALL return `0` (current behavior unchanged)

#### Scenario: Budget field is always string type
- **WHEN** budget is returned
- **THEN** the `total` field SHALL be a string, never converted to int

### Requirement: Packages endpoint includes budget per package

The `GET /bidding/tasks/:id/packages` endpoint SHALL return a `budget` field for each package in the response.

#### Scenario: Budget found in analysis_data.packages
- **WHEN** `analysis_data.packages[]` has a matching entry with non-empty `budget`
- **THEN** the response SHALL include `"budget": "<value>"`

#### Scenario: Budget not found
- **WHEN** no matching entry or budget field is empty
- **THEN** `"budget": ""` (empty string)
