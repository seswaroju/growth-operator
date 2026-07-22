output "staging_ipv4" {
  value       = hcloud_server.staging.ipv4_address
  description = "Point the api.<domain> DNS record here once a domain is chosen."
}

output "staging_server_id" {
  value = hcloud_server.staging.id
}
