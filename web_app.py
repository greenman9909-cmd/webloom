import contextlib
import io
import ipaddress
import mimetypes
import hashlib
import hmac
import json
import os
import shutil
import socket
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from html.parser import HTMLParser
from xml.sax.saxutils import escape as xml_escape

import requests
from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory, session, redirect, make_response

from spa_ripper.path_utils import query_variant_relpath
from spa_ripper.scraper import DEFAULT_USER_AGENT, SpaScraper
from webloom_integrations import (
    auth_signup,
    auth_signin,
    set_login_session,
    apply_auth_cookies,
    clear_login_session,
    current_identity,
    require_user,
    require_identity,
    require_owner,
    consume_capture_entitlement,
    create_project,
    update_project,
    list_projects,
    get_project,
    set_subscription_state,
    supabase_ready,
    storage_upload_file,
    storage_download,
    restore_free_capture,
    auth_recover,
    update_user_profile,
    claim_owner,
)


BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_JOB_ROOT = Path("/tmp/web_jobs") if os.environ.get("VERCEL") else BASE_DIR / "web_jobs"
JOB_ROOT = Path(os.environ.get("SPA_RIPPER_WEB_JOBS", _DEFAULT_JOB_ROOT)).resolve()
JOB_ROOT.mkdir(parents=True, exist_ok=True)

ALLOW_PRIVATE = os.environ.get("SPA_RIPPER_ALLOW_PRIVATE", "").lower() in {"1", "true", "yes"}
MAX_LOG_CHARS = 120_000

app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static",
    static_url_path="/__spa_ui/static",
)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.secret_key = os.environ.get("WEBLOOM_SESSION_SECRET") or os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("WEBLOOM_COOKIE_SECURE", "1") not in {"0","false","False"},
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "price_1UJzCYF0EUJm1EWSK5S9pcvU")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PUBLIC_APP_URL = os.environ.get("WEBLOOM_PUBLIC_URL", "").rstrip("/")
SPA_REKT_INTERNAL_URL = os.environ.get("SPA_REKT_INTERNAL_URL", "").rstrip("/")
SPA_REKT_INTERNAL_TOKEN = os.environ.get("SPA_REKT_INTERNAL_TOKEN", "")
SPA_REKT_ALLOWED_HOSTS = {
    h.strip().lower() for h in os.environ.get("SPA_REKT_ALLOWED_HOSTS", "").split(",") if h.strip()
}
ABUSE_SECRET = os.environ.get("WEBLOOM_ABUSE_SECRET") or str(app.secret_key)
SYNC_JOBS = (
    os.environ.get("WEBLOOM_SYNC_JOBS", "").lower() in {"1","true","yes"}
    or bool(os.environ.get("VERCEL"))
)

_jobs = {}
_jobs_lock = threading.Lock()


@app.after_request
def persist_refreshed_auth(response):
    auth_state = getattr(request, "webloom_refreshed_auth", None)
    if auth_state:
        return apply_auth_cookies(response, auth_state)
    return response


def _hmac_key(value: str) -> str:
    secret = ABUSE_SECRET.encode("utf-8") if isinstance(ABUSE_SECRET, str) else bytes(ABUSE_SECRET)
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _network_trial_key() -> str | None:
    raw = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(raw)
        prefix = 24 if addr.version == 4 else 64
        bucket = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
        return _hmac_key(str(bucket.network_address) + f"/{prefix}")
    except ValueError:
        return None


def _device_trial_key():
    device_id = request.cookies.get("wl_device")
    is_new = False
    if not device_id or len(device_id) < 24:
        device_id = uuid.uuid4().hex
        is_new = True
    return _hmac_key(device_id), device_id, is_new


def _with_device_cookie(response, device_id=None):
    existing = request.cookies.get("wl_device")
    if existing and len(existing) >= 24:
        return response
    device_id = device_id or uuid.uuid4().hex
    response.set_cookie(
        "wl_device",
        device_id,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        secure=app.config.get("SESSION_COOKIE_SECURE", True),
        samesite="Lax",
    )
    return response


