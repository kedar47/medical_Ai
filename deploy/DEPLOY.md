# Deploying the demo

| Subdomain | Serves |
|---|---|
| `medicalai.softgoway.com` | React dashboard, behind a demo password. `/api/*` is proxied to the backend with the dashboard token added server-side. |
| `api.medicalai.softgoway.com` | FastAPI backend: `/docs`, `/health`, Exotel webhooks and the `/telephony/media` WebSocket. |

Both run on **one Ubuntu VPS** (4 GB RAM minimum, 8 GB recommended, 20 GB disk).
Shared/FTP hosting cannot run the backend (Python process, PostgreSQL, WebSockets,
Whisper/Kokoro models).

## 1. DNS (manual)

In the softgoway.com DNS panel create two **A records** pointing to the VPS IP:

| Type | Name | Value |
|---|---|---|
| A | `medicalai` | `<VPS IP>` |
| A | `api.medicalai` | `<VPS IP>` |

Delete any existing records for these names that point at the shared hosting.
Check with `nslookup medicalai.softgoway.com` before step 2.

## 2. Deploy (one command)

SSH into the VPS (PuTTY or `ssh root@<VPS IP>`), then:

```bash
git clone https://github.com/kedar47/medical_ai.git /opt/medicalai
```
```bash
cd /opt/medicalai && sudo bash deploy/setup_vps.sh medicalai.softgoway.com api.medicalai.softgoway.com you@example.com
```

The script installs Docker, nginx and Certbot; generates `.env` with random
passwords/tokens; downloads the Kokoro voice models; starts PostgreSQL + API;
builds the dashboard; configures nginx; issues free HTTPS certificates; and
prints the demo login (also saved in `/root/medicalai-demo-login.txt`).
The first start takes several minutes while voice models load.

## 3. Updating after code changes

```bash
cd /opt/medicalai && git pull && sudo bash deploy/setup_vps.sh medicalai.softgoway.com api.medicalai.softgoway.com you@example.com
```

Existing `.env`, demo password, database and certificates are kept.

## Useful commands

```bash
cd /opt/medicalai && docker compose logs -f api
```
```bash
cd /opt/medicalai && docker compose restart api
```
```bash
sudo htpasswd /etc/nginx/medicalai.htpasswd demo
```
(The last one changes the demo password.)

## Alternative: dashboard over FTP

Only if the dashboard must stay on the shared hosting. The API still needs the VPS.

1. Point only `api.medicalai` at the VPS and run the setup script as above.
2. On your PC: create `frontend/.env.production` containing
   `VITE_API_BASE_URL=https://api.medicalai.softgoway.com`, then run
   `npm ci && npm run build` inside `frontend/`.
3. Upload everything inside `frontend/dist/` (including the hidden `.htaccess`)
   to the subdomain's folder with FileZilla.

Limitation: protected dashboard screens (stats, sessions, calls) return
"unauthorized" in this mode because the token can't be safely placed in the
browser. Catalogue, matching and ordering work. Prefer the VPS-only setup.
