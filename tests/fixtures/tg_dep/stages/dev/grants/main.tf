variable "upstream" {
  type = string
}

variable "tag" {
  type = string
}

resource "terraform_data" "grants" {
  input = {
    upstream = var.upstream
    tag      = var.tag
  }
}
