#!/bin/sh
# Writes /etc/nginx/snippets/real-ip.conf, which civitas.conf's public server
# includes: which peers may tell this nginx the visitor's address, and in
# which header. Run by the nginx image's entrypoint at every start (it runs
# /docker-entrypoint.d/*.sh) and once at build, so the file always exists.
#
# REAL_IP_FROM    space-separated addresses or CIDRs of the proxy in front
#                 of this nginx. Unset: loopback only. A host behind a
#                 reverse proxy must set this to that proxy's address
#                 (production sets the Pi's own LAN address, where its host
#                 nginx connects from).
#                 "none": trust no one; every visitor is read from the
#                 connection (nothing in front, or Cloudflare connecting
#                 straight here, which needs its ranges and CF-Connecting-IP
#                 instead).
# REAL_IP_HEADER  the header that proxy sets (default X-Real-IP).
#
# Getting this wrong collapses every visitor into one address, which feeds
# backend/app/api/visits.py (AGENTS.md section 8, docs/self-hosting.md).
set -eu

out=${REAL_IP_SNIPPET:-/etc/nginx/snippets/real-ip.conf}
from=${REAL_IP_FROM:-"127.0.0.1 ::1"}
header=${REAL_IP_HEADER:-X-Real-IP}

case "$header" in
  *[!A-Za-z0-9-]*|"") echo "real-ip.sh: REAL_IP_HEADER '$header' is not a header name" >&2; exit 1 ;;
esac

mkdir -p "$(dirname "$out")"
{
  echo "# Written by /docker-entrypoint.d/15-civitas-real-ip.sh from REAL_IP_FROM / REAL_IP_HEADER."
  if [ "$from" = "none" ]; then
    echo "# REAL_IP_FROM=none: no proxy is trusted; visitors are read from the connection."
  else
    for addr in $from; do
      case "$addr" in
        *[!0-9A-Fa-f:./]*) echo "real-ip.sh: REAL_IP_FROM entry '$addr' is not an address or CIDR" >&2; exit 1 ;;
      esac
      echo "set_real_ip_from $addr;"
    done
    echo "real_ip_header $header;"
  fi
} > "$out.tmp"
mv "$out.tmp" "$out"
