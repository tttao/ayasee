# Who owns what: Terraform or Declarative Automation Bundles

One object has one owner. This repository (OpenTofu through ayasee) owns the platform. Your
workload repositories use Declarative Automation Bundles (the former Databricks Asset Bundles) for
what runs on it. Two tools writing the same object fight over it, and the nightly drift check reports
the fight as drift.

## The contract

| Owned by Terraform (this repository) | Owned by Declarative Automation Bundles (workload repositories) |
|---|---|
| Azure resources: workspace, networking, access connector, storage | Jobs |
| Metastore assignment | Pipelines |
| Catalogs | Notebooks and other workspace files |
| Storage credentials | Models and model serving endpoints |
| External locations | Dashboards, alerts, experiments |
| Grants (`databricks_grants` on catalogs; schemas and below only when this repository creates them) | Schemas, tables and volumes created by those jobs, and the grants a bundle needs on its own objects |

The metastore itself is account-level. The account admin creates it once, outside this repository
(one per region, shared by every workspace of the account). This repository owns only its
assignment to the workspace. That narrows D13, which lists "metastore" as Terraform-owned: creating
it needs account-admin rights that a stage identity must not hold, and it is a one-off, not
something to plan and review on every merge.

Rules:

1. Bundles never create, change or delete a catalog, storage credentials, external locations or
   the metastore assignment, and never grant on a catalog.
2. Bundles reference Terraform-owned objects **by name**, as variables (`catalog: ${var.catalog}`),
   never by creating a copy. The names are the outputs and inputs of this repository, for example
   catalog `example_prod`.
3. Terraform never creates a job, pipeline, notebook or model. It may grant on them only through a
   `databricks_permissions` that the workload team asks for here, and then Terraform is that
   object's permission owner.
4. A grant has one owner too: `databricks_grants` is authoritative for a catalog, so anything
   granted on the catalog by hand or by a bundle is removed on the next apply. Ask for a grant by a
   pull request to `stages/<stage>/grants/terragrunt.hcl`.
5. Changes land through the ayasee pipeline. A catalog change, and the grants that depend on it,
   go in separate merges (README, "Landing dependent changes").

## Handing an object over

An object moves between the owners in three steps, never in one commit. Do it in dev, then uat,
then prod, one stage at a time.

**From a Bundle to Terraform** (for example a catalog a team created from a bundle):

1. Unbind the object from the bundle (`databricks bundle deployment unbind <resource key>`), then
   remove the resource from the bundle configuration. Deploy the bundle and check that the plan
   shows no deletion: the object must still exist afterwards.
2. In this repository add the resource and an `import` block (or `tofu import`) for the existing
   object, in a pull request. The plan must show an import and no create, replace or destroy.
3. Merge. The ledger now records the object under Terraform. Update the bundle to reference it by
   name.

**From Terraform to a Bundle** (for example a schema a team wants to evolve themselves):

1. In this repository add a `removed` block with `lifecycle { destroy = false }` for the
   resource (OpenTofu 1.7 and later) and merge it. The plan must show a forget, not a delete.
2. Add the resource to the bundle and bind it to the existing object with
   `databricks bundle deployment bind`. The bundle plan must show no create.
3. Remove the `removed` block in a later pull request.

Check both sides before each step: the plan of this repository (the PR comment) and the bundle's
`validate` output. A plan with a delete or replace of a catalog, storage credential or external
location is a stop: data is reachable through them. The policy pack and the risk rules flag it.
