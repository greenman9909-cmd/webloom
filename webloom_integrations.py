import os
import hashlib
import secrets
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import jsonify, request, session, has_request_context, make_response

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://lmpyaxhviskivbdigyqo.supabase.co").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "sb_publishable_ZsDJAyJ2THaIKi_OhCXHYw__sQfGdxp")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
STORAGE_BUCKET = os.environ.get("WEBLOOM_STORAGE_BUCKET", "webloom-projects")


def supabase_ready():
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def _session_token():
    if not has_request_context():
        return None
    return session.get("access_token")


def _sb_headers(token=None, content_type="application/json"):
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": content_type,
        "Authorization": f"Bearer {token or SUPABASE_ANON_KEY}",
    }
    return headers


def _service_headers():
    if not SUPABASE_SERVICE_ROLE_KEY:
        return None
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def _auth_error(response, fallback):
    try:
        data = response.json()
    except Exception:
        data = {}
    return data.get("msg") or data.get("error_description") or data.get("message") or fallback


def auth_anonymous():
    if not supabase_ready():
        raise RuntimeError("Supabase is not configured.")
    # GoTrue uses the signup endpoint for anonymous sessions when no email/phone is supplied.
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers=_sb_headers(),
        json={"data": {"webloom_guest": True}},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(_auth_error(r, "Anonymous session could not be created."))
    return r.json()


def auth_signup(email, password):
    if not supabase_ready():
        raise RuntimeError("Supabase is not configured.")
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers=_sb_headers(),
        json={"email": email, "password": password},
        timeout=20,
    )
    if not r.ok:
        raise ValueError(_auth_error(r, "Sign up failed."))
    return r.json()


def auth_signin(email, password):
    if not supabase_ready():
        raise RuntimeError("Supabase is not configured.")
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers=_sb_headers(),
        json={"email": email, "password": password},
        timeout=20,
    )
    if not r.ok:
        raise ValueError(_auth_error(r, "Invalid email or password."))
    return r.json()


def auth_user_from_token(token):
    if not token:
        return None
    r = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers=_sb_headers(token),
        timeout=20,
    )
    return r.json() if r.ok else None


def auth_refresh(refresh_token):
    if not refresh_token:
        return None
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
        headers=_sb_headers(),
        json={"refresh_token": refresh_token},
        timeout=20,
    )
    return r.json() if r.ok else None


def auth_recover(email, redirect_to=None):
    if not supabase_ready():
        raise RuntimeError("Supabase is not configured.")
    url = f"{SUPABASE_URL}/auth/v1/recover"
    if redirect_to:
        url += "?redirect_to=" + requests.utils.quote(redirect_to, safe="")
    r = requests.post(url, headers=_sb_headers(), json={"email": email}, timeout=20)
    if not r.ok:
        raise ValueError(_auth_error(r, "Could not send recovery email."))
    return True


def set_login_session(auth_data):
    user = auth_data.get("user") or {}
    token = auth_data.get("access_token")
    refresh_token = auth_data.get("refresh_token")
    if not user.get("id") or not token:
        raise ValueError("Authentication did not return a user session.")
    session.clear()
    session["user_id"] = user["id"]
    session["email"] = (user.get("email") or "").lower()
    session["access_token"] = token
    if refresh_token:
        session["refresh_token"] = refresh_token
    session.permanent = True
    profile = current_identity(refresh=False)
    return {
        "profile": profile,
        "access_token": token,
        "refresh_token": refresh_token,
    }


def apply_auth_cookies(response, auth_state):
    access_token = (auth_state or {}).get("access_token")
    refresh_token = (auth_state or {}).get("refresh_token")
    secure = os.environ.get("WEBLOOM_COOKIE_SECURE", "1") not in {"0", "false", "False"}
    if access_token:
        response.set_cookie(
            "wl_access",
            access_token,
            max_age=60 * 60,
            httponly=True,
            secure=secure,
            samesite="Lax",
        )
    if refresh_token:
        response.set_cookie(
            "wl_refresh",
            refresh_token,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=secure,
            samesite="Lax",
        )
    return response


def clear_login_session(response=None):
    session.clear()
    if response is not None:
        response.delete_cookie("wl_access")
        response.delete_cookie("wl_refresh")
        return response
    return None


