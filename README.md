# WebLoom

WebLoom turns a publicly accessible website into two things at once:

1. a reconstructed frontend project with an in-app live preview and ZIP export;
2. an AI-ready Fusion dataset with Markdown, structured entities, a link graph, change fingerprints and passive frontend metadata.

## Product surface

- **Capture** — public frontend pages and referenced assets
- **Live preview** — sandboxed directly inside WebLoom; no forced redirect
- **Fusion** — Markdown, dataset JSONL, graph, structured entities and passive audit
- **Projects** — preview, pages, assets, data, Markdown, graph, audit, metadata, links, sitemap and export
- **Developer API** — `/v1/scrape`, `/v1/map`, `/v1/crawl`, `/v1/extract`, `/v1/fusion`
- **API keys** — account-scoped `wl_...` Bearer keys
- **MCP server** — tools for scrape, map, crawl, extract and Fusion
- **Docs** — `/docs`, `/docs/api`, `/docs/mcp`

## Architecture

```text
Browser
   │
   ▼
WebLoom Flask app
   ├── Supabase Auth / Postgres / Storage
   ├── SPA-Ripper public frontend capture
   ├── Stripe subscription state
   └── server-to-server call
            │
            ▼
      private webloom-fusion service
            ├── scrape / map / crawl
            ├── Markdown + entities
            ├── graph + change tracking
            └── passive frontend audit
```

The private Fusion token is never sent to the browser.

## Local development

```bash
python -m pip install -r requirements.txt
python web_app.py
```

Copy `.env.example` values into your local environment. For full Fusion functionality, deploy the private `webloom-fusion` repository and set:

```text
WEBLOOM_FUSION_URL=https://your-private-fusion-service
WEBLOOM_FUSION_TOKEN=...
```

## MCP

```bash
cd mcp-server
npm install
WEBLOOM_API_URL=https://YOUR_WEBLOOM_DOMAIN \
WEBLOOM_API_KEY=wl_YOUR_KEY \
node src/index.mjs
```

See `/docs/mcp` in the web app for client configuration.

## Safety boundary

Public pages only. WebLoom does not bypass authentication, CAPTCHA, paywalls, DRM, private networks, or other access controls. Passive audit output redacts credential-like values.
