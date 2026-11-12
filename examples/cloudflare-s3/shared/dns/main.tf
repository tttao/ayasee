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

variable "zone_id" {
  description = "Cloudflare zone ID (not a secret)."
  type        = string
}

variable "records" {
  description = "Proxied CNAME records of one stage: hostname => target (the tunnel's cfargotunnel.com name)."
  type        = map(string)
}

resource "cloudflare_dns_record" "this" {
  for_each = var.records

  zone_id = var.zone_id
  name    = each.key
  type    = "CNAME"
  content = each.value
  proxied = true
  ttl     = 1 # automatic; required by the API, ignored for proxied records
}
