"""Desktop launcher environment-loading tests."""

import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from scripts.desktop_server import load_session_credentials, SECRET_NAMES


class DesktopSessionTest(unittest.TestCase):
    def test_loads_session_without_printing_secrets(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "scripts.desktop_server.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="dummy\n"),
            ) as run,
        ):
            load_session_credentials()
            self.assertEqual(run.call_count, 3)
            self.assertTrue(all(os.environ[n] == "dummy" for n in SECRET_NAMES))

    def test_preserves_explicit_environment(self):
        with (
            patch.dict(
                os.environ, {n: "explicit" for n in SECRET_NAMES}, clear=True
            ),
            patch("scripts.desktop_server.subprocess.run") as run,
        ):
            load_session_credentials()
            run.assert_not_called()
