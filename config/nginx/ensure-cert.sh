#!/bin/sh
# Runs from /docker-entrypoint.d/ before nginx starts.
# - If real certs are mounted at /etc/nginx/ssl-provided, use them.
# - Otherwise generate a self-signed cert so a fresh `docker compose up`
#   works without running config/nginx/generate-ssl.sh first.
set -e
SSL_DIR=/etc/nginx/ssl
SRC=/etc/nginx/ssl-provided
mkdir -p "$SSL_DIR"
if [ -s "$SRC/cert.pem" ] && [ -s "$SRC/key.pem" ]; then
    cp "$SRC/cert.pem" "$SSL_DIR/cert.pem"
    cp "$SRC/key.pem" "$SSL_DIR/key.pem"
    echo "ensure-cert: using mounted certificate"
elif [ ! -s "$SSL_DIR/cert.pem" ] || [ ! -s "$SSL_DIR/key.pem" ]; then
    echo "ensure-cert: no certificate mounted - generating self-signed (CN=${SSL_CN:-localhost})"
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
        -subj "/O=LlamaAgent/CN=${SSL_CN:-localhost}" >/dev/null 2>&1
fi
chmod 600 "$SSL_DIR/key.pem"
