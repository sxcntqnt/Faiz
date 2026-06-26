#!/usr/bin/env bash
 

set -euo pipefail
 

# ==========================================================
# Logging
# ==========================================================
 

log_info() {
echo "[INFO] $*"
}
 

log_warn() {
echo "[WARN] $*"
}
 

log_error() {
echo "[ERROR] $*" >&2
}
 

# ==========================================================
# Helpers
# ==========================================================
 

have_command() {
command -v "$1" >/dev/null 2>&1
}
 

apt_install() {
apt-get install -y "$@"
}
 

ensure_dependency() {
local binary="$1"
local installer="$2"
 

if have_command "$binary"; then
log_info "$binary already installed"
return
fi
 

log_info "Installing $binary"
"$installer"
}
 

# ==========================================================
# Repository Setup
# ==========================================================
 

setup_cloudflared_repo() {
 

[ -f /etc/apt/sources.list.d/cloudflared.list ] && return
 

curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
| gpg --dearmor \
-o /usr/share/keyrings/cloudflare-main.gpg
 

cat >/etc/apt/sources.list.d/cloudflared.list <<EOF
deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main
EOF
 

apt-get update
}
 

setup_hashicorp_repo() {
 

[ -f /etc/apt/sources.list.d/hashicorp.list ] && return
 

curl -fsSL https://apt.releases.hashicorp.com/gpg \
| gpg --dearmor \
-o /usr/share/keyrings/hashicorp-archive-keyring.gpg
 

cat >/etc/apt/sources.list.d/hashicorp.list <<EOF
deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main
EOF
 

apt-get update
}
 

setup_caddy_repo() {
 

[ -f /etc/apt/sources.list.d/caddy-stable.list ] && return
 

apt_install debian-keyring debian-archive-keyring
 

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
| gpg --dearmor \
-o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
 

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
> /etc/apt/sources.list.d/caddy-stable.list
 

apt-get update
}
 

setup_clickhouse_repo() {
 

[ -f /etc/apt/sources.list.d/clickhouse.list ] && return
 

curl -fsSL https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key \
| gpg --dearmor \
-o /usr/share/keyrings/clickhouse-keyring.gpg
 

cat >/etc/apt/sources.list.d/clickhouse.list <<EOF
deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg] https://packages.clickhouse.com/deb stable main
EOF
 

apt-get update
}
 

# ==========================================================
# Installers
# ==========================================================
install_base_system() {
 

log_info "Installing base system packages"
 

export DEBIAN_FRONTEND=noninteractive
 

apt-get update
 

apt_install \
curl \
wget \
git \
gnupg \
ca-certificates \
lsb-release \
procps \
unzip \
tar
}
 

install_dev_tools() {
 

log_info "Installing development tools"
 

apt_install \
build-essential \
tmux \
jq \
yq
}
 

install_nginx() {
log_info "Installing Nginx"
 

apt_install nginx
 

mkdir -p /etc/nginx/flamos
 

systemctl enable nginx >/dev/null 2>&1 || true
}
 

install_certbot() {
 

log_info "Installing certbot"
 

apt_install certbot python3-certbot-nginx
 

# ensure auto-renew timer is enabled
systemctl enable certbot.timer >/dev/null 2>&1 || true
systemctl start certbot.timer >/dev/null 2>&1 || true
 

 

        cat >/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
        #!/usr/bin/env bash
        systemctl reload nginx
        EOF
}
 

chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
 

}
 

install_edge_stack() {
 

log_info "Installing edge stack (Nginx + TLS automation)"
 

export DEBIAN_FRONTEND=noninteractive
 

apt_install nginx
 

systemctl enable nginx >/dev/null 2>&1 || true
 

install_certbot
}
 

install_nodejs() {
 

curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
 

apt_install nodejs
}
 

install_pnpm() {
 

if ! have_command npm; then
install_nodejs
fi
 

npm install -g pnpm
}
 

install_go() {
 

apt_install golang-go
}
 

ensure_go() {
 

local min="1.24"
 

if ! have_command go; then
log_info "Installing Go"
install_go
return
fi
 

local current
 

current=$(go version | awk '{print $3}' | sed 's/go//')
 

if ! printf '%s\n%s\n' "$min" "$current" | sort -VC; then
log_warn "Go version too old ($current), upgrading"
install_go
fi
}
 

install_cloudflared() {
 

setup_cloudflared_repo
 

apt_install cloudflared
}
 

install_caddy() {
 

setup_caddy_repo
 

apt_install caddy
}
 

install_redis() {
 

apt_install redis-server
}
 

install_clickhouse() {
 

setup_clickhouse_repo
 

apt_install clickhouse-server clickhouse-client
}
 

install_vault() {
 

setup_hashicorp_repo
 

apt_install vault
}
 

install_dgraph() {
 

local tmp
 

tmp=$(mktemp)
 

curl -fsSL https://get.dgraph.io -o "$tmp"
 

bash "$tmp"
 

rm -f "$tmp"
}
 

install_uv() {
 

curl -LsSf https://astral.sh/uv/install.sh | sh
}
 

install_tmux() {
 

apt_install tmux
}
 

install_jq() {
 

apt_install jq
}
 

install_yq() {
 

apt_install yq
}
 

install_build_tools() {
 

apt_install build-essential
}
 

install_rebar3() {
 

apt_install rebar3
}
 

install_tailscale() {
 

curl -fsSL https://tailscale.com/install.sh | sh
}
 

install_erlang_elixir() {
 

apt_install erlang elixir
}
 

# ==========================================================
# Dependency Bootstrap
# ==========================================================
 

install_flamos_dependencies() {
 

log_info "Installing FlamOS dependencies"
 

export DEBIAN_FRONTEND=noninteractive
 

apt-get update
 

install_base_system
install_dev_tools
 

ensure_dependency node install_nodejs
ensure_dependency pnpm install_pnpm
 

ensure_go
 

ensure_dependency cloudflared install_cloudflared
ensure_dependency caddy install_caddy
ensure_dependency redis-cli install_redis
ensure_dependency clickhouse-client install_clickhouse
ensure_dependency vault install_vault
ensure_dependency dgraph install_dgraph
ensure_dependency uv install_uv
ensure_dependency tmux install_tmux
ensure_dependency jq install_jq
ensure_dependency yq install_yq
ensure_dependency rebar3 install_rebar3
ensure_dependency tailscale install_tailscale
ensure_dependency erl install_erlang_elixir
 

if ! dpkg -s build-essential >/dev/null 2>&1; then
log_info "Installing build-essential"
install_build_tools
fi
 

log_info "FlamOS dependency installation complete"
}
