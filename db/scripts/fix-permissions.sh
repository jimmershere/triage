#!/bin/sh
set -eu

DATA_DIR="/var/lib/postgresql/data"
KEY_FILE="$DATA_DIR/server.key"
CERT_FILE="$DATA_DIR/server.crt"

if [ -f "$KEY_FILE" ]; then
  echo "Fixing permissions on $KEY_FILE"
  chown 999:999 "$KEY_FILE"
  chmod 600 "$KEY_FILE"
else
  echo "Key file $KEY_FILE not present; skipping"
fi

if [ -f "$CERT_FILE" ]; then
  echo "Fixing permissions on $CERT_FILE"
  chown 999:999 "$CERT_FILE"
  chmod 644 "$CERT_FILE"
else
  echo "Cert file $CERT_FILE not present; skipping"
fi
