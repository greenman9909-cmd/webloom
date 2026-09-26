import unittest
from unittest.mock import patch

import web_app
import webloom_integrations as wi


class WebLoomFunctionRegressionTests(unittest.TestCase):
    def test_auth_prefers_http_only_access_cookie(self):
        with web_app.app.test_request_context("/", headers={"Cookie": "wl_access=cookie-token; wl_refresh=refresh-token"}):
            with (
                patch.object(wi, "auth_user_from_token", return_value={"id": "user-1", "email": ""}) as auth_user,
                patch.object(wi, "profile_for", return_value={"role": "user", "plan": "free", "is_anonymous": True}),
            ):
                ident = wi.current_identity(refresh=False)
        auth_user.assert_called_once_with("cookie-token")
        self.assertEqual(ident["id"], "user-1")
        self.assertTrue(ident["is_anonymous"])

    def test_new_capture_uses_inline_non_json_error_handling(self):
        client = web_app.app.test_client()
        response = client.get("/new")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn('id="captureError"', body)
        self.assertIn("await response.text()", body)
        self.assertNotIn("await response.json()", body)
        self.assertIn('placeholder="example.com"', body)

    def test_project_schema_tracks_anonymous_profiles(self):
        with open("supabase/schema.sql", "r", encoding="utf-8") as handle:
            schema = handle.read()
        self.assertIn("is_anonymous boolean not null default false", schema)
        self.assertIn("new.is_anonymous", schema)


if __name__ == "__main__":
    unittest.main()