def _validate_public_url(target_url: str) -> None:
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs are supported.")
    if not parsed.hostname:
        raise ValueError("That URL does not contain a valid hostname.")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in URLs are not supported.")

    if ALLOW_PRIVATE:
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        answers = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname: {exc}") from exc

    seen = set()
    for answer in answers:
        ip_text = answer[4][0]
        if ip_text in seen:
            continue
        seen.add(ip_text)
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("Private, loopback, link-local, and reserved network targets are blocked.")


class GuardedSession(requests.Session):
    def send(self, request, **kwargs):
        _validate_public_url(request.url)
        return super().send(request, **kwargs)


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_parts = []
        self.description = None
        self.canonical = None
        self.open_graph = {}

    def handle_starttag(self, tag, attrs):
        values = {str(k).lower(): v for k, v in attrs if k}
        if tag.lower() == "title":
            self.in_title = True
        elif tag.lower() == "meta":
            key = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content")
            if key == "description" and content:
                self.description = content
            if key.startswith("og:") and content:
                self.open_graph[key] = content
        elif tag.lower() == "link":
            rel = str(values.get("rel") or "").lower()
            if "canonical" in rel and values.get("href"):
                self.canonical = values["href"]

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)


def _write_project_reports(output_dir: Path, job: dict, scraper: SpaScraper):
    index_file = output_dir / "index.html"
    parser = _MetadataParser()
    try:
        parser.feed(index_file.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass

    source_url = job["url"]
    base_host = urlparse(source_url).netloc
    discovered = sorted(scraper.queued_set)
    page_urls = []
    for url in discovered:
        parsed = urlparse(url)
        if parsed.netloc != base_host:
            continue
        suffix = Path(parsed.path).suffix.lower()
        if not suffix or suffix in {".html", ".htm"}:
            page_urls.append(url)
    if source_url not in page_urls:
        page_urls.insert(0, source_url)

    files = []
    for path in output_dir.rglob("*"):
        if path.is_file():
            files.append({
                "path": path.relative_to(output_dir).as_posix(),
                "bytes": path.stat().st_size,
            })

    metadata = {
        "title": " ".join(" ".join(parser.title_parts).split()) or None,
        "description": parser.description,
        "canonical": parser.canonical,
        "open_graph": parser.open_graph,
    }
    manifest = {
        "source_url": source_url,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files_saved": scraper.processed_count,
        "bytes_saved": scraper.total_bytes,
        "failed_count": len(scraper.failed_urls),
        "pages": page_urls,
        "files": files,
    }

    (output_dir / "webloom-project.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "links.json").write_text(
        json.dumps(discovered, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sitemap.extend(f"  <url><loc>{xml_escape(url)}</loc></url>" for url in page_urls)
    sitemap.append("</urlset>")
    (output_dir / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")


class JobLog(io.TextIOBase):
    def __init__(self, job_id):
        self.job_id = job_id

    def write(self, text):
        if not text:
            return 0
        with _jobs_lock:
            job = _jobs.get(self.job_id)
            if not job:
                return len(text)
            job["log"] += text
            if len(job["log"]) > MAX_LOG_CHARS:
                job["log"] = job["log"][-MAX_LOG_CHARS:]
        return len(text)

    def flush(self):
        pass


def normalize_target(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        raise ValueError("Enter a website URL.")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    _validate_public_url(value)
    return value


def job_public(job):
    return {
        "id": job["id"],
        "url": job["url"],
        "status": job["status"],
        "created_at": job["created_at"],
        "finished_at": job.get("finished_at"),
        "files": job.get("files", 0),
        "bytes": job.get("bytes", 0),
        "failed": job.get("failed", 0),
        "error": job.get("error"),
        "log": job.get("log", ""),
        "preview_url": f"/preview/{job['id']}/" if job["status"] == "done" else None,
        "download_url": f"/api/jobs/{job['id']}/download" if job["status"] == "done" else None,
    }


def run_job(job_id):
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "running"

    output_dir = JOB_ROOT / job_id / "frontend"
    output_dir.mkdir(parents=True, exist_ok=True)
    writer = JobLog(job_id)

    try:
        scraper = SpaScraper(
            base_url=job["url"],
            output_dir=str(output_dir),
            timeout=20,
            deep_assets=bool(job.get("deep_assets")),
            max_files=job.get("max_files"),
            max_bytes=job.get("max_bytes"),
        )
        safe_session = GuardedSession()
        safe_session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
        scraper.session = safe_session

        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            scraper.run()

        if not (output_dir / "index.html").exists():
            raise RuntimeError("Capture finished without creating index.html.")

        _write_project_reports(output_dir, job, scraper)

        archive_base = JOB_ROOT / job_id / "webloom-project"
        archive_path = archive_base.with_suffix(".zip")
        shutil.make_archive(str(archive_base), "zip", root_dir=str(output_dir))

        if supabase_ready():
            owner_id = job.get("user_id")
            storage_prefix = f"projects/{owner_id}/{job_id}"
            for file_path in output_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(output_dir).as_posix()
                mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                storage_upload_file(f"{storage_prefix}/frontend/{rel}", file_path, mime, token=job.get("auth_token"))
            storage_upload_file(
                f"{storage_prefix}/webloom-project.zip",
                archive_path,
                "application/zip",
                token=job.get("auth_token"),
            )

        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "done"
            job["files"] = scraper.processed_count
            job["bytes"] = scraper.total_bytes
            job["failed"] = len(scraper.failed_urls)
            job["finished_at"] = time.time()

        if supabase_ready():
            update_project(
                job_id,
                token=job.get("auth_token"),
                status="done",
                file_count=scraper.processed_count,
                byte_count=scraper.total_bytes,
                failed_count=len(scraper.failed_urls),
                preview_path=f"/preview/{job_id}/",
                archive_path=f"projects/{job.get('user_id')}/{job_id}/webloom-project.zip",
                metadata={
                    "storage_prefix": f"projects/{job.get('user_id')}/{job_id}",
                    "capture_mode": "deep" if job.get("deep_assets") else "standard",
                    "max_files": job.get("max_files"),
                    "max_bytes": job.get("max_bytes"),
                },
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )

    except Exception as exc:
        writer.write(f"\n[!] {exc}\n")
        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "error"
            job["error"] = str(exc)
            job["finished_at"] = time.time()
        if supabase_ready():
            update_project(
                job_id,
                token=job.get("auth_token"),
                status="error",
                error=str(exc),
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            if job.get("entitlement_reason") == "free" and job.get("trial_key"):
                restore_free_capture(job.get("trial_key"), token=job.get("auth_token"))


@app.get("/")
def home():
    return render_template("index.html")




@app.get("/signin")
def signin_page():
    return render_template("signin.html")


@app.get("/signup")
def signup_page():
    return render_template("signup.html")


@app.get("/forgot")
def forgot_page():
    return render_template("forgot.html")


@app.get("/dashboard")
def dashboard_page():
    return render_template("dashboard.html")


@app.get("/new")
def new_capture_page():
    return render_template("new.html")


@app.get("/project")
@app.get("/project/<project_id>")
def project_page(project_id=None):
    return render_template("project.html")


@app.get("/pricing")
def pricing_page():
    return render_template("pricing.html")


@app.get("/billing")
def billing_page():
    return render_template("billing.html")


@app.get("/account")
def account_page():
    return render_template("account.html")


@app.get("/settings")
def settings_page():
    return render_template("settings.html")


@app.get("/admin")
def admin_page():
    ident = current_identity()
    if not ident:
        return redirect("/signin?next=/admin")
    if ident.get("role") != "owner":
        return redirect("/dashboard")
    return render_template("admin.html")


@app.get("/owner-claim")
def owner_claim_page():
    ident = current_identity()
    if not ident:
        return redirect("/signin?next=/owner-claim")
    if ident.get("is_anonymous") or not ident.get("email"):
        return redirect("/signup?next=/owner-claim")
    return render_template("owner_claim.html")


@app.post("/api/owner/claim")
@require_user
def api_owner_claim():
    if request.webloom_user.get("is_anonymous") or not request.webloom_user.get("email"):
        return jsonify({"ok": False, "error": "Use a permanent signed-in account to claim owner access."}), 409
    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "Enter the owner claim code."}), 400
    try:
        result = claim_owner(code, token=request.cookies.get("wl_access") or session.get("access_token"))
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "Owner claim was rejected."}), 403
    return jsonify({"ok": True, "redirect": "/admin"})


@app.get("/privacy")
def privacy_page():
    return render_template("privacy.html")


@app.get("/terms")
def terms_page():
    return render_template("terms.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "WebLoom",
        "supabase": supabase_ready(),
        "stripe": bool(STRIPE_SECRET_KEY and STRIPE_PRO_PRICE_ID),
    })


@app.post("/api/auth/signup")
def api_signup():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if "@" not in email or len(password) < 8:
        return jsonify({"ok": False, "error": "Use a valid email and a password with at least 8 characters."}), 400
    try:
        data = auth_signup(email, password)
        if data.get("access_token"):
            auth_state = set_login_session(data)
            response = jsonify({"ok": True, "profile": auth_state["profile"], "redirect": "/dashboard"})
            return apply_auth_cookies(response, auth_state)
        return jsonify({"ok": True, "confirmation_required": True})
    except (ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/auth/signin")
def api_signin():
    payload = request.get_json(silent=True) or {}
    try:
        auth_state = set_login_session(auth_signin(payload.get("email",""), payload.get("password","")))
        response = jsonify({"ok": True, "profile": auth_state["profile"], "redirect": "/dashboard"})
        return apply_auth_cookies(response, auth_state)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401


@app.post("/api/auth/recover")
def api_recover():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    if "@" not in email:
        return jsonify({"ok": False, "error": "Enter a valid email address."}), 400
    try:
        redirect_to = (PUBLIC_APP_URL or request.host_url.rstrip("/")) + "/signin"
        auth_recover(email, redirect_to=redirect_to)
        return jsonify({"ok": True})
    except (ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/auth/signout")
def api_signout():
    response = jsonify({"ok": True})
    return clear_login_session(response)


@app.get("/api/me")
def api_me():
    ident = current_identity()
    response = jsonify({"authenticated": bool(ident), **({"user": ident} if ident else {})})
    return _with_device_cookie(response)


@app.patch("/api/account")
@require_user
def api_account_update():
    payload = request.get_json(silent=True) or {}
    display_name = (payload.get("display_name") or "").strip()
    if len(display_name) > 80:
        return jsonify({"ok": False, "error": "Display name is too long."}), 400
    profile = update_user_profile(
        request.webloom_user["id"],
        {"display_name": display_name or None},
    )
    return jsonify({"ok": True, "profile": profile})


@app.patch("/api/settings")
@require_user
def api_settings_update():
    payload = request.get_json(silent=True) or {}
    current = request.webloom_user.get("settings") or {}
    capture_mode = payload.get("capture_mode", current.get("capture_mode", "standard"))
    export_format = payload.get("export_format", current.get("export_format", "zip"))
    project_naming = payload.get("project_naming", current.get("project_naming", "hostname"))

    if capture_mode not in {"standard", "deep"}:
        return jsonify({"ok": False, "error": "Invalid capture mode."}), 400
    if capture_mode == "deep" and request.webloom_user.get("role") != "owner" and request.webloom_user.get("plan") != "pro":
        return jsonify({"ok": False, "error": "Deep capture is a Pro setting."}), 403
    if export_format not in {"zip", "zip_metadata"}:
        return jsonify({"ok": False, "error": "Invalid export format."}), 400
    if project_naming not in {"hostname", "ask"}:
        return jsonify({"ok": False, "error": "Invalid project naming option."}), 400

    profile = update_user_profile(
        request.webloom_user["id"],
        {"settings": {
            "capture_mode": capture_mode,
            "export_format": export_format,
            "project_naming": project_naming,
        }},
    )
    return jsonify({"ok": True, "profile": profile})


@app.get("/api/projects")
@require_user
def api_projects():
    return jsonify({"projects": list_projects(request.webloom_user["id"])})


@app.get("/api/projects/<project_id>")
@require_user
def api_project(project_id):
    project = get_project(request.webloom_user["id"], project_id)
    if not project:
        abort(404)
    return jsonify({"project": project})


@app.post("/api/billing/checkout")
@require_user
def api_billing_checkout():
    if not STRIPE_SECRET_KEY or not STRIPE_PRO_PRICE_ID:
        return jsonify({"ok": False, "error": "Billing is not configured yet."}), 503
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    user = request.webloom_user
    if user.get("is_anonymous") or not user.get("email"):
        return jsonify({
            "ok": False,
            "error": "Create or sign in to an account before starting Pro billing.",
            "account_required": True,
        }), 409
    base = PUBLIC_APP_URL or request.host_url.rstrip("/")
    session_obj = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRO_PRICE_ID, "quantity": 1}],
        customer=user.get("stripe_customer_id") or None,
        customer_email=None if user.get("stripe_customer_id") else user.get("email"),
        success_url=f"{base}/billing?checkout=success",
        cancel_url=f"{base}/pricing?checkout=cancelled",
        client_reference_id=user["id"],
        metadata={"webloom_user_id": user["id"]},
        subscription_data={"metadata": {"webloom_user_id": user["id"]}},
        allow_promotion_codes=True,
    )
    return jsonify({"ok": True, "url": session_obj.url})


