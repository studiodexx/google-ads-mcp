"""Verify local OAuth never persists or prints refresh tokens."""

import contextlib
import io
import os
import unittest
from unittest.mock import MagicMock, patch

from scripts import authorize_local


class LocalOAuthTest(unittest.TestCase):
    def test_secret_stays_in_environment(self):
        flow = MagicMock()
        flow.run_local_server.return_value.refresh_token = "dummy-refresh"
        output = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.stdin.isatty", return_value=True),
            patch(
                "getpass.getpass", side_effect=["dummy-client", "dummy-secret"]
            ),
            patch.object(
                authorize_local.InstalledAppFlow,
                "from_client_config",
                return_value=flow,
            ) as factory,
            contextlib.redirect_stdout(output),
        ):
            authorize_local.authorize()
            self.assertEqual(
                os.environ["GOOGLE_ADS_REFRESH_TOKEN"], "dummy-refresh"
            )
            self.assertNotIn("dummy-refresh", output.getvalue())
            self.assertNotIn("dummy-secret", output.getvalue())
            self.assertTrue(
                factory.call_args.kwargs["autogenerate_code_verifier"]
            )
            self.assertEqual(
                flow.run_local_server.call_args.kwargs["bind_addr"], "127.0.0.1"
            )

    def test_refuses_noninteractive_secret_input(self):
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(RuntimeError):
                authorize_local.authorize()
