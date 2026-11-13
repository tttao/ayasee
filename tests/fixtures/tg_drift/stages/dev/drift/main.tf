resource "terraform_data" "r" {
  input            = "ayasee-fixture-secret-marker-drift"
  triggers_replace = timestamp()
}
