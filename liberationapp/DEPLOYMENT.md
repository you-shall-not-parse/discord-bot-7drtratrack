# HLL Frontline Deployment

The original Liberation frontend, API, PostgreSQL, and Redis stack has been
retired. The Discord bot now hosts the HLL Frontline personnel dashboard on
`127.0.0.1:7020`, while the independently hosted historic-stats service remains
on `127.0.0.1:7010`.

`Caddyfile.production` exposes the new dashboard at `hllfrontline.com` and
historic stats at `7drhistostats.hllfrontline.com`. Both `www` forms redirect
to their canonical address.

Install and validate the configuration on the host:

```bash
sudo cp liberationapp/Caddyfile.production /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Verify both the local service and public endpoint:

```bash
curl http://127.0.0.1:7020/api/health
curl -I https://hllfrontline.com
curl -I http://127.0.0.1:7010
curl -I https://7drhistostats.hllfrontline.com
```

The bind address and port can be changed with `FRONTLINE_WEB_HOST` and
`FRONTLINE_WEB_PORT`. Keep the bind address on loopback when Caddy and the bot
run on the same host.