def profile_for(user_id, token=None):
    token = token or _session_token()
    if not token:
        return None
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers={**_sb_headers(token), "Accept": "application/vnd.pgrst.object+json"},
        params={"id": f"eq.{user_id}", "select": "*"},
        timeout=20,
    )
    return r.json() if r.ok else None


def current_identity(refresh=True):
    if not has_request_context():
        return None

    token = request.cookies.get("wl_access") or session.get("access_token")
    refresh_token = request.cookies.get("wl_refresh") or session.get("refresh_token")
    if not token:
        if session.get("guest_id") or (session.get("user_id") and session.get("is_anonymous")):
            return {
                "id": session.get("guest_id") or session.get("user_id"),
                "email": session.get("email") or "",
                "is_anonymous": True,
                "role": "user",
                "plan": "free",
                "subscription_status": None,
                "free_capture_used": False,
                "stripe_customer_id": None,
                "display_name": "Guest",
                "settings": {},
            }
        return None

    user = auth_user_from_token(token)
    if not user and refresh and refresh_token:
        refreshed = auth_refresh(refresh_token)
        if refreshed:
            token = refreshed.get("access_token")
            user = refreshed.get("user") or auth_user_from_token(token)
            if token and user:
                session["access_token"] = token
                session["refresh_token"] = refreshed.get("refresh_token") or refresh_token
                session["user_id"] = user.get("id")
                session["email"] = (user.get("email") or "").lower()
                request.webloom_refreshed_auth = {
                    "access_token": token,
                    "refresh_token": refreshed.get("refresh_token") or refresh_token,
                }

    if not user or not user.get("id"):
        session.clear()
        return None

    user_id = user["id"]
    session["user_id"] = user_id
    session["email"] = (user.get("email") or "").lower()
    session["access_token"] = token
    if refresh_token:
        session["refresh_token"] = refresh_token

    profile = profile_for(user_id, token) or {}
    return {
        "id": user_id,
        "email": user.get("email") or "",
        "is_anonymous": bool(user.get("is_anonymous") or profile.get("is_anonymous")),
        "role": profile.get("role", "user"),
        "plan": profile.get("plan", "free"),
        "subscription_status": profile.get("subscription_status"),
        "free_capture_used": bool(profile.get("free_capture_used")),
        "stripe_customer_id": profile.get("stripe_customer_id"),
        "display_name": profile.get("display_name") or "",
        "settings": profile.get("settings") or {},
    }


def ensure_identity():
    ident = current_identity()
    if ident:
        return ident
    try:
        auth_data = auth_anonymous()
        auth_state = set_login_session(auth_data)
        request.webloom_new_auth = auth_state
        return auth_state.get("profile") or current_identity(refresh=False)
    except Exception:
        guest_id = session.get("guest_id") or f"guest_{secrets.token_hex(12)}"
        session["guest_id"] = guest_id
        session["user_id"] = guest_id
        session["is_anonymous"] = True
        return {
            "id": guest_id,
            "email": "",
            "is_anonymous": True,
            "role": "user",
            "plan": "free",
            "subscription_status": None,
            "free_capture_used": False,
            "stripe_customer_id": None,
            "display_name": "Guest",
            "settings": {},
        }


