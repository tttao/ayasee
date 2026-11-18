"""Issue labels shared by the guard, the issue commands and the drift check."""

STOP_LABEL = "ayasee:stop-the-line"
APPLY_FAILED_LABEL = "ayasee:apply-failed"  # lets nightly drift skip an env with a failed apply
DRIFT_LABEL = "ayasee:drift"
POST_APPROVAL_LABEL = "ayasee:post-approval"  # break-glass use awaiting after-the-fact approval
PROD_APPROVAL_LABEL = "ayasee:prod-approval"  # gate_mechanism: workflow, a prod plan to approve
BREAKGLASS_APPROVAL_LABEL = "ayasee:breakglass-approval"  # workflow gate, a break-glass plan
