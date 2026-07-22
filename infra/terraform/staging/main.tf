# Staging VPS mirroring prod (MVP-009). SCAFFOLD — see versions.tf for the BLOCKED note.

resource "hcloud_ssh_key" "deploy" {
  name       = "${var.server_name}-deploy"
  public_key = var.ssh_public_key
}

resource "hcloud_firewall" "staging" {
  name = "${var.server_name}-fw"

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
}

resource "hcloud_server" "staging" {
  name         = var.server_name
  server_type  = var.server_type
  image        = "docker-ce" # docker preinstalled
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.deploy.id]
  firewall_ids = [hcloud_firewall.staging.id]

  labels = {
    env     = "staging"
    project = "growth-operator"
  }
}

# DNS: intentionally omitted until a domain + DNS provider are chosen. When decided, add
# the matching provider (e.g. cloudflare_record / hetznerdns_record) mapping
# api.${var.domain} -> hcloud_server.staging.ipv4_address.
#
# Meta test-number webhook wiring (ticket scope) also lands here once WhatsApp API access
# is granted — configure the webhook callback URL to https://api.${var.domain}/... .