@app.post("/api/billing/portal")
@require_user
def api_billing_portal():
    if not STRIPE_SECRET_KEY:
        return jsonify({"ok": False, "error": "Billing is not configured yet."}), 503
    customer = request.webloom_user.get("stripe_customer_id")
    if not customer:
        return jsonify({"ok": False, "error": "No billing profile exists yet."}), 400
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    base = PUBLIC_APP_URL or request.host_url.rstrip("/")
    portal = stripe.billing_portal.Session.create(customer=customer, return_url=f"{base}/billing")
    return jsonify({"ok": True, "url": portal.url})


@app.post("/api/stripe/webhook")
def api_stripe_webhook():
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"ok": False}), 503
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(request.get_data(), signature, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return jsonify({"ok": False}), 400

    obj = event["data"]["object"]
    event_type = event["type"]
    user_id = None
    customer_id = obj.get("customer")
    subscription_id = obj.get("subscription") or (obj.get("id") if event_type.startswith("customer.subscription.") else None)
    status = None

    if event_type == "checkout.session.completed":
        user_id = (obj.get("metadata") or {}).get("webloom_user_id") or obj.get("client_reference_id")
        status = "active" if obj.get("mode") == "subscription" else None
    elif event_type.startswith("customer.subscription."):
        user_id = (obj.get("metadata") or {}).get("webloom_user_id")
        status = obj.get("status")
        customer_id = obj.get("customer")
        subscription_id = obj.get("id")

    if user_id and status:
        set_subscription_state(user_id, customer_id, subscription_id, status)
    return jsonify({"received": True})


@app.post("/api/admin/rekt")
@require_owner
def api_admin_rekt():
    if not SPA_REKT_INTERNAL_URL or not SPA_REKT_INTERNAL_TOKEN:
        return jsonify({"ok": False, "error": "Private SPA-REKT service is not configured."}), 503
    payload = request.get_json(silent=True) or {}
    try:
        target = normalize_target(payload.get("url",""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    host = (urlparse(target).hostname or "").lower()
    if not SPA_REKT_ALLOWED_HOSTS:
        return jsonify({"ok": False, "error": "The private audit allowlist is not configured."}), 503
    if host not in SPA_REKT_ALLOWED_HOSTS:
        return jsonify({"ok": False, "error": "That host is not on the owner audit allowlist."}), 403
    r = requests.post(
        f"{SPA_REKT_INTERNAL_URL}/scan",
        headers={"Authorization": f"Bearer {SPA_REKT_INTERNAL_TOKEN}"},
        json={"url": target, "authorized": True},
        timeout=120,
    )
    try:
        body = r.json()
    except Exception:
        body = {"error": r.text[:1000] or "Private audit service returned an invalid response."}
    if not r.ok:
        return jsonify({"ok": False, "error": body.get("error") or "Private audit failed."}), r.status_code
    return jsonify({"ok": True, "result": body.get("result", body)}), 200


@app.post("/api/clone")
@require_identity
def clone():
    payload = request.get_json(silent=True) or {}
    try:
        target = normalize_target(payload.get("url", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if not supabase_ready():
        return jsonify({"ok": False, "error": "Database/auth service is not configured yet."}), 503

    user = request.webloom_user
    network_key = _network_trial_key()
    trial_key, device_id, device_is_new = _device_trial_key()
    job_id = str(uuid.uuid4())
    auth_token = request.cookies.get("wl_access") or session.get("access_token")

    try:
        entitlement = consume_capture_entitlement(
            trial_key,
            network_key=network_key,
            token=auth_token,
        )
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    if not entitlement.get("allowed"):
        reason = entitlement.get("reason")
        if reason == "free_network_rate_limited":
            message = "The free capture limit for this network has been reached. Pro can continue without that free-use limit."
        elif reason == "free_device_used":
            message = "The free capture for this browser has already been used. Upgrade to Pro to continue."
        else:
            message = "Your free capture has been used. Upgrade to Pro to continue."
        return jsonify({
            "ok": False,
            "error": message,
            "upgrade_required": True,
            "reason": reason,
        }), 402

    try:
        create_project(
            user["id"],
            job_id,
            target,
            token=auth_token,
        )
    except RuntimeError as exc:
        if entitlement.get("reason") == "free":
            restore_free_capture(trial_key, token=auth_token)
        return jsonify({"ok": False, "error": str(exc)}), 503

    is_paid = (
        user.get("role") == "owner"
        or (
            user.get("plan") == "pro"
            and user.get("subscription_status") in {"active", "trialing"}
        )
    )

    deep_assets = bool(is_paid)
    max_files = 2000 if user.get("role") == "owner" else (1000 if is_paid else 250)
    max_bytes = (
        500 * 1024 * 1024
        if user.get("role") == "owner"
        else (250 * 1024 * 1024 if is_paid else 50 * 1024 * 1024)
    )

    update_project(
        job_id,
        token=auth_token,
        engine="webloom-deep" if deep_assets else "webloom-standard",
        metadata={
            "capture_mode": "deep" if deep_assets else "standard",
            "anonymous": bool(user.get("is_anonymous")),
        },
    )

    job = {
        "id": job_id,
        "url": target,
        "status": "queued",
        "created_at": time.time(),
        "finished_at": None,
        "files": 0,
        "bytes": 0,
        "failed": 0,
        "error": None,
        "log": "",
        "user_id": user["id"],
        "entitlement_reason": entitlement.get("reason"),
        "trial_key": trial_key,
        "deep_assets": deep_assets,
        "max_files": max_files,
        "max_bytes": max_bytes,
        "auth_token": auth_token,
    }

    with _jobs_lock:
        _jobs[job_id] = job

    if SYNC_JOBS:
        run_job(job_id)
        with _jobs_lock:
            finished = _jobs.get(job_id, job)
        status_code = 201 if finished.get("status") == "done" else 500
        response = jsonify({
            "ok": finished.get("status") == "done",
            "job": job_public(finished),
            **({"error": finished.get("error") or "Capture failed."} if finished.get("status") != "done" else {}),
        })
        if device_is_new:
            response = _with_device_cookie(response, device_id=device_id)
        return response, status_code

    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    response = jsonify({"ok": True, "job": job_public(job)})
    if device_is_new:
        response = _with_device_cookie(response, device_id=device_id)
    return response, 202


@app.get("/api/jobs")
@require_user
def jobs():
    with _jobs_lock:
        items = [
            item for item in _jobs.values()
            if item.get("user_id") == request.webloom_user["id"]
        ]
        items = sorted(items, key=lambda item: item["created_at"], reverse=True)[:20]
        return jsonify({"jobs": [job_public(item) for item in items]})


@app.get("/api/jobs/<job_id>")
@require_user
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job and job.get("user_id") == request.webloom_user["id"]:
            return jsonify({"job": job_public(job)})

    project = get_project(request.webloom_user["id"], job_id) if supabase_ready() else None
    if not project:
        abort(404)
    return jsonify({"job": {
        "id": project["id"],
        "url": project["source_url"],
        "status": project["status"],
        "created_at": project.get("created_at"),
        "finished_at": project.get("finished_at"),
        "files": project.get("file_count", 0),
        "bytes": project.get("byte_count", 0),
        "failed": project.get("failed_count", 0),
        "error": project.get("error"),
        "preview_url": f"/preview/{job_id}/" if project["status"] == "done" else None,
        "download_url": f"/api/jobs/{job_id}/download" if project["status"] == "done" else None,
    }})


@app.get("/api/jobs/<job_id>/download")
@require_user
def job_download(job_id):
    user_id = request.webloom_user["id"]
    project = get_project(user_id, job_id) if supabase_ready() else None

    with _jobs_lock:
        job = _jobs.get(job_id)

    if project and project.get("status") == "done":
        stored_path = project.get("archive_path") or f"projects/{user_id}/{job_id}/webloom-project.zip"
        stored = storage_download(stored_path)
        if stored:
            data, _ = stored
            host = urlparse(project.get("source_url") or "").hostname or "frontend"
            safe_host = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in host)
            return send_file(
                io.BytesIO(data),
                as_attachment=True,
                download_name=f"{safe_host}-webloom.zip",
                mimetype="application/zip",
            )

    if not job or job.get("user_id") != user_id or job.get("status") != "done":
        abort(404)

    base = JOB_ROOT / job_id
    frontend = base / "frontend"
    archive_base = base / "webloom-project"
    archive_path = archive_base.with_suffix(".zip")
    if not archive_path.exists():
        shutil.make_archive(str(archive_base), "zip", root_dir=str(frontend))

    host = urlparse(job["url"]).hostname or "frontend"
    safe_host = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in host)
    return send_file(
        archive_path,
        as_attachment=True,
        download_name=f"{safe_host}-webloom.zip",
        mimetype="application/zip",
    )


def _serve_preview_file(job_id, asset_path, remember=False):
    ident = current_identity()
    if not ident:
        abort(401)

    project = get_project(ident["id"], job_id) if supabase_ready() else None
    with _jobs_lock:
        job = _jobs.get(job_id)

    if project:
        if project.get("status") != "done":
            abort(404)
    elif not job or job.get("user_id") != ident["id"] or job.get("status") != "done":
        abort(404)

    request_rel = asset_path or "index.html"
    query = request.query_string.decode("utf-8", errors="ignore")

    def finish(response):
        if remember:
            response.set_cookie(
                "spa_preview_job",
                job_id,
                max_age=3600,
                httponly=True,
                samesite="Lax",
                secure=app.config.get("SESSION_COOKIE_SECURE", True),
            )
        return response

    candidates = []
    if query:
        candidates.append(query_variant_relpath("/" + request_rel + "?" + query))
    candidates.append(request_rel)

    if project and supabase_ready():
        prefix = (project.get("metadata") or {}).get("storage_prefix") or f"projects/{ident['id']}/{job_id}"
        for rel in candidates:
            if rel.startswith("../") or "/../" in rel:
                abort(404)
            stored = storage_download(f"{prefix}/frontend/{rel}")
            if stored:
                data, content_type = stored
                response = app.response_class(data, mimetype=content_type)
                return finish(response)
        if "." not in Path(request_rel).name:
            stored = storage_download(f"{prefix}/frontend/index.html")
            if stored:
                data, content_type = stored
                return finish(app.response_class(data, mimetype=content_type))
        abort(404)

    root = (JOB_ROOT / job_id / "frontend").resolve()
    for rel in candidates:
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            abort(404)
        if target.is_file():
            return finish(send_from_directory(root, rel))

    if "." not in Path(request_rel).name:
        return finish(send_from_directory(root, "index.html"))

    abort(404)


@app.get("/preview/<job_id>/", defaults={"asset_path": ""})
@app.get("/preview/<job_id>/<path:asset_path>")
def preview(job_id, asset_path):
    return _serve_preview_file(job_id, asset_path, remember=True)


@app.get("/<path:asset_path>")
def preview_root_asset(asset_path):
    job_id = request.cookies.get("spa_preview_job")
    if not job_id:
        abort(404)
    return _serve_preview_file(job_id, asset_path, remember=False)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "7860"))
    app.run(host=host, port=port, threaded=True)
