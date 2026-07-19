#!/bin/bash

# Generate self-signed SSL certificates for development
# For production, use proper certificates from a CA like Let's Encrypt

echo "Generating self-signed SSL certificates..."

# Generate private key
openssl genrsa -out key.pem 2048

# Generate self-signed certificate (valid for 365 days)
openssl req -new -x509 -key key.pem -out cert.pem -days 365 \
  -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost"

# Set appropriate permissions
chmod 600 key.pem
chmod 644 cert.pem

echo "✓ SSL certificates generated:"
echo "  - cert.pem (public certificate)"
echo "  - key.pem  (private key)"
echo ""
echo "For production, use certificates from a trusted CA like Let's Encrypt."
