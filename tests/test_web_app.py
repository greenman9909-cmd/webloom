import unittest
from unittest.mock import patch

import web_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.client = web_app.app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_homepage_renders_console(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"WebLoom", response.data)
        self.assertIn(b"Capture the frontend", response.data)

    def test_private_loopback_target_is_blocked(self):
        with self.assertRaises(ValueError):
            web_app.normalize_target("http://127.0.0.1:8080")

    def test_missing_scheme_defaults_to_https(self):
        with patch.object(web_app, "_validate_public_url"):
            self.assertEqual(
                web_app.normalize_target("example.com"),
                "https://example.com",
            )

    def test_signin_returns_profile_and_redirect(self):
        auth_payload = {
            "profile": {"id": "user-1", "plan": "free"},
            "access_token": "access",
            "refresh_token": "refresh",
        }
        with (
            patch.object(web_app, "auth_signin", return_value={"user": {"id": "user-1"}}),
            patch.object(web_app, "set_login_session", return_value=auth_payload),
            patch.object(web_app, "apply_auth_cookies", side_effect=lambda response, state: response),
        ):
            response = self.client.post(
                "/api/auth/signin",
                json={"email": "user@example.com", "password": "password123"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["profile"]["id"], "user-1")
        self.assertEqual(body["redirect"], "/dashboard")

    def test_signup_confirmation_required(self):
        with patch.object(web_app, "auth_signup", return_value={"user": {"id": "user-1"}}):
            response = self.client.post(
                "/api/auth/signup",
                json={"email": "user@example.com", "password": "password123"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["confirmation_required"])

    def test_signout_returns_ok(self):
        response = self.client.post("/api/auth/signout")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])


    def test_docs_routes_render(self):
        for path in ("/docs", "/docs/api", "/docs/mcp"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"WebLoom", response.data)

    def test_public_api_requires_authentication(self):
        response = self.client.post("/v1/scrape", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()["ok"])

    def test_preview_html_rewrites_root_assets_into_project_scope(self):
        rendered = web_app._preview_rewrite_html(
            b'<html><head></head><body><img src="/assets/a.png"><script>fetch("/api/data")</script></body></html>',
            "job-123",
        ).decode("utf-8")
        self.assertIn('/preview/job-123/assets/a.png', rendered)
        self.assertIn('/preview/job-123/__origin/', rendered)
        self.assertIn('<base href="/preview/job-123/">', rendered)

    def test_new_capture_uses_inline_preview_instead_of_forced_redirect(self):
        response = self.client.get("/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="liveCaptureFrame"', response.data)
        self.assertNotIn(b'location.href = "/project/" + data.job.id', response.data)


    def test_new_capture_uses_real_streaming_capture_endpoint(self):
        response = self.client.get("/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"/api/clone/start"', response.data)
        self.assertIn(b'text/event-stream', response.data)
        self.assertIn(b'No fake timer', response.data)
        self.assertNotIn(b'setInterval(() =>', response.data)

    def test_completed_job_stream_returns_preview_event(self):
        identity = {
            "id": "user-1",
            "email": "owner@example.com",
            "is_anonymous": False,
            "role": "owner",
            "plan": "pro",
            "subscription_status": "active",
        }
        project = {
            "id": "job-1",
            "source_url": "https://example.com",
            "status": "done",
            "file_count": 12,
            "byte_count": 4096,
            "failed_count": 1,
            "metadata": {},
        }
        with (
            patch.object(web_app, "ensure_identity", return_value=identity),
            patch.object(web_app, "get_project", return_value=project),
        ):
            response = self.client.get("/api/jobs/job-1/stream")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        body = response.get_data(as_text=True)
        self.assertIn('"event": "complete"', body)
        self.assertIn('"/preview/job-1/"', body)

    def test_preview_context_accepts_running_project_for_progressive_preview(self):
        identity = {"id": "user-1"}
        project = {
            "id": "job-1",
            "source_url": "https://example.com",
            "status": "running",
            "metadata": {},
        }
        with web_app.app.test_request_context("/preview/job-1/"):
            with (
                patch.object(web_app, "current_identity", return_value=identity),
                patch.object(web_app, "get_project", return_value=project),
            ):
                ident, loaded_project, job = web_app._preview_project_context("job-1")
        self.assertEqual(ident["id"], "user-1")
        self.assertEqual(loaded_project["status"], "running")


if __name__ == "__main__":
    unittest.main()
