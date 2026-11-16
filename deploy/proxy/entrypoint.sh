#!/bin/sh
# Generates the Caddyfile from FINANCE360_ALLOWED_HOSTS (one list to maintain, ADR-0012), then runs
# Caddy. Each entry's port is dropped: Caddy listens on 443 inside the container, and the compose
# file maps FINANCE360_HTTPS_PORT to it. Entries are validated; nothing else reaches the Caddyfile.
set -euf  # -f: no globbing, so an entry like "*" is validated (and rejected), never expanded

upstream="finance360:${FINANCE360_PORT:-8000}"
raw=${FINANCE360_ALLOWED_HOSTS:-localhost,127.0.0.1}
names=""
for entry in $(printf '%s' "$raw" | tr ',' ' '); do
  name=${entry%%:*}
  case "$name" in
    ''|*[!A-Za-z0-9.-]*) echo "proxy: bad FINANCE360_ALLOWED_HOSTS entry: $entry" >&2; exit 1 ;;
  esac
  case " $names " in *" $name "*) ;; *) names="$names $name" ;; esac
  # A browser sends no SNI for an IP address, so Caddy needs a default certificate: the first IP
  # entry (the LAN address), else the first name.
  case "$name" in
    *[!0-9.]*) ;;
    *) [ -n "${ip:-}" ] || ip=$name ;;
  esac
done
[ -n "$names" ] || { echo "proxy: FINANCE360_ALLOWED_HOSTS is empty" >&2; exit 1; }
first=${names# }
default_sni=${ip:-${first%% *}}
sites=$(printf '%s' "$names" | sed 's/^ //; s/ /, /g')

cat > /tmp/Caddyfile <<CADDY
{
	admin off
	auto_https disable_redirects
	skip_install_trust
	default_sni $default_sni
}

# Certificates (from Caddy's own local CA) for each allowed name.
$sites {
	tls internal
	reverse_proxy $upstream
}

# Any other Host still reaches the app, which answers 421 (one place decides).
:443 {
	tls internal
	reverse_proxy $upstream
}
CADDY

exec caddy run --config /tmp/Caddyfile --adapter caddyfile
