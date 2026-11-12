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

variable "ssh_hostname" {
  description = "Hostname of the SSH endpoint of the host that runs every stage."
  type        = string
}

variable "allowed_emails" {
  description = "People who may open SSH to the host. A change here is a change of who has access."
  type        = list(string)
}

resource "cloudflare_zero_trust_access_policy" "ssh" {
  account_id = var.account_id
  name       = "SSH to the host"
  decision   = "allow"
  include    = [for e in var.allowed_emails : { email = { email = e } }]
}

resource "cloudflare_zero_trust_access_application" "ssh" {
  account_id       = var.account_id
  name             = "SSH to the host"
  domain           = var.ssh_hostname
  type             = "ssh"
  session_duration = "8h"

  policies = [{ id = cloudflare_zero_trust_access_policy.ssh.id, precedence = 1 }]
}
