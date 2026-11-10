resource "terraform_data" "r" {
  input = "ayasee-fixture-secret-marker-bad"

  provisioner "local-exec" {
    command = "exit 1"
  }
}
