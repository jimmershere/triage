#!/bin/bash
set -euo pipefail

CERT_SOURCE_DIR="/etc/postgresql/certs"
CERT_DEST_DIR="/var/lib/postgresql/data"

copy_if_exists() {
  local name="$1"
  local src="${CERT_SOURCE_DIR}/${name}"
  local dest="${CERT_DEST_DIR}/${name}"

  if [ -f "${src}" ]; then
    cp "${src}" "${dest}"
  else
    echo "Expected SSL file ${src} missing" >&2
    exit 1
  fi
}

copy_if_exists "server.crt"
copy_if_exists "server.key"

chmod 600 "${CERT_DEST_DIR}/server.key"
chmod 644 "${CERT_DEST_DIR}/server.crt"

