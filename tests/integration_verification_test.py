"""Regression coverage for the integration verification GAQL."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from scripts.test_write_account import verify_existing


class VerificationTest(unittest.TestCase):
    def test_budget_queried_separately_and_no_writes(self):
        service = MagicMock()
        paused = NS(status=NS(name="PAUSED"))
        service.search.side_effect = [
            [NS(campaign=paused, ad_group=paused, ad_group_ad=paused)],
            [NS(campaign_budget=NS(amount_micros=500000))],
        ]
        with (
            patch("ads_mcp.write_guard._is_test_account", return_value=True),
            patch("ads_mcp.write_guard.get_write_client") as factory,
        ):
            factory.return_value.get_service.return_value = service
            verify_existing("1234567890", "123", 0.5)
        queries = [
            call.kwargs["query"] for call in service.search.call_args_list
        ]
        self.assertNotIn("campaign_budget", queries[0])
        self.assertIn("FROM campaign ", queries[1])
        service.mutate.assert_not_called()

    def test_wrong_budget_fails(self):
        service = MagicMock()
        paused = NS(status=NS(name="PAUSED"))
        service.search.side_effect = [
            [NS(campaign=paused, ad_group=paused, ad_group_ad=paused)],
            [NS(campaign_budget=NS(amount_micros=1000000))],
        ]
        with (
            patch("ads_mcp.write_guard._is_test_account", return_value=True),
            patch("ads_mcp.write_guard.get_write_client") as factory,
        ):
            factory.return_value.get_service.return_value = service
            with self.assertRaises(RuntimeError):
                verify_existing("1234567890", "123", 0.5)
