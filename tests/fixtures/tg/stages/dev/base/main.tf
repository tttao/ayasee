resource "terraform_data" "base" {
  input = "ayasee-fixture-secret-marker-base"
}

output "id" {
  value = terraform_data.base.id
}
