# Deployment & Edge Hardening

How to put JobOffer Scanner behind a TLS-terminating reverse proxy that applies
CSP/HSTS and Secure cookies to the **web UI** (Streamlit cannot set these
itself), while passing the **REST API** through to FastAPI (which sets its own
strict JSON headers via `api/security.py`).

## Topology

```
            ┌──────────────────────────── edge (Caddy / nginx) ─────────────┐
  client ──▶│ TLS, CSP/HSTS, Secure cookies, gzip                           │
            │   /api/*  ─▶ FastAPI   127.0.0.1:8000   (own security headers) │
            │   /*      ─▶ Streamlit 127.0.0.1:5000   (UI; needs ws upgrade) │
            └───────────────────────────────────────────────────────────────┘
```

Why an edge at all: Streamlit can't emit CSP/HSTS or mark its session cookie
`Secure`, so a public deployment needs a proxy to harden the browser surface.
The API already hardens itself, so the proxy just forwards `/api/*`.

## Caddy (auto-TLS, simplest)

```bash
# Edit the domain + upstream ports first.
cp deploy/Caddyfile.example /etc/caddy/Caddyfile
caddy run --config /etc/caddy/Caddyfile
```
Caddy provisions and renews certificates automatically — that's what makes the
Secure cookie + HSTS meaningful.

## nginx (TLS via certbot)

```bash
cp deploy/nginx.conf.example /etc/nginx/sites-available/joboffer
ln -s /etc/nginx/sites-available/joboffer /etc/nginx/sites-enabled/
certbot --nginx -d yourdomain.com     # obtain certs, edit paths in the conf
nginx -t && systemctl reload nginx
```
The `map $http_upgrade` block + `Upgrade`/`Connection` headers are **required**
— without them Streamlit's websocket silently fails to connect.

## Backends

Run these bound to localhost (the proxy is the only public listener):

```bash
streamlit run app.py --server.address 127.0.0.1 --server.port 5000
python -m api.main          # API_HOST=127.0.0.1 API_PORT=8000
```

When the API itself sits behind TLS you may also enable its built-in HSTS:
`API_ENABLE_HSTS=1` (footgun on plain HTTP — only behind TLS).

## Replit / PaaS note

Vanilla Replit serves Streamlit directly without these headers. For a hardened
public deployment, front it with Caddy/nginx (above) or an edge like Cloudflare
that applies the same CSP/HSTS/cookie policy. The `deploy/*.example` files are
the source of truth for the header set.

## Metrics endpoint

If you scrape `GET /metrics` (see `METRICS_ENABLED`), keep it private: bind the
API to localhost and either don't expose `/metrics` through the proxy or set
`METRICS_TOKEN` and have Prometheus send `Authorization: Bearer <token>`.

## See also

- `deploy/RUNBOOK.md` — backups, restore drills, incidents, escalation.
- `docker-compose.yml` — the full multi-service stack for local/self-host.