def require_user(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        ident = current_identity()
        if not ident:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        request.webloom_user = ident
        return fn(*args, **kwargs)
    return wrapped


def require_identity(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            ident = ensure_identity()
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        request.webloom_user = ident
        result = fn(*args, **kwargs)
        auth_state = getattr(request, "webloom_new_auth", None)
        if auth_state:
            response = make_response(result)
            return apply_auth_cookies(response, auth_state)
        return result
    return wrapped


def require_owner(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        ident = current_identity()
        if not ident:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        if ident.get("role") != "owner":
            return jsonify({"ok": False, "error": "Owner access required."}), 403
        request.webloom_user = ident
        return fn(*args, **kwargs)
    return wrapped


def consume_capture_entitlement(trial_key, network_key=None, token=None):
    token = token or _session_token()
    headers = _service_headers() or (_sb_headers(token) if token else _sb_headers())
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/consume_capture_entitlement",
            headers=headers,
            json={
                "p_trial_key": trial_key,
                "p_network_key": network_key,
            },
            timeout=10,
        )
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {"allowed": True, "reason": "free"}


def create_project(user_id, project_id, source_url, token=None):
    token = token or _session_token()
    headers = _service_headers() or (_sb_headers(token) if token else _sb_headers())
    try:
        from urllib.parse import urlparse
        hostname = urlparse(source_url).hostname or ""
    except Exception:
        hostname = ""
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/projects",
            headers={**headers, "Prefer": "return=representation"},
            json={
                "id": project_id,
                "user_id": user_id,
                "source_url": source_url,
                "hostname": hostname,
                "status": "queued",
                "engine": "webloom-standard",
            },
            timeout=10,
        )
        if r.ok:
            rows = r.json()
            if isinstance(rows, list):
                return rows[0] if rows else {}
            return rows or {}
    except Exception:
        pass
    return {
        "id": project_id,
        "user_id": user_id,
        "source_url": source_url,
        "hostname": hostname,
        "status": "queued",
    }


def restore_free_capture(trial_key, token=None):
    token = token or _session_token()
    headers = _service_headers() or (_sb_headers(token) if token else _sb_headers())
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/restore_free_capture",
            headers=headers,
            json={"p_trial_key": trial_key},
            timeout=10,
        )
        return r.ok
    except Exception:
        return False


def update_project(project_id, token=None, **fields):
    token = token or _session_token()
    headers = _service_headers() or (_sb_headers(token) if token else _sb_headers())
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/projects",
            headers={**headers, "Prefer": "return=minimal"},
            params={"id": f"eq.{project_id}"},
            json=fields,
            timeout=10,
        )
        return r.ok
    except Exception:
        return False


def list_projects(user_id, limit=50, token=None):
    token = token or _session_token()
    headers = _service_headers() or (_sb_headers(token) if token else _sb_headers())
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/projects",
            headers=headers,
            params={
                "user_id": f"eq.{user_id}",
                "select": "*",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            timeout=10,
        )
        return r.json() if r.ok else []
    except Exception:
        return []


def get_project(user_id, project_id, token=None):
    token = token or _session_token()
    headers = _service_headers() or (_sb_headers(token) if token else _sb_headers())
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/projects",
            headers={**headers, "Accept": "application/vnd.pgrst.object+json"},
            params={
                "user_id": f"eq.{user_id}",
                "id": f"eq.{project_id}",
                "select": "*",
            },
            timeout=10,
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def update_user_profile(user_id, updates, token=None):
    token = token or _session_token()
    if not token:
        raise RuntimeError("Authentication required.")
    allowed = {"display_name", "settings"}
    payload = {k: v for k, v in (updates or {}).items() if k in allowed}
    if not payload:
        return profile_for(user_id, token) or {}
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers={
            **_sb_headers(token),
            "Prefer": "return=representation",
        },
        params={"id": f"eq.{user_id}"},
        json=payload,
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(_auth_error(r, "Could not update profile."))
    rows = r.json()
    if isinstance(rows, list):
        return rows[0] if rows else {}
    return rows or {}


def set_subscription_state(user_id, customer_id, subscription_id, status):
    headers = _service_headers()
    if not headers:
        return False
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/set_subscription_state",
        headers=headers,
        json={
            "p_user": user_id,
            "p_customer": customer_id,
            "p_subscription": subscription_id,
            "p_status": status,
        },
        timeout=20,
    )
    return r.ok


def storage_upload_bytes(object_path, data, content_type="application/octet-stream", token=None):
    token = token or _session_token()
    headers = _service_headers(content_type) or (_sb_headers(token, content_type) if token else None)
    if not headers:
        return None
    clean = object_path.lstrip("/")
    try:
        r = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{clean}",
            headers={
                **headers,
                "x-upsert": "true",
            },
            data=data,
            timeout=30,
        )
        if not r.ok:
            return None
        return clean
    except Exception:
        return None


def storage_upload_file(object_path, file_path, content_type="application/octet-stream", token=None):
    try:
        with open(file_path, "rb") as fh:
            return storage_upload_bytes(object_path, fh.read(), content_type, token)
    except Exception:
        return None


