## MODIFIED Requirements

### Requirement: Read-only plan of changed first-stage units
`plan-first-stage` SHALL check out the PR head with `persist-credentials: false`, plan only the changed units of the first stage with `binding-mode: same-job`, `encrypt: false`, `lock: false` and `allow-no-units: true`, keep plan files on the runner, run the policy pack with that stage only when a plan was made, and write the comment text headed "Plan of `<stage>`". When the first stage's units directory exists but holds no unit, the plan SHALL pass and the comment SHALL say that there are no units under `<units_root>/<stage>` and nothing was planned (never that there are no IaC changes), naming the directory only when its path is plain (letters, digits, `.`, `_`, `-`, `/`), and SHALL warn when the base commit has units in that stage.

#### Scenario: State lock
- **WHEN** a PR plan runs while a staged apply holds the lock
- **THEN** the PR plan does not take or wait for the lock

#### Scenario: Callers before the first unit
- **WHEN** a pull request changes `.ayasee.yaml` and the callers while `<units_root>/<first stage>` holds only a `.gitkeep`
- **THEN** `plan-first-stage` and `plan-dev` pass, the policy pack does not run, and the comment says that there are no units under that directory and nothing was planned

#### Scenario: Pull request removes every first-stage unit
- **WHEN** the base commit has units under the first stage's directory and the pull request leaves none
- **THEN** the comment says nothing was planned and warns that the base commit has units there
