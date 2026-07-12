# Historic Stats Deployment

The Liberation frontend, API, PostgreSQL, and Redis stack has been retired.
Only the independently hosted historic-stats service remains.

`Caddyfile.production` exposes `7drhistostats.hllfrontline.com` and proxies it
to the existing service on `127.0.0.1:7010`. The `www` form redirects to the
canonical subdomain.

Install and validate the configuration on the host:

```bash
sudo cp liberationapp/Caddyfile.production /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Verify both the local service and public endpoint:

```bash
curl -I http://127.0.0.1:7010
curl -I https://7drhistostats.hllfrontline.com
```

The apex `hllfrontline.com` and the former Liberation containers are
intentionally not configured here.
