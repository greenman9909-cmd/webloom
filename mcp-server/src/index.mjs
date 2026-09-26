#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import * as z from "zod/v4";

const API_URL = (process.env.WEBLOOM_API_URL || "").replace(/\/$/, "");
const API_KEY = process.env.WEBLOOM_API_KEY || "";

async function callWebLoom(path, body) {
  if (!API_URL) throw new Error("WEBLOOM_API_URL is required.");
  if (!API_KEY) throw new Error("WEBLOOM_API_KEY is required.");
  const response = await fetch(API_URL + path, {
    method: "POST",
    headers: {
      "Authorization": "Bearer " + API_KEY,
      "Content-Type": "application/json",
      "Accept": "application/json"
    },
    body: JSON.stringify(body)
  });
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; }
  catch { throw new Error("WebLoom returned an invalid response."); }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || ("WebLoom API failed with HTTP " + response.status));
  }
  return data;
}

function result(data) {
  return {
    content: [{ type: "text", text: JSON.stringify(data, null, 2) }]
  };
}

function failure(error) {
  return {
    isError: true,
    content: [{ type: "text", text: String(error?.message || error) }]
  };
}

function createServer() {
  const server = new McpServer({
    name: "webloom",
    version: "1.0.0"
  });

  server.registerTool(
    "webloom_scrape",
    {
      description: "Scrape one publicly accessible web page into clean Markdown, metadata, entities, links and a content fingerprint.",
      inputSchema: z.object({
        url: z.string().url()
      })
    },
    async ({ url }) => {
      try { return result(await callWebLoom("/v1/scrape", { url })); }
      catch (error) { return failure(error); }
    }
  );

  server.registerTool(
    "webloom_map",
    {
      description: "Discover public URLs on a website before deciding which pages to scrape or crawl.",
      inputSchema: z.object({
        url: z.string().url(),
        limit: z.number().int().min(1).max(5000).optional(),
        search: z.string().max(200).optional()
      })
    },
    async ({ url, limit, search }) => {
      try { return result(await callWebLoom("/v1/map", { url, limit, search })); }
      catch (error) { return failure(error); }
    }
  );

  server.registerTool(
    "webloom_crawl",
    {
      description: "Crawl publicly accessible internal pages into an AI-ready Markdown and entity dataset.",
      inputSchema: z.object({
        url: z.string().url(),
        limit: z.number().int().min(1).max(250).optional(),
        max_depth: z.number().int().min(0).max(4).optional()
      })
    },
    async ({ url, limit, max_depth }) => {
      try { return result(await callWebLoom("/v1/crawl", { url, limit, max_depth })); }
      catch (error) { return failure(error); }
    }
  );

  server.registerTool(
    "webloom_extract",
    {
      description: "Extract named public metadata or structured-schema fields across publicly accessible pages.",
      inputSchema: z.object({
        url: z.string().url(),
        fields: z.array(z.string().min(1)).min(1).max(50),
        limit: z.number().int().min(1).max(250).optional(),
        max_depth: z.number().int().min(0).max(4).optional()
      })
    },
    async ({ url, fields, limit, max_depth }) => {
      try { return result(await callWebLoom("/v1/extract", { url, fields, limit, max_depth })); }
      catch (error) { return failure(error); }
    }
  );

  server.registerTool(
    "webloom_fusion",
    {
      description: "Run WebLoom Fusion: crawl + clean Markdown + entities + link graph + change fingerprints + passive frontend metadata.",
      inputSchema: z.object({
        url: z.string().url(),
        limit: z.number().int().min(1).max(250).optional(),
        max_depth: z.number().int().min(0).max(4).optional()
      })
    },
    async ({ url, limit, max_depth }) => {
      try { return result(await callWebLoom("/v1/fusion", { url, limit, max_depth })); }
      catch (error) { return failure(error); }
    }
  );

  return server;
}

void serveStdio(createServer);
console.error("WebLoom MCP server running on stdio");
