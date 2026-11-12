# `cloud/app/data/`

Runtime data files that are **not** source code and are refreshed on a
schedule rather than edited.

## `dbip-country-lite.mmdb` — offline IP → country database

Used by [`cloud/app/geoip.py`](../geoip.py) to pick the price currency from the
visitor's IP **without any third-party request** — the IP is matched against
this static table in memory and never leaves the server (152-ФЗ: no
cross-border transfer).

- **Source:** DB-IP *IP to Country Lite*, MMDB build —
  <https://db-ip.com/db/download/ip-to-country-lite>
- **Direct URL:** `https://download.db-ip.com/free/dbip-country-lite-YYYY-MM.mmdb.gz`
- **Publisher:** DB-IP (EU company). **Licence:** CC-BY-4.0 (attribution only).
- **Not committed** (see `.gitignore` here) and **not baked into the image**.

### Where it lives in production (Timeweb RU VDS)

The API is a `docker run` container (`cloud/infra/server-scripts/plg-api-run.sh`)
whose `/app` is `COPY`-baked at build time — a file dropped into the repo tree
after the build is *not* visible inside the container. So the DB is kept on the
**host** and **bind-mounted**:

| | path |
|---|---|
| host file | `/opt/plg/data/dbip-country-lite.mmdb` |
| mounted into container (read-only) | `/app/cloud/app/data/dbip-country-lite.mmdb` = `GEOIP_DB_PATH` default |

`plg-api-run.sh` adds the `-v` mount automatically **iff** the host file exists.

### Install / quarterly refresh

```bash
sudo mkdir -p /opt/plg/data
curl -fSL -o /tmp/dbip.mmdb.gz \
  "https://download.db-ip.com/free/dbip-country-lite-$(date +%Y-%m).mmdb.gz"
gunzip -c /tmp/dbip.mmdb.gz | sudo tee /opt/plg/data/dbip-country-lite.mmdb >/dev/null
rm /tmp/dbip.mmdb.gz

# First install (mount not yet in the running container): recreate it.
cd /opt/plg && bash cloud/infra/server-scripts/plg-api-run.sh
# Later refreshes (mount already present): just restart.
docker restart plg-api
```

If the file is missing or unreadable the API still runs: every lookup returns
`None` and callers fall back to the domestic tier (`ru` / 499 ₽). Nothing
breaks — non-RU visitors just see the RUB price until the file is in place, and
while `intl_billing` is off that is the behaviour anyway.

### Attribution

The landing footer must carry a link back to DB-IP (CC-BY-4.0 requirement):
`<a href="https://db-ip.com">IP Geolocation by DB-IP</a>`.
