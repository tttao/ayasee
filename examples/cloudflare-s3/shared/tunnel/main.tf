terraform {
  required_version = ">= 1.10.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.19"
    }
  }
}

# The provider reads CLOUDFLARE_API_TOKEN, an environment secret of each GitHub environment
# (identity: secrets). Never set api_token here.
provider "cloudflare" {}

variable "account_id" {
  description = "Cloudflare account ID (not a secret)."
  type        = string
}

variable "tunnel_id" {
  description = "ID of the existing tunnel (created once, outside ayasee, with its credentials)."
  type        = string
}

variable "ingress" {
  description = "Hostname => origin service, for every stage: one tunnel serves test and prod."
  type        = map(string)
}

# One object for all stages: this is why the unit is a shared unit (shared_units in .ayasee.yaml).
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "this" {
  account_id = var.account_id
  tunnel_id  = var.tunnel_id

  config = {
    ingress = concat(
      [for host, service in var.ingress : { hostname = host, service = service }],
      [{ service = "http_status:404" }], # the catch-all rule must come last
    )
  }
}
