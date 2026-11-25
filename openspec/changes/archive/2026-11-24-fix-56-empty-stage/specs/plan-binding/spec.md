## ADDED Requirements

### Requirement: A stage without units is explicit
The plan and apply actions SHALL discover units in the main shell, so that a missing units directory, a discovery failure or a duplicate unit name stops the script at once. A units directory that exists but holds no unit SHALL fail with `no units found under <dir>`, unless the plan action's `allow-no-units` input is `true`; the plan action SHALL then plan nothing and succeed with only the outputs `no-units=true` and `has-changes=false`, writing no manifest, change set, summary, plan file or plan JSON. `allow-no-units` SHALL be refused with the `drift` and `breakglass` binding modes and with a value other than `true` or `false`, SHALL never cover a missing units directory, and the apply action SHALL never accept a stage without units. When units exist, the plan action SHALL output `no-units=false`.

#### Scenario: Empty stage without the opt-in
- **WHEN** the plan action runs for a stage whose directory holds only a `.gitkeep`, with `allow-no-units` false
- **THEN** it fails with `no units found under` that directory, before any binding step

#### Scenario: Empty stage with the opt-in
- **WHEN** the plan action runs for that stage with `allow-no-units` true
- **THEN** it succeeds with `no-units=true` and `has-changes=false` and writes no manifest or plan

#### Scenario: Discovery failure with the opt-in
- **WHEN** two unit directories map to the same unit name and `allow-no-units` is true
- **THEN** the plan action fails with `duplicate unit name` and never reports `no-units`

#### Scenario: Missing directory
- **WHEN** `<units_root>/<stage>` does not exist and `allow-no-units` is true
- **THEN** the plan action fails with `units directory not found`
