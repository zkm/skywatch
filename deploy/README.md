# Production deployment (skywatch.zkm.tw)

Copies of the files installed on the `skywatch` DigitalOcean droplet (Ubuntu 24.04, 1 vCPU / 2 GB).
Secrets are not here: they live only in `/opt/skywatch-deploy/skywatch.env` on the server.

| File here | Installed at | What it does |
|---|---|---|
| `compose.yml`, `Caddyfile` | `/opt/skywatch-deploy/` | Skywatch behind Caddy (automatic HTTPS). Skywatch publishes no port; data in `/var/lib/skywatch` |
| `skywatch-autodeploy` | `/usr/local/sbin/` | Deploys `origin/main` when it changes and its CI `test` check passed, health-checks the site, rolls back and emails on failure |
| `skywatch-autodeploy.service`, `.timer` | `/etc/systemd/system/` | Runs the deploy check every 5 minutes |
| `skywatch-backup` | `/usr/local/sbin/` | Nightly SQLite snapshot plus the push key and voter salt, 14 days kept in `/var/backups/skywatch` |
| `skywatch-backup.cron` | `/etc/cron.d/skywatch-backup` | Runs the backup at 03:30 UTC |

`/opt/skywatch` is a plain clone of this repo's `main`; the deploy script fast-forwards it.

## Day to day

Merging to `main` is the deploy: within about 5 minutes of CI passing, the server picks it up.

```sh
journalctl -u skywatch-autodeploy -n 50     # recent deploys and checks
systemctl start skywatch-autodeploy          # deploy now instead of waiting
docker compose -f /opt/skywatch-deploy/compose.yml logs -f skywatch
```

Deploying by hand is the same as what the script does:
`git -C /opt/skywatch pull --ff-only && docker compose -f /opt/skywatch-deploy/compose.yml up -d --build skywatch`.

## Rebuilding the server

1. Create the droplet with your SSH key, then `apt install docker.io docker-compose-v2 sqlite3 git`,
   allow 22, 80, 443/tcp and 443/udp in `ufw`, and add 1 GB of swap.
2. `git clone https://github.com/zkm/skywatch.git /opt/skywatch`, copy the files above into place,
   and write `/opt/skywatch-deploy/skywatch.env` (mode 600) with the SMTP, alert and audio-source settings.
3. Restore `/var/lib/skywatch` from a backup if you have one: `vapid_private.pem` and `voter.salt`
   can't be regenerated without breaking push subscriptions and letting everyone vote again.
4. `docker compose -f /opt/skywatch-deploy/compose.yml up -d --build`,
   `systemctl enable --now skywatch-autodeploy.timer`, and point the `skywatch` A record in
   Cloudflare (DNS only) at the new IP.
