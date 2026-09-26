import os
import re
import sys
from html import unescape
from urllib.parse import urljoin, urlparse
from typing import Set, List, Optional
import requests

from spa_ripper.path_utils import query_variant_relpath

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

COMMON_EXTRA_ENDPOINTS = [
    "/favicon.ico",
    "/favicon.svg",
    "/apple-touch-icon.png",
    "/icon.png",
    "/icon-192.png",
    "/icon-512.png",
    "/manifest.webmanifest",
    "/manifest.json",
    "/latest.rss",
    "/sw.js",
    "/robots.txt",
]

# Attribute match for HTML tags
HTML_ATTR_REGEX = re.compile(
    r"""(?:src|href|poster|data-src)\s*=\s*["']([^"'#\s>]+)""",
    re.IGNORECASE
)

# Responsive image srcset regex
SRCSET_REGEX = re.compile(
    r"""(?:srcset)\s*=\s*["']([^"']+)""",
    re.IGNORECASE
)

# CSS url(...) detection
CSS_URL_REGEX = re.compile(
    r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""",
    re.IGNORECASE
)

# JavaScript string-literal detection for bundled assets.
# Restricting discovery to quoted strings avoids false positives such as
# res.json() and response.json() while still catching import(), new URL(),
# media files, manifests, subtitles, and other runtime asset references.
JS_STRING_LITERAL_REGEX = re.compile(
    r"""["'`]([^"'`\r\n]+)["'`]""",
    re.IGNORECASE,
)

JS_ASSET_EXTENSIONS = (
    ".js", ".mjs", ".cjs", ".css", ".json",
    ".woff2", ".woff", ".ttf", ".eot",
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg", ".ico",
    ".mp4", ".webm", ".mov", ".m3u8",
    ".mp3", ".ogg", ".wav", ".m4a",
    ".vtt", ".srt",
)

# JSON schema / manifest icon regex
JSON_SRC_REGEX = re.compile(
    r'''"(?:src|url|href|icon|banner)"\s*:\s*"([^"#\s>]+)"''',
    re.IGNORECASE
)


class SpaScraper:
    def __init__(
        self,
        base_url: str,
        output_dir: str,
        user_agent: str = DEFAULT_USER_AGENT,
        extra_endpoints: Optional[List[str]] = None,
        timeout: int = 15,
        deep_assets: bool = True,
        max_files: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ):
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        parsed_base = urlparse(self.base_url)
        self.base_netloc = parsed_base.netloc
        self.output_dir = output_dir
        self.timeout = timeout
        self.extra_endpoints = extra_endpoints or COMMON_EXTRA_ENDPOINTS
        self.deep_assets = deep_assets
        self.max_files = max_files
        self.max_bytes = max_bytes

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

        self.queue: List[str] = []
        self.queued_set: Set[str] = set()
        self.processed_count = 0
        self.total_bytes = 0
        self.failed_urls: List[tuple] = []

    def normalize_url(self, raw_url: str, context_url: str) -> str:
        """Resolve relative URLs and remove hash fragments while preserving query strings."""
        joined = urljoin(context_url, raw_url.strip())
        parsed = urlparse(joined)
        return parsed._replace(fragment="").geturl()

    def url_to_local_path(self, target_url: str) -> str:
        """Translate a target URL to a collision-safe path in output_dir."""
        return os.path.join(
            self.output_dir,
            query_variant_relpath(target_url),
        )

    def enqueue(self, url: str, context_url: str):
        clean_url = self.normalize_url(url, context_url)
        parsed = urlparse(clean_url)

        # Stay on the same host to prevent mirroring the entire internet
        if parsed.netloc == self.base_netloc and clean_url not in self.queued_set:
            self.queued_set.add(clean_url)
            self.queue.append(clean_url)

    def extract_html_assets(self, html: str, context_url: str) -> Set[str]:
        found = set()
        for attr in HTML_ATTR_REGEX.findall(html):
            clean = unescape(attr.strip())
            if not clean.startswith(("data:", "javascript:", "mailto:", "tel:")):
                found.add(clean)

        # Extract items from srcset/imagesrcset and decode HTML entities.
        for srcset in SRCSET_REGEX.findall(html):
            decoded = unescape(srcset)
            parts = [p.strip().split()[0] for p in decoded.split(",") if p.strip()]
            found.update(parts)

        return found

    def extract_css_assets(self, css: str) -> Set[str]:
        found = set()
        for match in CSS_URL_REGEX.findall(css):
            clean = match.strip()
            if not clean.startswith("data:"):
                found.add(clean)
        return found

    def extract_js_assets(self, js: str) -> Set[str]:
        found = set()
        for match in JS_STRING_LITERAL_REGEX.findall(js):
            clean = match.strip()
            if clean.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
                continue

            # Runtime template placeholders cannot be resolved statically.
            if "${" in clean:
                continue

            path_only = urlparse(clean).path.lower()
            filename = os.path.basename(path_only)
            stem, _ = os.path.splitext(filename)

            # Ignore fragments such as ".jpg" from string concatenation.
            if not stem or filename in JS_ASSET_EXTENSIONS:
                continue

            if path_only.endswith(JS_ASSET_EXTENSIONS):
                found.add(clean)

        return found

    def extract_json_assets(self, json_text: str) -> Set[str]:
        found = set()
        for match in JSON_SRC_REGEX.findall(json_text):
            clean = match.strip()
            if not clean.startswith(("data:", "javascript:")):
                found.add(clean)
        return found

    def run(self):
        os.makedirs(self.output_dir, exist_ok=True)
        print(f"[*] Target URL:  {self.base_url}")
        print(f"[*] Output Dir:  {os.path.abspath(self.output_dir)}")
        print("=" * 60)

        # 1. Fetch Root Index
        print(f"[+] Fetching root shell: {self.base_url}")
        try:
            resp = self.session.get(self.base_url, timeout=self.timeout)
            resp.raise_for_status()
            root_html = resp.text

            index_path = os.path.join(self.output_dir, "index.html")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(root_html)

            html_size = os.path.getsize(index_path)
            self.total_bytes += html_size
            self.processed_count += 1
            self.queued_set.add(self.normalize_url(self.base_url, self.base_url))
            print(f"[OK] Saved index.html ({html_size:,} bytes)")

            # Enqueue initial root assets
            for asset in self.extract_html_assets(root_html, self.base_url):
                self.enqueue(asset, self.base_url)

        except Exception as exc:
            print(f"[!] Critical error fetching root shell: {exc}", file=sys.stderr)
            return

        # 2. Enqueue common well-known files (PWA manifest, icons, etc.)
        for endpoint in self.extra_endpoints:
            self.enqueue(endpoint, self.base_url)

        # 3. Process the recursive download queue
        queue_idx = 0
        while queue_idx < len(self.queue):
            if self.max_files and self.processed_count >= self.max_files:
                print(f"[!] Capture file limit reached ({self.max_files}).")
                break
            if self.max_bytes and self.total_bytes >= self.max_bytes:
                print(f"[!] Capture size limit reached ({self.max_bytes:,} bytes).")
                break

            current_url = self.queue[queue_idx]
            queue_idx += 1

            local_path = self.url_to_local_path(current_url)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            try:
                res = self.session.get(current_url, timeout=self.timeout)
                if res.status_code != 200:
                    print(f"[-] [SKIP {res.status_code}] {current_url}")
                    self.failed_urls.append((current_url, f"HTTP {res.status_code}"))
                    continue

                content = res.content
                if self.max_bytes and self.total_bytes + len(content) > self.max_bytes:
                    print(f"[-] [LIMIT] {current_url} would exceed capture size limit")
                    self.failed_urls.append((current_url, "Capture size limit"))
                    break
                with open(local_path, "wb") as f:
                    f.write(content)

                size = len(content)
                self.total_bytes += size
                self.processed_count += 1
                rel_dest = os.path.relpath(local_path, self.output_dir)
                print(f"[+] [{self.processed_count}] {rel_dest} ({size:,} bytes)")

                content_type = res.headers.get("Content-Type", "").lower()

                # Deep scan CSS files for fonts and background images
                if local_path.lower().endswith(".css") or "text/css" in content_type:
                    css_text = content.decode("utf-8", errors="ignore")
                    for css_asset in self.extract_css_assets(css_text):
                        self.enqueue(css_asset, current_url)

                # Deep scan JS bundles for dynamically imported chunks and assets
                elif (local_path.lower().endswith(".js") or "javascript" in content_type) and self.deep_assets:
                    js_text = content.decode("utf-8", errors="ignore")
                    for js_asset in self.extract_js_assets(js_text):
                        # Preserve relative imports by resolving against the current bundle.
                        self.enqueue(js_asset, current_url)

                # Deep scan Web Manifest and JSON configs
                elif local_path.lower().endswith((".webmanifest", ".json")) or "json" in content_type:
                    json_text = content.decode("utf-8", errors="ignore")
                    for json_asset in self.extract_json_assets(json_text):
                        self.enqueue(json_asset, self.base_url)

            except Exception as exc:
                print(f"[!] [FAIL] {current_url}: {exc}")
                self.failed_urls.append((current_url, str(exc)))

        # Summary
        print("\n" + "=" * 60)
        print("CLONE SUMMARY")
        print(f"Total files saved:   {self.processed_count}")
        print(f"Total downloaded:    {self.total_bytes / (1024 * 1024):.2f} MB ({self.total_bytes:,} bytes)")
        print(f"Failed / unreachable:{len(self.failed_urls)}")
        print("=" * 60)
