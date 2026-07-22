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
