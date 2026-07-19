# HTTPS Setup for NclaveAI

NclaveAI requires HTTPS for the Live Mode (voice chat) feature because the Web Speech API only works in secure contexts.

## Quick Start (Development)

### Option 1: Accept Self-Signed Certificate

1. Generate self-signed certificates:
   ```bash
   ./generate_certs.sh
   ```

2. Start the server with HTTPS:
   ```bash
   uvicorn app.main:app --reload --port 8081 --ssl-keyfile key.pem --ssl-certfile cert.pem
   ```

3. Open `https://localhost:8081` in your browser

4. **Important**: You'll see a security warning. Click "Advanced" → "Proceed to localhost (unsafe)"
   - This is required for the browser to accept the certificate
   - Once accepted, speech recognition will work

### Option 2: Use mkcert (Recommended for Development)

[mkcert](https://github.com/FiloSottile/mkcert) creates locally-trusted development certificates that browsers accept automatically.

1. Install mkcert:
   ```bash
   # macOS
   brew install mkcert nss
   
   # Linux
   sudo apt install libnss3-tools
   brew install mkcert
   ```

2. Install the local CA:
   ```bash
   mkcert -install
   ```

3. Generate certificates for localhost:
   ```bash
   mkcert -key-file key.pem -cert-file cert.pem localhost 127.0.0.1 ::1
   ```

4. Start the server:
   ```bash
   uvicorn app.main:app --reload --port 8081 --ssl-keyfile key.pem --ssl-certfile cert.pem
   ```

5. Open `https://localhost:8081` - no security warnings!

## Production Deployment

### Option 1: Reverse Proxy (Recommended)

Use a reverse proxy like Nginx or Caddy with Let's Encrypt:

#### Using Caddy (Automatic HTTPS)

Create a `Caddyfile`:
```
yourdomain.com {
    reverse_proxy localhost:8081
}
```

Caddy automatically obtains and renews Let's Encrypt certificates.

#### Using Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Option 2: Docker with HTTPS

1. Generate certificates (or use Let's Encrypt)

2. Update `docker-compose.yml`:
   ```yaml
   services:
     agent:
       build: .
       command: uvicorn app.main:app --host 0.0.0.0 --port 8081 --ssl-keyfile /app/certs/key.pem --ssl-certfile /app/certs/cert.pem
       ports:
         - "8081:8081"
       volumes:
         - ./certs:/app/certs:ro
   ```

3. Create a `certs` directory and place your certificates:
   ```bash
   mkdir -p certs
   cp /path/to/your/cert.pem certs/
   cp /path/to/your/key.pem certs/
   ```

4. Start with Docker Compose:
   ```bash
   docker-compose up -d
   ```

## Troubleshooting

### "Speech recognition error: not-allowed"

This error occurs when:
1. **Not using HTTPS**: The Web Speech API requires a secure context
   - Solution: Access via `https://` not `http://`

2. **Self-signed certificate not accepted**: Browser blocks the microphone
   - Solution: Click the lock icon → Allow microphone access
   - Or use mkcert for trusted certificates

3. **Microphone permission denied**: User denied microphone access
   - Solution: Click the lock icon in the address bar and allow microphone access

### Browser Compatibility

Live Mode works best in:
- ✅ Chrome (recommended)
- ✅ Edge
- ✅ Safari 14.1+
- ⚠️ Firefox (limited Web Speech API support)

### Testing HTTPS

Test your HTTPS setup:
```bash
curl -k https://localhost:8081/health
```

The `-k` flag allows self-signed certificates.

## Environment Variables

Configure HTTPS via environment variables:

```bash
# .env file
SSL_KEYFILE=/path/to/key.pem
SSL_CERTFILE=/path/to/cert.pem
```

Or in `docker-compose.yml`:
```yaml
environment:
  SSL_KEYFILE: "/app/certs/key.pem"
  SSL_CERTFILE: "/app/certs/cert.pem"
```
