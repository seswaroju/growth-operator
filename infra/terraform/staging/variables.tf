variable "hcloud_token" {
  type        = string
  sensitive   = true
  description = "Hetzner Cloud API token (provide via TF_VAR_hcloud_token or a tfvars file; never commit)."
}

variable "server_name" {
  type    = string
  default = "gop-staging"
}

variable "server_type" {
  type    = string
  default = "cpx21" # 3 vCPU / 4GB — per the ticket
}

variable "location" {
  type        = string
  default     = "nbg1" # Nuremberg (EU). DATA-RESIDENCY DECISION PENDING — BLOCKERS #8.
  description = "Hetzner location. EU (nbg1/fsn1/hel1) vs an India VPS is undecided (DPDP)."
}

variable "ssh_public_key" {
  type        = string
  description = "SSH public key authorized on the staging box."
}

variable "domain" {
  type        = string
  default     = ""
  description = "Staging domain, e.g. staging.gop.dev. Empty until a domain + DNS provider are chosen."
}
