# Staging environment (MVP-009) — SCAFFOLD, BLOCKED

This is un-applied infrastructure-as-code. **Do not `terraform apply`** until the
prerequisites below exist and the founder approves provisioning (§8/§10.5).

## Blocked on
1. **Hetzner Cloud account + API token** (`TF_VAR_hcloud_token`).
2. **A domain + DNS provider** (`api.staging.<domain>`) — none chosen yet; DNS resource is
   intentionally omitted from `main.tf`.
3. **Data-residency decision** — Hetzner EU vs an India VPS (BLOCKERS.md #8, DPDP posture).
   `var.location` defaults to EU (`nbg1`) as a placeholder only.
4. **Meta WhatsApp test-number access** — pending API access; webhook wiring lands when it
   arrives.

## What exists here
- `versions.tf` / `variables.tf` / `main.tf` / `outputs.tf` — a Hetzner CPX21 server +
  firewall + SSH key. DNS + Meta webhook are stubbed with TODO comments.
- `../../../.github/workflows/deploy-staging.yml` — deploy-on-merge-to-main workflow
  (migrations before container swap, `/readyz` smoke). Gated behind a `staging` environment
  and repo secrets; a no-op until those are configured.

## When unblocked (rough order)
1. Founder decides residency + domain, creates the Hetzner token, sets repo secrets.
2. `terraform -chdir=infra/terraform/staging init && terraform plan` (review), then apply.
3. Add the DNS record resource for the chosen provider.
4. Enable the deploy workflow's environment; confirm `merge -> staging <5min incl. migrations`.
5. Wire the Meta test number webhook once WhatsApp access is granted.
