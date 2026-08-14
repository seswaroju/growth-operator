# Secrets (SOPS + age) — MVP-008

Real secrets live **encrypted** in git as `secrets/<env>.enc.yaml` (dev/staging/prod),
encrypted with [SOPS](https://github.com/getsops/sops) + [age]. They are decrypted at
container boot to a plaintext file that `core/common/config.py` (`SopsSecretsSource`) reads
via `GROWTH_OPERATOR_SECRETS_FILE`.

> ⚠️ **Never commit plaintext secrets or age private keys.** Only `*.enc.yaml` (encrypted)
> and `*.example.yaml` (fake values) belong in git. The gitleaks pre-commit hook blocks
> accidental plaintext secrets.

## One-time setup (founder)
```bash
brew install sops age
age-keygen -o ~/.config/sops/age/keys.txt        # note the public key (age1...)
# paste that public key into .sops.yaml (creation_rules[].age)
```

## Create / edit an encrypted secrets file
```bash
cp secrets/dev.example.yaml /tmp/dev.plain.yaml   # fill in real values
sops --encrypt /tmp/dev.plain.yaml > secrets/dev.enc.yaml
rm /tmp/dev.plain.yaml
# later edits: sops secrets/dev.enc.yaml
```

## Boot-time decryption
`scripts/decrypt-secrets.sh <env>` decrypts `secrets/<env>.enc.yaml` to a runtime path and
exports `GROWTH_OPERATOR_SECRETS_FILE`. In staging/prod set
`GROWTH_OPERATOR_REQUIRE_SECRETS_FILE=true` so boot **hard-fails** (clear message) if the
age key is missing or decryption produced no file — never a silent fallback to defaults.

## Status
Scaffold only (MVP-008). The age keypair and the real `*.enc.yaml` files are the founder's
step — no keys or real secrets are committed here.

---

## Production (PILOT-1A)

`prod.example.yaml` is the canonical list of what a live pilot needs. It contains **names and
placeholders only** — no value in it is real, which is why it is safe in git.

### Why a forgotten value is now a failed deploy

Outside `dev`, `core/common/safety.py` refuses to start any of the three processes if a setting is
still the published development default. Before PILOT-1A, setting `env=prod` changed nothing:
sessions would have been signed with `dev-only-insecure-secret` (anyone could mint a token for any
merchant) and Meta webhooks validated against `dev-whatsapp-app-secret` (forged inbound messages
would be indistinguishable from real ones). Both failed silently, which is what made them
dangerous — the system looked entirely healthy.

### Founder one-time setup

```bash
brew install sops age
age-keygen -o ~/.config/sops/age/keys.txt     # prints the PUBLIC recipient (age1...)
```

1. Paste the **public** key into `.sops.yaml`, replacing the placeholder recipient.
2. Build the plaintext file from the schema:
   ```bash
   cp secrets/prod.example.yaml /tmp/prod.plain.yaml
   # replace EVERY value; generation commands are in the comments
   sops --encrypt /tmp/prod.plain.yaml > secrets/prod.enc.yaml
   rm /tmp/prod.plain.yaml
   ```
3. Commit `secrets/prod.enc.yaml` — encrypted, so this is intended.
4. Install the **private** key on the pilot host at `~/.config/sops/age/keys.txt`, mode `0600`,
   copied by hand or through a founder-controlled channel.

### The private key does not go into GitHub Actions

It would make production credentials decryptable by anyone who can trigger a workflow, and by
GitHub. The host holds the key; CI only tells the host to deploy. If unattended deploys later need
it, that is a deliberate design decision with its own review — not a convenience taken here.

### At deploy

`scripts/deploy-prod.sh` runs `scripts/decrypt-secrets.sh prod` first. The decrypted file lands at
`GROWTH_OPERATOR_SECRETS_FILE` (mounted read-only into the containers) and never touches the image
— a secret in an image layer is a secret in every registry that image reaches. With
`require_secrets_file=true`, a container whose decryption step did not run refuses to boot.
