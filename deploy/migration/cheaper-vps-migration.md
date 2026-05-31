# Cheaper VPS Migration Runbook

This project currently runs the Next.js app on Vercel and the Python prediction
API on a separate Ubuntu server. The server migration only needs to move the
prediction API, its environment file, cached model state, reinforcement DBs, and
optional TimesFM install.

## Recommended target size

Use the smallest instance that keeps the prediction API stable:

- Prophet only, TimesFM disabled: 2 vCPU / 4 GB RAM is the practical floor.
- TimesFM enabled: 2-4 vCPU / 8 GB RAM is safer because torch and model loading
  can spike memory.
- Disk: 40 GB or larger if keeping `model_cache` and S&P500 data snapshots.

If the goal is the lowest monthly bill, start with TimesFM disabled on the new
server, verify Prophet and portfolio endpoints, then enable TimesFM only if the
server still has memory headroom.

If Vultr does not allow a direct downgrade, keep the current instance but switch
the prediction API to resource-aware MoE mode first. This keeps cheap Prophet and
wrapper agents always available while gating heavier experts such as TimesFM and
cross-symbol correlation forecasts.

```env
ENABLE_RESOURCE_MOE=true
MOE_PROFILE=cheap
MOE_MAX_HEAVY_IN_FLIGHT=1
```

## Source server backup

On the current Vultr server:

```bash
cd /root/no-slip
sudo bash deploy/migration/source_backup.sh
```

Before destroying the server, prefer a short service stop for a cleaner SQLite
snapshot:

```bash
cd /root/no-slip
git pull origin main
sudo STOP_SERVICE_DURING_BACKUP=true bash deploy/migration/source_backup.sh
```

The script prints:

- `backup_path`
- `checksum_path`
- `contents_path`
- `manifest_path`

Copy the backup and checksum to the new server:

```bash
scp /root/no-slip-migration/no-slip-prediction-*.tar.gz* root@NEW_SERVER_IP:/root/
```

Or copy it to your laptop before destroying the server:

```bash
mkdir -p ~/Downloads/no-slip-server-backups
scp root@OLD_SERVER_IP:/root/no-slip-migration/no-slip-prediction-*.tar.gz* ~/Downloads/no-slip-server-backups/
scp root@OLD_SERVER_IP:/root/no-slip-migration/no-slip-prediction-*.contents.txt ~/Downloads/no-slip-server-backups/
```

## Target server restore

On the new Ubuntu server:

```bash
export BACKUP_PATH=/root/no-slip-prediction-YYYYMMDDTHHMMSSZ.tar.gz
export REPO_URL=https://github.com/wehackteam/no-slip.git
export APP_DIR=/root/no-slip
export SERVICE_USER=root
export INSTALL_TIMESFM=false
sudo -E bash /root/no-slip/deploy/migration/target_restore.sh
```

If `/root/no-slip` is not cloned yet, run this first:

```bash
git clone https://github.com/wehackteam/no-slip.git /root/no-slip
```

For TimesFM on the target:

```bash
export INSTALL_TIMESFM=true
export TIMESFM_REPO_PATH=/opt/timesfm
sudo -E bash /root/no-slip/deploy/migration/target_restore.sh
```

## Verification

On the new server:

```bash
systemctl status no-slip-prediction --no-pager
curl http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/predict-step \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(grep '^PREDICTION_API_TOKEN=' /etc/no-slip/prediction-api.env | cut -d= -f2-)" \
  -d '{"symbol":"AAPL","marketMode":"sp500","data":[]}' | python3 -m json.tool | head -n 40
```

Check the migrated state:

```bash
ls -lah /root/no-slip/services/trader/model_cache
ls -lah /root/no-slip/data/sp500
journalctl -u no-slip-prediction -n 120 --no-pager
```

## Vercel cutover

After the new API passes health checks, update Vercel:

```env
PREDICTION_API_URL=http://NEW_SERVER_IP:8000
PREDICTION_API_TOKEN=same_token_as_/etc/no-slip/prediction-api.env
```

Redeploy the Vercel production app after changing the environment variable.
Keep the old Vultr server running for one day as rollback.

## Rollback

If the new server fails:

1. Set `PREDICTION_API_URL` back to the old Vultr API.
2. Redeploy Vercel.
3. Keep the new server stopped until logs are reviewed.

## Cost-control checklist

- Disable TimesFM first if memory is tight:

```env
ENABLE_TIMESFM_DRAWDOWN=false
```

- Prefer MoE cheap mode before disabling TimesFM completely:

```env
ENABLE_RESOURCE_MOE=true
MOE_PROFILE=cheap
MOE_MAX_HEAVY_IN_FLIGHT=1
```

- Keep `/root/no-slip/services/trader/.venv` out of backups; rebuild it on the
  new server.
- Keep only the latest migration archive after a successful cutover.
- Do not expose port `8000` publicly without a firewall or reverse proxy allowlist.
