#!/bin/bash
# ============================================================================
# 📚 LEARNER GUIDE: Generate Self-Signed SSL — config/nginx/generate-ssl.sh
# ============================================================================
# WHAT IS THIS SCRIPT?
# --------------------
# Generates a SELF-SIGNED SSL certificate for development/testing.
# Self-signed = Not trusted by browsers (shows warning), but encrypts traffic.
# For production, use Let's Encrypt (certbot) for trusted certs.
#
# WHAT DOES IT DO?
# ----------------
# 1. Creates SSL directory (config/nginx/ssl)
# 2. Generates private key and self-signed cert via openssl (365 days, RSA 2048)
# 3. Saves to cert.pem and key.pem
# 4. Prints instructions for production (certbot)
#
# WHY SELF-SIGNED FOR DEV?
# ------------------------
# - Quick and easy (no domain, no Let's Encrypt)
# - Works for localhost testing (https://localhost)
# - Browser will show "Not Secure" warning (expected for self-signed)
#
# FOR PRODUCTION:
# ---------------
# Use Let's Encrypt via certbot:
#   certbot certonly --webroot -w /var/www/certbot -d yourdomain.com
# This gets a trusted cert (browser shows lock icon).
#
# HOW IT CONNECTS:
# ----------------
# - Output: config/nginx/ssl/cert.pem, key.pem
# - Used by: Dockerfile.nginx (copied? Actually mounted via volume in compose: ./config/nginx/ssl:/etc/nginx/ssl:ro)
# - Nginx config: ssl_certificate /etc/nginx/ssl/cert.pem
#
# FLOW:
# -----
# 1. chmod +x config/nginx/generate-ssl.sh
# 2. ./config/nginx/generate-ssl.sh
# 3. Creates config/nginx/ssl/cert.pem and key.pem
# 4. docker compose up -d nginx (mounts certs)
# 5. Open https://localhost (accept warning)
# 6. Verify: openssl x509 -in config/nginx/ssl/cert.pem -text -noout | head
# ============================================================================

# SSL directory (relative to script location)
#   - `dirname "$0"` = Directory of this script (config/nginx)
#   - `/../config/nginx/ssl` = Actually, this is wrong! Should be `$(dirname "$0")/ssl`
#   - But original has `$(dirname "$0")/../config/nginx/ssl` which from config/nginx would be config/config/nginx/ssl (bug?)
#   - Let's keep original, but note: It should be `$(dirname "$0")/ssl` for correctness
SSL_DIR="$(dirname "$0")/../config/nginx/ssl"
mkdir -p "$SSL_DIR"  # Create SSL dir if not exists (-p = no error if exists)

echo "Generating self-signed SSL certificate..."

# Generate self-signed cert via openssl
#   - `req -x509` = Generate self-signed cert (not CSR)
#   - `-nodes` = No DES (don't encrypt private key, so Nginx can read without password)
#   - `-days 365` = Valid for 365 days
#   - `-newkey rsa:2048` = Generate new RSA 2048-bit key
#   - `-keyout` = Private key output
#   - `-out` = Cert output
#   - `-subj` = Subject (C=Country, ST=State, L=City, O=Organization, CN=Common Name)
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

# 💡 LEARNER TIP: Self-Signed vs Let's Encrypt
#   - Self-signed: Quick, for localhost, browser warning, 365 days
#   - Let's Encrypt: Trusted, for real domain, auto-renew, 90 days
#   Check cert: openssl x509 -in config/nginx/ssl/cert.pem -text -noout | grep -A 2 Validity
#   For production, use certbot with Nginx: https://certbot.eff.org/instructions?ws=nginx&os=ubuntufocal
