# HLL Frontline Deployment

The original Liberation frontend, API, PostgreSQL, and Redis stack has been
retired. The Discord bot now hosts the HLL Frontline personnel dashboard on
`127.0.0.1:7020`, while the independently hosted historic-stats service remains
on `127.0.0.1:7010`.

`Caddyfile.production` exposes the new dashboard at `hllfrontline.com` and
historic stats at `7drhistostats.hllfrontline.com`. Both `www` forms redirect
to their canonical address.

## Required PIN configuration

The dashboard fails closed unless `APPPIN` contains at least eight characters.
Add a long, random value to the bot's existing `.env` file:

```dotenv
APPPIN="replace-this-with-a-long-random-value"
```

Keep `.env` out of Git (the repository `.gitignore` already excludes it) and
restrict it to the service account on Linux:

```bash
chmod 600 .env
```

Restart the bot after adding or changing `APPPIN`. Changing it invalidates all
existing website sessions. Sessions last for up to seven days or until the bot
restarts. Five failed PIN attempts from one client are blocked for 15 minutes.

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

`/api/health` remains unauthenticated for service monitoring. The homepage,
dashboard API, report pages, and HTML/Excel exports all require a valid PIN
session. A request to `/` should redirect to `/login` before authentication.

The bind address and port can be changed with `FRONTLINE_WEB_HOST` and
`FRONTLINE_WEB_PORT`. Keep the bind address on loopback when Caddy and the bot
run on the same host.

## Security hardening checklist

1. Keep `FRONTLINE_WEB_HOST=127.0.0.1`; never expose port 7020 publicly.
2. Keep Cloudflare SSL/TLS mode on Full (strict) and Always Use HTTPS enabled.
3. If a Cloudflare Access application currently covers `hllfrontline.com`,
   remove or disable it when switching to this PIN-only login, otherwise users
   will see both authentication layers.
4. Prevent direct-origin bypass. The preferred end state is a Cloudflare Tunnel
   pointing `hllfrontline.com` to `http://127.0.0.1:7020`, with the public Caddy
   site block for that hostname removed. Alternatively, restrict origin HTTPS
   traffic to Cloudflare IP ranges or configure Authenticated Origin Pulls.
5. Add a Cloudflare rate-limiting rule for `POST /login` as an outer layer in
   addition to the application's built-in lockout.
6. Apply Ubuntu, Caddy, Python dependency, and bot updates regularly. Back up
   `data/rollcall.xlsx` and the bot's state files.
7. Review rejected-login warnings and Caddy access logs. Change `APPPIN`
   immediately if it is posted publicly or shared with someone who should no
   longer have access.

### Cloudflare dotfile block rule

The application returns `404` before authentication for hidden files and known
secret filenames. Stop the same automated probes before they reach Caddy by
opening Cloudflare **Security > Security rules > Create rule > Custom rules**
(shown as **Security > WAF > Custom rules** in the older dashboard), choosing
action **Block**, and using this expression:

```text
http.host eq "hllfrontline.com" and (
  http.request.uri.path contains "/." or
  lower(http.request.uri.path) contains "/wp-config.php" or
  lower(http.request.uri.path) contains "/config.php" or
  lower(http.request.uri.path) contains "/credentials.json" or
  lower(http.request.uri.path) contains "/secrets.json" or
  lower(http.request.uri.path) contains "/docker-compose.yml" or
  lower(http.request.uri.path) contains "/id_rsa"
)
```

Name it `Block hidden-file probes`. This intentionally blocks `.well-known`
paths on this hostname; HLL Frontline does not use them because Caddy handles
TLS and the application exposes no `.well-known` route.
