#!/bin/bash
# ============================================
# Generate Self-Signed SSL Certificates
# For development/testing only!
# For production: use Let's Encrypt (certbot)
# ============================================

SSL_DIR="$(dirname "$0")/../config/nginx/ssl"
mkdir -p "$SSL_DIR"

echo "Generating self-signed SSL certificate..."

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$SSL_DIR/key.pem" \
    -out "$SSL_DIR/cert.pem" \
    -subj "/C=US/ST=State/L=City/O=LlamaAgent/CN=localhost"

echo "SSL certificate generated at:"
echo "  Certificate: $SSL_DIR/cert.pem"
echo "  Private Key: $SSL_DIR/key.pem"
echo ""
echo "For production, use certbot:"
echo "  certbot certonly --webroot -w /var/www/certbot -d yourdomain.com"
