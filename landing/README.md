# Landing page

A single self-contained static file (`index.html`) — no build step, no external
assets, no JS dependencies. Deploy it to any static host:

```bash
# Cloudflare Pages / Netlify / Vercel: point at the landing/ directory.
# Or serve it from the edge proxy in front of the app:
#   Caddy:   handle /  { root * /srv/landing  file_server }
#   nginx:   location = / { root /srv/landing; }
```

Conventions baked into the page (update both sides together):

- `/app` is the link target for the Streamlit app — map it at the proxy
  (`handle /app* { reverse_proxy 127.0.0.1:5000 }`) or replace with the
  real app URL before deploying.
- The pricing table mirrors `services/billing._DEFAULT_TIERS` (Free 5/2,
  Pro $12 50/30 detailed, Power $24 200/unlimited + API). If you change the
  tier table or Stripe prices, change this page too.
- `/terms` and `/privacy` are placeholders — publish real documents before
  charging money.
- Replace `hello@example.com` with a monitored address.
