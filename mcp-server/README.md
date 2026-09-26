# WebLoom MCP Server

MCP tools for WebLoom's public-web data API.

## Tools

- `webloom_scrape`
- `webloom_map`
- `webloom_crawl`
- `webloom_extract`
- `webloom_fusion`

## Setup

```bash
npm install
```

Set:

- `WEBLOOM_API_URL` — your deployed WebLoom URL
- `WEBLOOM_API_KEY` — an account API key created at `/api-keys`

Example MCP client config:

```json
{
  "mcpServers": {
    "webloom": {
      "command": "node",
      "args": ["/absolute/path/to/webloom/mcp-server/src/index.mjs"],
      "env": {
        "WEBLOOM_API_URL": "https://YOUR_WEBLOOM_DOMAIN",
        "WEBLOOM_API_KEY": "wl_YOUR_KEY"
      }
    }
  }
}
```

This server is for publicly accessible web content. It does not bypass logins, CAPTCHA, paywalls, DRM, or private network controls.
