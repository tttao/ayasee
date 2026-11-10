variable "marker" {
  type = string
}

resource "terraform_data" "base" {
  input = var.marker
}

output "marker" {
  value = terraform_data.base.input
}