def storage_download(object_path, token=None):
    token = token or _session_token()
    headers = _service_headers() or (_sb_headers(token) if token else _sb_headers())
    clean = object_path.lstrip("/")
    try:
        r = requests.get(
            f"{SUPABASE_URL}/storage/v1/object/authenticated/{STORAGE_BUCKET}/{clean}",
            headers=headers,
            timeout=30,
        )
        if not r.ok:
            r_pub = requests.get(
                f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{clean}",
                headers=headers,
                timeout=30,
            )
            if not r_pub.ok:
                return None
            r = r_pub
        return r.content, r.headers.get("content-type") or "application/octet-stream"
    except Exception:
        return None


def claim_owner(code, token=None):
    token = token or request.cookies.get("wl_access") or session.get("access_token")
    if not token:
        raise RuntimeError("Authentication required.")
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/claim_webloom_owner",
        headers=_sb_headers(token),
        json={"p_code": code},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError("Could not claim owner access.")
    return r.json()


def create_api_key(user_id, name="Default key"):
    headers = _service_headers()
    if not headers:
        raise RuntimeError("Server API-key management is not configured.")
    raw_key = "wl_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    prefix = raw_key[:12]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/api_keys",
        headers={**headers, "Prefer": "return=representation"},
        json={
            "user_id": user_id,
            "name": (name or "Default key")[:80],
            "key_prefix": prefix,
            "key_hash": key_hash,
        },
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(_auth_error(r, "Could not create API key."))
    rows = r.json()
    row = rows[0] if isinstance(rows, list) and rows else (rows or {})
    return raw_key, row


def list_api_keys(user_id, token=None):
    token = token or _session_token()
    if not token:
        return []
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/api_keys",
        headers=_sb_headers(token),
        params={
            "user_id": f"eq.{user_id}",
            "select": "id,name,key_prefix,created_at,last_used_at,revoked_at",
            "order": "created_at.desc",
        },
        timeout=20,
    )
    return r.json() if r.ok else []


def revoke_api_key(user_id, key_id, token=None):
    token = token or _session_token()
    if not token:
        raise RuntimeError("Authentication required.")
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/api_keys",
        headers={**_sb_headers(token), "Prefer": "return=minimal"},
        params={"id": f"eq.{key_id}", "user_id": f"eq.{user_id}"},
        json={"revoked_at": datetime.now(timezone.utc).isoformat()},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(_auth_error(r, "Could not revoke API key."))
    return True


def identity_from_api_key(raw_key):
    if not raw_key or not raw_key.startswith("wl_"):
        return None
    headers = _service_headers()
    if not headers:
        return None
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/api_keys",
        headers={**headers, "Accept": "application/vnd.pgrst.object+json"},
        params={
            "key_hash": f"eq.{key_hash}",
            "revoked_at": "is.null",
            "select": "id,user_id,name",
        },
        timeout=20,
    )
    if not r.ok:
        return None
    key_row = r.json() or {}
    user_id = key_row.get("user_id")
    if not user_id:
        return None

    profile_response = requests.get(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers={**headers, "Accept": "application/vnd.pgrst.object+json"},
        params={"id": f"eq.{user_id}", "select": "*"},
        timeout=20,
    )
    if not profile_response.ok:
        return None
    profile = profile_response.json() or {}

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/api_keys",
        headers={**headers, "Prefer": "return=minimal"},
        params={"id": f"eq.{key_row.get('id')}"},
        json={"last_used_at": datetime.now(timezone.utc).isoformat()},
        timeout=10,
    )

    return {
        "id": user_id,
        "email": profile.get("email") or "",
        "is_anonymous": bool(profile.get("is_anonymous")),
        "role": profile.get("role", "user"),
        "plan": profile.get("plan", "free"),
        "subscription_status": profile.get("subscription_status"),
        "free_capture_used": bool(profile.get("free_capture_used")),
        "stripe_customer_id": profile.get("stripe_customer_id"),
        "display_name": profile.get("display_name") or "",
        "settings": profile.get("settings") or {},
        "api_key_id": key_row.get("id"),
        "api_key_name": key_row.get("name") or "",
    }
