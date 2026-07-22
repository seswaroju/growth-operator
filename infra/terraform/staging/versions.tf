# Staging infrastructure (MVP-009) — SCAFFOLD, NOT YET APPLIED.
# BLOCKED on: Hetzner account + API token, a domain + DNS provider, the data-residency
# decision (BLOCKERS.md #8 — Hetzner EU vs India VPS), and Meta test-number access.
# Do not `terraform apply` until those exist and the founder approves provisioning.
terraform {
  required_version = ">= 1.6"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.48"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}
