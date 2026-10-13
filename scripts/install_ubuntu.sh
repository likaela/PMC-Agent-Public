#!/usr/bin/env bash
# Run explicitly on an Ubuntu host. Does not change any business data.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-docker}"
source /etc/os-release
[[ "${ID}" == ubuntu && ( "${VERSION_ID}" == 22.04 || "${VERSION_ID}" == 24.04 ) ]] || {
  echo 'Supported hosts: Ubuntu 22.04/24.04' >&2; exit 1;
}
if [[ "${EUID}" == 0 ]]; then sudo_cmd=(); else sudo_cmd=(sudo); fi
as_service() {
  if [[ "${EUID}" == 0 ]]; then runuser -u pmc-agent -- "$@"; else sudo -u pmc-agent "$@"; fi
}

if [[ "$mode" == docker ]]; then
  # Official Docker apt repository; no curl | sh installer.
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    if dpkg-query -W -f='${Status}' docker.io 2>/dev/null | grep -q 'install ok installed'; then
      echo 'Existing docker.io detected. Follow Docker migration instructions; no packages removed automatically.' >&2
      exit 1
    fi
    "${sudo_cmd[@]}" apt-get update
    "${sudo_cmd[@]}" apt-get install -y ca-certificates curl git
    "${sudo_cmd[@]}" install -m 0755 -d /etc/apt/keyrings
    "${sudo_cmd[@]}" curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    "${sudo_cmd[@]}" chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
      "$(dpkg --print-architecture)" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" | "${sudo_cmd[@]}" tee /etc/apt/sources.list.d/pmc-docker.list >/dev/null
    "${sudo_cmd[@]}" apt-get update
    "${sudo_cmd[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  "${sudo_cmd[@]}" systemctl enable --now docker
  "${sudo_cmd[@]}" docker compose version
  echo "Docker ready. In $project_dir: sudo docker compose up -d --build"
  exit 0
fi

[[ "$mode" == native ]] || { echo 'Usage: bash scripts/install_ubuntu.sh [docker|native]' >&2; exit 1; }
[[ "$VERSION_ID" == 24.04 ]] || { echo 'Ubuntu 22.04: use Docker for Python 3.12/Node 22.' >&2; exit 1; }
[[ "$project_dir" == /opt/PMC-Agent ]] || { echo 'Native install requires clone at /opt/PMC-Agent (see deployment guide).' >&2; exit 1; }
"${sudo_cmd[@]}" apt-get update
"${sudo_cmd[@]}" apt-get install -y git python3 python3-venv python3-pip nginx curl ca-certificates xz-utils
python3 -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'

# Install an official, checksum-verified Node 22 archive only if Node is absent.
if ! command -v node >/dev/null 2>&1; then
  case "$(uname -m)" in x86_64) node_arch=x64 ;; aarch64) node_arch=arm64 ;; *) echo 'Unsupported Node architecture' >&2; exit 1 ;; esac
  node_version=22.22.0
  node_archive="node-v${node_version}-linux-${node_arch}.tar.xz"
  node_temp="$(mktemp -d -t pmc-node-XXXXXXXX)"
  curl -fsSL "https://nodejs.org/dist/v${node_version}/${node_archive}" -o "$node_temp/$node_archive"
  curl -fsSL "https://nodejs.org/dist/v${node_version}/SHASUMS256.txt" -o "$node_temp/SHASUMS256.txt"
  ( cd "$node_temp"; grep " ${node_archive}\$" SHASUMS256.txt | sha256sum -c - )
  "${sudo_cmd[@]}" tar -xJf "$node_temp/$node_archive" -C /usr/local --strip-components=1
  echo "Verified Node archive retained at $node_temp"
fi
node -e 'const v=process.versions.node.split(".").map(Number); if(v[0]<22 || (v[0]===22 && v[1]<12)) process.exit(1)'
cd "$project_dir"
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
[[ -f .env ]] || cp .env.example .env
"${sudo_cmd[@]}" getent passwd pmc-agent >/dev/null || "${sudo_cmd[@]}" useradd --system --home /opt/PMC-Agent --shell /usr/sbin/nologin pmc-agent
"${sudo_cmd[@]}" chown "$(id -un):pmc-agent" .env
"${sudo_cmd[@]}" chmod 640 .env
"${sudo_cmd[@]}" install -d -o pmc-agent -g pmc-agent -m 0750 data/runtime
as_service venv/bin/python -m scripts.run_public --init-only
( cd frontend; npm ci; VITE_API_BASE_URL=/ npm run build )
"${sudo_cmd[@]}" install -d -m 0755 /var/www/pmc-agent
"${sudo_cmd[@]}" cp -a frontend/dist/. /var/www/pmc-agent/
"${sudo_cmd[@]}" cp deploy/systemd/pmc-agent.service /etc/systemd/system/pmc-agent.service
"${sudo_cmd[@]}" cp deploy/nginx/pmc-agent.conf /etc/nginx/sites-available/pmc-agent
"${sudo_cmd[@]}" ln -sfn /etc/nginx/sites-available/pmc-agent /etc/nginx/sites-enabled/pmc-agent
echo 'If the nginx default site is enabled, select this site as default in nginx before proceeding (guide has exact commands).'
"${sudo_cmd[@]}" nginx -t
"${sudo_cmd[@]}" systemctl daemon-reload
echo 'Install complete. Start with: bash scripts/start_production.sh native'
