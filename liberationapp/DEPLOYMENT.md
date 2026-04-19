# HLL Frontline Deployment

This stack is intended to run behind Caddy on a VPS.

## Compose Exposure

- `frontend` binds to `127.0.0.1:8081`
- `liberation-api` binds to `127.0.0.1:8080`

That keeps both services off the public internet while still allowing:

- Caddy on the host to proxy traffic into the frontend
- the host machine or bot process to call the API on `127.0.0.1:8080`

## Recommended Caddy Setup

Use `Caddyfile.production` as the starting point.

- `www.hllfrontline.com` redirects to `hllfrontline.com`
- `hllfrontline.com` reverse proxies to `127.0.0.1:8081`
- compression is enabled with `zstd` and `gzip`
- access logs are written to `/var/log/caddy/hllfrontline.access.log`

If `/var/log/caddy` does not exist, create it before reloading Caddy:

```bash
sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy
```

Then copy the config into place:

```bash
sudo cp liberationapp/Caddyfile.production /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## Cloudflare

Recommended DNS records:

- `A` record: `hllfrontline.com -> 130.162.174.77`
- `CNAME` record: `www -> hllfrontline.com`

Once HTTPS works through Caddy, Cloudflare can be switched from `DNS only` to `Proxied`.

Use `Full (strict)` in Cloudflare SSL/TLS mode.

## Deploy Or Refresh

From `liberationapp/`:

```bash
docker compose -f docker-compose.liberation.yml up -d --build
```

## Verification

Check the host-local services:

```bash
curl http://127.0.0.1:8081
curl http://127.0.0.1:8080/health
```

Check the public domain:

```bash
curl -I http://hllfrontline.com
curl -I https://hllfrontline.com
curl -I https://www.hllfrontline.com
```

Expected result:

- HTTP redirects to HTTPS
- apex domain serves the app
- `www` redirects to the apex domain