import os
import sys
import json
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import requests

from spa_ripper.path_utils import query_variant_relpath


class SpaDevServer:
    """Generic local server for a cloned SPA with optional API reverse proxying."""

    def __init__(
        self,
        directory: str,
        port: int = 8080,
        host: str = "0.0.0.0",
        proxy_target=None,
        api_prefix: str = "/api/",
    ):
        self.directory = os.path.abspath(directory)
        self.port = port
        self.host = host
        self.proxy_target = proxy_target.rstrip("/") if proxy_target else None
        self.api_prefix = api_prefix

    def make_handler(self):
        root_dir = self.directory
        proxy_target = self.proxy_target
        api_prefix = self.api_prefix

        class SpaHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=root_dir, **kwargs)

            def do_POST(self):
                if proxy_target and self.path.startswith(api_prefix):
                    self.proxy_request("POST")
                    return

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def do_GET(self):
                if proxy_target and self.path.startswith(api_prefix):
                    self.proxy_request("GET")
                    return

                # Query-bearing assets (notably Next.js /_next/image) are
                # stored under deterministic query-specific local filenames.
                local_rel = query_variant_relpath(self.path)
                local_file = os.path.join(root_dir, local_rel)
                if os.path.exists(local_file) and not os.path.isdir(local_file):
                    self.path = "/" + local_rel.replace(os.sep, "/")
                    return super().do_GET()

                # SPA client-side route fallback (no extension = route, not asset)
                path_no_qs = self.path.split("?", 1)[0]
                if not os.path.splitext(path_no_qs)[1]:
                    self.path = "/index.html"
                    return super().do_GET()

                # Transparent asset pass-through: if the file is missing locally
                # but we have a proxy origin, fetch it live and cache to disk.
                local_abs = os.path.join(root_dir, path_no_qs.lstrip("/").replace("/", os.sep))
                if not os.path.exists(local_abs) and proxy_target:
                    self._fetch_and_cache(local_abs)
                    return

                return super().do_GET()

            def _fetch_and_cache(self, local_save_path):
                """Fetch a missing static asset from the proxy origin, serve it, and cache locally."""
                upstream_url = f"{proxy_target}{self.path}"
                req_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": proxy_target + "/",
                }
                try:
                    resp = requests.get(upstream_url, headers=req_headers, timeout=15)
                    if resp.status_code == 200:
                        try:
                            os.makedirs(os.path.dirname(local_save_path), exist_ok=True)
                            with open(local_save_path, "wb") as fh:
                                fh.write(resp.content)
                        except Exception:
                            pass
                        ct = resp.headers.get("Content-Type", "application/octet-stream")
                        self.send_response(200)
                        self.send_header("Content-Type", ct)
                        self.send_header("Content-Length", str(len(resp.content)))
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        try:
                            self.wfile.write(resp.content)
                        except (ConnectionResetError, BrokenPipeError):
                            pass
                    else:
                        self.send_error(resp.status_code, "Upstream error")
                except Exception as exc:
                    self.send_error(502, f"Proxy fetch failed: {exc}")

            def proxy_request(self, method: str):
                target_url = f"{proxy_target}{self.path}"
                body = None
                if method == "POST":
                    length = int(self.headers.get("Content-Length", 0))
                    if length > 0:
                        body = self.rfile.read(length)

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": self.headers.get("Accept", "*/*"),
                }
                content_type = self.headers.get("Content-Type")
                if content_type:
                    headers["Content-Type"] = content_type

                try:
                    res = requests.request(
                        method=method,
                        url=target_url,
                        headers=headers,
                        data=body,
                        timeout=10,
                        allow_redirects=True,
                    )
                    self.send_response(res.status_code)
                    for k, v in res.headers.items():
                        if k.lower() not in (
                            "content-length",
                            "content-encoding",
                            "transfer-encoding",
                            "connection",
                        ):
                            self.send_header(k, v)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(res.content)))
                    self.end_headers()
                    self.wfile.write(res.content)
                except Exception as exc:
                    payload = json.dumps(
                        {"ok": False, "proxy_error": str(exc)}
                    ).encode("utf-8")
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        return SpaHandler

    def start(self):
        handler_class = self.make_handler()
        server = ThreadingHTTPServer((self.host, self.port), handler_class)
        print(f"[*] SPA Server listening on http://localhost:{self.port}")
        print(f"[*] Serving Directory:     {self.directory}")
        if self.proxy_target:
            print(f"[*] Proxying {self.api_prefix}* -> {self.proxy_target}")
        else:
            print("[*] Running in offline mode (no API proxy)")
        print("[*] Press Ctrl+C to stop.")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
        finally:
            server.server_close()


PORT = 8080
DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ani.pm_frontend")
REANIME_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ReAnime.to-API",
    "reanime",
    "static",
)
REANIME_ORIGIN = os.environ.get("REANIME_URL", "https://owais-anime-stream-open.onrender.com").rstrip("/")
FALLBACK_ORIGIN = "https://ani.pm"

SELECTION_MAP = {}
LAST_ANILIST_ID = "189046"


class AniPMProxyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # Keep logs readable
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def safe_write(self, data):
        try:
            self.wfile.write(data)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def do_POST(self):
        if self.path.startswith("/api/"):
            self.proxy_api("POST")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.safe_write(b'{"ok":true}')

    def do_GET(self):
        # 1. Forward API and Embed requests
        if self.path.startswith("/api/") or self.path.startswith("/embed/"):
            self.proxy_api("GET")
            return

        # 2. Serve ReAnime static assets (/static/embed.js, etc.)
        if self.path.startswith("/static/"):
            rel_path = self.path[len("/static/"):].split("?")[0].lstrip("/")
            local_static_file = os.path.join(REANIME_STATIC_DIR, rel_path)
            if os.path.exists(local_static_file) and not os.path.isdir(local_static_file):
                mime, _ = mimetypes.guess_type(local_static_file)
                try:
                    with open(local_static_file, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", mime or "application/javascript")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.safe_write(data)
                    return
                except Exception:
                    pass
            # If not found locally, proxy to ReAnime origin
            self.proxy_to_reanime("GET")
            return

        # 3. Serve static file if it exists locally
        req_path = self.translate_path(self.path.split("?")[0])
        if os.path.exists(req_path) and not os.path.isdir(req_path):
            return super().do_GET()

        # 4. If missing file has an extension (images, banners, logos, chunks), fetch from upstream & cache
        ext = os.path.splitext(self.path.split("?")[0])[1].lower()
        if ext:
            self.fetch_and_cache(req_path)
            return

        # 5. SPA client-side routing fallback (all route paths serve index.html)
        self.path = "/index.html"
        return super().do_GET()

    def fetch_and_cache(self, local_save_path):
        """Fetch missing static asset from upstream ani.pm and cache locally on disk."""
        upstream_url = f"{FALLBACK_ORIGIN}{self.path}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://ani.pm/",
        }

        try:
            resp = requests.get(upstream_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                # Cache to disk for future requests
                try:
                    os.makedirs(os.path.dirname(local_save_path), exist_ok=True)
                    with open(local_save_path, "wb") as f:
                        f.write(resp.content)
                except Exception:
                    pass

                content_type = resp.headers.get("Content-Type") or self.guess_type(local_save_path)
                self.send_response(200)
                self.send_header("Content-Type", content_type or "application/octet-stream")
                self.send_header("Content-Length", str(len(resp.content)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.safe_write(resp.content)
                return
            else:
                self.send_error(resp.status_code, "Upstream Not Found")
                return
        except Exception:
            self.send_error(404, "File Not Found")
            return

    def proxy_to_reanime(self, method):
        target_url = f"{REANIME_ORIGIN}{self.path}"
        try:
            resp = requests.request(
                method=method,
                url=target_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=10,
            )
            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() not in ("content-length", "content-encoding", "transfer-encoding"):
                    self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp.content)))
            self.end_headers()
            self.safe_write(resp.content)
        except Exception:
            self.send_error(502, "Bad Gateway")

    def proxy_api(self, method):
        global LAST_ANILIST_ID
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = self.rfile.read(length)

        # Intercept settlar session: serve ReAnime embed player directly
        if self.path.startswith("/api/anime/settlar/session"):
            parsed_qs = parse_qs(urlparse(self.path).query)
            ep = parsed_qs.get("ep", ["1"])[0]
            sel = parsed_qs.get("selection", [""])[0]
            ani_id = SELECTION_MAP.get(sel, LAST_ANILIST_ID)

            host = self.headers.get("Host", f"localhost:{PORT}")
            embed_url = f"http://{host}/embed/ani/{ani_id}/{ep}"

            payload = json.dumps({
                "embedUrl": embed_url,
                "expiresAt": 2147483647,
                "provider": "anipm",
            }).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.safe_write(payload)
            return

        # 1. Try ReAnime backend first
        reanime_url = f"{REANIME_ORIGIN}{self.path}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, */*",
        }

        try:
            resp = requests.request(
                method=method,
                url=reanime_url,
                headers=headers,
                data=body,
                timeout=6,
                allow_redirects=True,
            )
            if resp.status_code < 400:
                self.send_response(resp.status_code)
                for k, v in resp.headers.items():
                    if k.lower() not in ("content-length", "content-encoding", "transfer-encoding"):
                        self.send_header(k, v)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(resp.content)))
                self.end_headers()
                self.safe_write(resp.content)
                return
        except Exception:
            pass

        # 2. Fallback to upstream origin
        fallback_url = f"{FALLBACK_ORIGIN}{self.path}"
        fallback_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://ani.pm/",
            "Origin": "https://ani.pm",
        }

        try:
            resp = requests.request(
                method=method,
                url=fallback_url,
                headers=fallback_headers,
                data=body,
                timeout=10,
                allow_redirects=True,
            )

            if "/api/anime/playback-bootstrap" in self.path and resp.status_code == 200:
                try:
                    data = resp.json()
                    core = data.get("core") or {}
                    ani_id = core.get("anilistId")
                    sel = data.get("settlarSelection")
                    if ani_id:
                        LAST_ANILIST_ID = str(ani_id)
                        if sel:
                            SELECTION_MAP[sel] = str(ani_id)
                except Exception:
                    pass

            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() not in ("content-length", "content-encoding", "transfer-encoding"):
                    self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp.content)))
            self.end_headers()
            self.safe_write(resp.content)
        except Exception:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.safe_write(b'{"ok":true}')


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AniPMProxyHandler)
    print(f"[*] Serving ani.pm frontend at http://localhost:{PORT}")
    print(f"[*] Primary backend: {REANIME_ORIGIN}")
    print(f"[*] Upstream fallback: {FALLBACK_ORIGIN}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()


if __name__ == "__main__":
    main()
