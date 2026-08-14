#!/usr/bin/env bash
# One-time host preparation for a fresh Ubuntu VPS (PILOT-1D-L §20).
#
# Everything a bare host needs before `scripts/deploy-prod.sh` can run, and nothing more. No
# Terraform, no Ansible, no configuration-management platform: this is one box, run once, and a
# tool that has to be learned before it can be used is not an improvement over a script that can
# be read in a minute.
#
# Idempotent — safe to re-run. Installs nothing that is already present.
#
#   curl -fsSL https://raw.githubusercontent.com/<repo>/main/scripts/bootstrap-host.sh | sudo bash
#   # or, having cloned already:
#   sudo scripts/bootstrap-host.sh
#
# What it deliberately does NOT do: clone the repository, install the age key, decrypt secrets, or
# deploy. Those need the founder's private key and judgement, and a script that pretends to handle
# secrets unattended is how private keys end up in shell history.
set -euo pipefail

[ "$(id -u)" = "0" ] || { echo "run with sudo" >&2; exit 1; }

DEPLOY_USER="${DEPLOY_USER:-deploy}"
APP_DIR="${APP_DIR:-/opt/vaylorn}"
SSH_ALLOW="${SSH_ALLOW:-}"     # e.g. SSH_ALLOW=203.0.113.10 to restrict SSH to one address

log() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

log "[1/6] base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# age + sops decrypt the secrets file on this host; postgresql-client is what the backup script
# uses for pg_dump; the rest is unavoidable plumbing.
apt-get install -y -qq --no-install-recommends \
  ca-certificates curl git ufw age postgresql-client ripgrep >/dev/null
# `sops` is not in Ubuntu's archive; install the release binary for this architecture.
if ! command -v sops >/dev/null 2>&1; then
  arch="$(dpkg --print-architecture)"
  ver="${SOPS_VERSION:-v3.9.1}"
  curl -fsSL -o /usr/local/bin/sops \
    "https://github.com/getsops/sops/releases/download/${ver}/sops-${ver}.linux.${arch}"
  chmod +x /usr/local/bin/sops
fi
echo "    git $(git --version | awk '{print $3}') · age $(age --version 2>/dev/null || echo present) · sops $(sops --version 2>/dev/null | head -1)"

log "[2/6] docker engine + compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
docker compose version >/dev/null 2>&1 || { echo "FATAL: compose plugin missing" >&2; exit 1; }
systemctl enable --now docker >/dev/null 2>&1 || true
echo "    $(docker --version) · $(docker compose version | head -1)"

log "[3/6] deploy user"
if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER" >/dev/null
fi
usermod -aG docker "$DEPLOY_USER"
# Carry root's authorised keys across so the founder can log in as the deploy user immediately;
# no new key material is generated here.
if [ -f /root/.ssh/authorized_keys ] && [ ! -s "/home/$DEPLOY_USER/.ssh/authorized_keys" ]; then
  install -d -m700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
  install -m600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
    /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

log "[4/6] application directory"
install -d -m755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR"
install -d -m750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /var/backups/vaylorn
# The decrypted secrets file lands here at deploy time. 0700 so no other account can read it even
# for the moments it exists.
install -d -m700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /run/secrets 2>/dev/null || true

log "[5/6] firewall"
# Only 80 and 443 are opened. Postgres and Redis publish no ports at all in the production compose,
# so they are unreachable regardless — this is the second lock, not the only one.
ufw --force reset >/dev/null 2>&1 || true
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
if [ -n "$SSH_ALLOW" ]; then
  ufw allow from "$SSH_ALLOW" to any port 22 proto tcp >/dev/null
  echo "    SSH restricted to $SSH_ALLOW"
else
  ufw allow OpenSSH >/dev/null
  echo "    SSH open to all — re-run with SSH_ALLOW=<your.ip> to restrict"
fi
ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status numbered | sed 's/^/    /'

log "[6/6] done"
cat <<NEXT
    Host is ready. Remaining steps need your private key and judgement:

      sudo -iu $DEPLOY_USER
      git clone <repo> $APP_DIR && cd $APP_DIR
      install -Dm600 /path/to/keys.txt ~/.config/sops/age/keys.txt
      sops -d secrets/prod.enc.yaml >/dev/null    # prove decryption BEFORE deploying
      # populate /etc/vaylorn.env (see runbooks/FIRST_PILOT_DEPLOY.md), then:
      scripts/deploy-prod.sh prod
      scripts/pilot-health-check.sh
NEXT
