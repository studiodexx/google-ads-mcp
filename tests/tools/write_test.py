# Copyright 2026 studiodexx.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test cases for the write tools and their safety guards."""

import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.oauth2.credentials import Credentials

import ads_mcp.write_guard as guard
from ads_mcp.tools import write

CID = "1234567890"


def _real_client():
    """A real client (dummy credentials) so the built protos are validated."""
    return GoogleAdsClient(
        credentials=Credentials(token="test"),
        developer_token="test",
        use_proto_plus=True,
    )


class WriteTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = pathlib.Path(self.tmp.name) / "audit.jsonl"
        env = {
            guard.AUDIT_LOG_ENV: str(self.log_path),
            guard.MAX_DAILY_BUDGET_ENV: "50",
        }
        self.env = patch.dict(os.environ, env, clear=False)
        self.env.start()
        os.environ.pop(guard.REQUIRE_TEST_ACCOUNT_ENV, None)
        os.environ.pop(guard.ALLOWED_CUSTOMERS_ENV, None)

        self.client = _real_client()
        self.service = MagicMock()
        self.client.get_service = MagicMock(return_value=self.service)
        self.client_patch = patch(
            "ads_mcp.write_guard.get_write_client", return_value=self.client
        )
        self.client_patch.start()
        self.test_account_patch = patch(
            "ads_mcp.write_guard._is_test_account", return_value=True
        )
        self.is_test_account = self.test_account_patch.start()

    def tearDown(self):
        self.test_account_patch.stop()
        self.client_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def audit(self):
        if not self.log_path.exists():
            return []
        return [
            e
            for l in self.log_path.read_text().splitlines()
            if (e := json.loads(l))["outcome"] != "started"
        ]

    def sent_request(self):
        return self.service.mutate.call_args.kwargs["request"]

    def mock_mutate_response(self, *results):
        response = self.client.get_type("MutateGoogleAdsResponse")
        for field, name in results:
            r = self.client.get_type("MutateOperationResponse")
            getattr(r, field).resource_name = name
            response.mutate_operation_responses.append(r)
        self.service.mutate.return_value = response

    def mock_budget_row(self, amount_micros, reference_count=1):
        row = MagicMock()
        row.campaign_budget.resource_name = f"customers/{CID}/campaignBudgets/7"
        row.campaign_budget.amount_micros = amount_micros
        row.campaign_budget.reference_count = reference_count
        self.service.search.return_value = [row]


class TestCreateSearchCampaign(WriteTestBase):

    def test_creates_paused_campaign_with_budget(self):
        self.mock_mutate_response(
            ("campaign_budget_result", f"customers/{CID}/campaignBudgets/1"),
            ("campaign_result", f"customers/{CID}/campaigns/2"),
        )
        result = write.create_search_campaign(CID, "Teste", 20.5)

        ops = self.sent_request().mutate_operations
        budget = ops[0].campaign_budget_operation.create
        campaign = ops[1].campaign_operation.create
        self.assertEqual(budget.amount_micros, 20_500_000)
        self.assertEqual(campaign.status.name, "PAUSED")
        self.assertEqual(campaign.advertising_channel_type.name, "SEARCH")
        self.assertEqual(campaign.campaign_budget, budget.resource_name)
        self.assertEqual(
            type(campaign).pb(campaign).WhichOneof("campaign_bidding_strategy"),
            "manual_cpc",
        )
        self.assertFalse(self.sent_request().validate_only)
        self.assertEqual(
            result["resource_names"][1], f"customers/{CID}/campaigns/2"
        )
        [entry] = self.audit()
        self.assertEqual(entry["outcome"], "ok")
        self.assertEqual(entry["tool"], "create_search_campaign")

    def test_maximize_clicks(self):
        self.mock_mutate_response()
        write.create_search_campaign(
            CID, "Teste", 10, bidding_strategy="MAXIMIZE_CLICKS"
        )
        campaign = self.sent_request().mutate_operations[1].campaign_operation
        self.assertEqual(
            type(campaign.create)
            .pb(campaign.create)
            .WhichOneof("campaign_bidding_strategy"),
            "target_spend",
        )

    def test_budget_above_cap_is_blocked_and_logged(self):
        with self.assertRaises(guard.WriteBlocked):
            write.create_search_campaign(CID, "Teste", 50.01)
        self.service.mutate.assert_not_called()
        [entry] = self.audit()
        self.assertEqual(entry["outcome"], "blocked")
        self.assertIn("MAX_DAILY_BUDGET", entry["reason"])

    def test_missing_cap_blocks(self):
        os.environ.pop(guard.MAX_DAILY_BUDGET_ENV)
        with self.assertRaises(guard.WriteBlocked):
            write.create_search_campaign(CID, "Teste", 1)
        self.service.mutate.assert_not_called()

    def test_dry_run_sets_validate_only(self):
        self.mock_mutate_response()
        result = write.create_search_campaign(CID, "Teste", 5, dry_run=True)
        self.assertTrue(self.sent_request().validate_only)
        self.assertTrue(result["dry_run"])

    def test_non_test_account_blocked(self):
        self.is_test_account.return_value = False
        with self.assertRaises(guard.WriteBlocked):
            write.create_search_campaign(CID, "Teste", 5)
        self.service.mutate.assert_not_called()
        self.assertEqual(self.audit()[0]["outcome"], "blocked")

    def test_non_test_account_allowed_when_opted_out(self):
        os.environ[guard.REQUIRE_TEST_ACCOUNT_ENV] = "false"
        self.is_test_account.return_value = False
        self.mock_mutate_response()
        write.create_search_campaign(CID, "Teste", 5)
        self.service.mutate.assert_called_once()
        self.is_test_account.assert_not_called()

    def test_customer_allowlist(self):
        os.environ[guard.ALLOWED_CUSTOMERS_ENV] = "111-222-3333"
        with self.assertRaises(guard.WriteBlocked):
            write.create_search_campaign(CID, "Teste", 5)
        os.environ[guard.ALLOWED_CUSTOMERS_ENV] = "123-456-7890, 999"
        self.mock_mutate_response()
        write.create_search_campaign("123-456-7890", "Teste", 5)
        self.service.mutate.assert_called_once()


class TestCreateAdGroup(WriteTestBase):

    def test_creates_paused_ad_group(self):
        self.mock_mutate_response(
            ("ad_group_result", f"customers/{CID}/adGroups/3")
        )
        write.create_ad_group(CID, "22", "Grupo", cpc_bid=1.25)
        ad_group = self.sent_request().mutate_operations[0].ad_group_operation
        self.assertEqual(ad_group.create.status.name, "PAUSED")
        self.assertEqual(ad_group.create.type_.name, "SEARCH_STANDARD")
        self.assertEqual(
            ad_group.create.campaign, f"customers/{CID}/campaigns/22"
        )
        self.assertEqual(ad_group.create.cpc_bid_micros, 1_250_000)

    def test_cpc_above_cap_blocked(self):
        with self.assertRaises(guard.WriteBlocked):
            write.create_ad_group(CID, "22", "Grupo", cpc_bid=51)
        self.service.mutate.assert_not_called()

    def test_rejects_non_numeric_campaign_id(self):
        with self.assertRaises(ToolError):
            write.create_ad_group(CID, "22 OR 1=1", "Grupo")


class TestCreateResponsiveSearchAd(WriteTestBase):

    HEADLINES = ["Titulo um", "Titulo dois", "Titulo tres"]
    DESCRIPTIONS = ["Descricao um.", "Descricao dois."]

    def test_creates_paused_ad(self):
        self.mock_mutate_response()
        write.create_responsive_search_ad(
            CID,
            "33",
            self.HEADLINES,
            self.DESCRIPTIONS,
            ["https://example.com"],
            path1="ofertas",
        )
        aga = self.sent_request().mutate_operations[0].ad_group_ad_operation
        self.assertEqual(aga.create.status.name, "PAUSED")
        rsa = aga.create.ad.responsive_search_ad
        self.assertEqual([h.text for h in rsa.headlines], self.HEADLINES)
        self.assertEqual(len(rsa.descriptions), 2)
        self.assertEqual(rsa.path1, "ofertas")

    def test_validates_limits(self):
        cases = [
            (self.HEADLINES[:2], self.DESCRIPTIONS, ["https://a.com"]),
            (self.HEADLINES, self.DESCRIPTIONS[:1], ["https://a.com"]),
            (self.HEADLINES + ["x" * 31], self.DESCRIPTIONS, ["https://a.com"]),
            (self.HEADLINES, self.DESCRIPTIONS + ["x" * 91], ["https://a.com"]),
            (self.HEADLINES, self.DESCRIPTIONS, ["ftp://a.com"]),
            (self.HEADLINES, self.DESCRIPTIONS, []),
        ]
        for headlines, descriptions, urls in cases:
            with self.subTest(headlines=headlines, descriptions=descriptions):
                with self.assertRaises(ToolError):
                    write.create_responsive_search_ad(
                        CID, "33", headlines, descriptions, urls
                    )
        self.service.mutate.assert_not_called()


class TestSetStatus(WriteTestBase):

    def test_pause_needs_no_confirmation(self):
        self.mock_mutate_response()
        write.set_status(CID, "campaign", "2", "PAUSED")
        op = self.sent_request().mutate_operations[0].campaign_operation
        self.assertEqual(op.update.status.name, "PAUSED")
        self.assertEqual(list(op.update_mask.paths), ["status"])

    def test_enable_without_confirmation_blocked(self):
        for confirm in (None, "sim", "ATIVAR 3", "ativar 2"):
            with self.subTest(confirm=confirm):
                with self.assertRaises(guard.WriteBlocked):
                    write.set_status(CID, "campaign", "2", "ENABLED", confirm)
        self.service.mutate.assert_not_called()
        self.assertTrue(all(e["outcome"] == "blocked" for e in self.audit()))

    def test_enable_campaign_with_confirmation(self):
        self.mock_budget_row(10_000_000)
        self.mock_mutate_response()
        write.set_status(CID, "campaign", "2", "ENABLED", confirm="ATIVAR 2")
        op = self.sent_request().mutate_operations[0].campaign_operation
        self.assertEqual(op.update.status.name, "ENABLED")

    def test_enable_campaign_over_cap_blocked(self):
        self.mock_budget_row(80_000_000)
        with self.assertRaises(guard.WriteBlocked):
            write.set_status(
                CID, "campaign", "2", "ENABLED", confirm="ATIVAR 2"
            )
        self.service.mutate.assert_not_called()
        self.assertEqual(self.audit()[0]["outcome"], "blocked")

    def test_enable_ad(self):
        self.mock_budget_row(10_000_000)
        self.service.search.return_value[0].campaign.id = 2
        self.mock_mutate_response()
        write.set_status(CID, "ad", "33~44", "ENABLED", confirm="ATIVAR 33~44")
        op = self.sent_request().mutate_operations[0].ad_group_ad_operation
        self.assertEqual(
            op.update.resource_name, f"customers/{CID}/adGroupAds/33~44"
        )
        self.assertEqual(op.update.status.name, "ENABLED")

    def test_invalid_ad_id(self):
        with self.assertRaises(ToolError):
            write.set_status(CID, "ad", "44", "PAUSED")


class TestUpdateCampaignBudget(WriteTestBase):

    def test_updates_amount(self):
        self.mock_budget_row(10_000_000)
        self.mock_mutate_response()
        result = write.update_campaign_budget(CID, "2", 30)
        op = self.sent_request().mutate_operations[0].campaign_budget_operation
        self.assertEqual(op.update.amount_micros, 30_000_000)
        self.assertEqual(list(op.update_mask.paths), ["amount_micros"])
        self.assertEqual(result["previous_daily_budget"], 10)
        self.assertEqual(self.audit()[0]["result"]["new_daily_budget"], 30)

    def test_above_cap_blocked(self):
        with self.assertRaises(guard.WriteBlocked):
            write.update_campaign_budget(CID, "2", 100)
        self.service.search.assert_not_called()

    def test_shared_budget_blocked(self):
        self.mock_budget_row(10_000_000, reference_count=2)
        with self.assertRaises(guard.WriteBlocked):
            write.update_campaign_budget(CID, "2", 30)
        self.service.mutate.assert_not_called()


class TestApiErrorsAreLogged(WriteTestBase):

    def test_api_exception_logged_as_error(self):
        from google.ads.googleads.errors import GoogleAdsException

        failure = MagicMock()
        failure.errors = [MagicMock(message="boom")]
        self.service.mutate.side_effect = GoogleAdsException(
            None, None, failure, "req-1"
        )
        with self.assertRaises(ToolError):
            write.create_search_campaign(CID, "Teste", 5)
        [entry] = self.audit()
        self.assertEqual(entry["outcome"], "error")
        self.assertEqual(entry["request_id"], "req-1")
        self.assertEqual(entry["errors"], ["boom"])


class TestIsTestAccount(unittest.TestCase):

    @patch("ads_mcp.write_guard.get_write_client")
    def test_checks_each_time(self, mock_get_service):
        row = MagicMock()
        row.customer.test_account = True
        mock_get_service.return_value.get_service.return_value.search.return_value = [
            row
        ]
        self.assertTrue(guard._is_test_account(CID, None))
        self.assertTrue(guard._is_test_account(CID, None))
        self.assertEqual(
            mock_get_service.return_value.get_service.return_value.search.call_count,
            2,
        )

    @patch("ads_mcp.write_guard.get_write_client")
    def test_real_account(self, mock_get_service):
        row = MagicMock()
        row.customer.test_account = False
        mock_get_service.return_value.get_service.return_value.search.return_value = [
            row
        ]
        self.assertFalse(guard._is_test_account(CID, None))


if __name__ == "__main__":
    unittest.main()


class TestAdditionalGuards(WriteTestBase):
    def test_nonfinite_cap_and_amount(self):
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with (
                self.subTest(cap=value),
                patch.dict(os.environ, {"MAX_DAILY_BUDGET": value}),
            ):
                with self.assertRaises(guard.WriteBlocked):
                    write.create_search_campaign(CID, "Test", 5)
        for amount in (float("nan"), float("inf"), -1, 0, 0.0000001):
            with (
                self.subTest(amount=amount),
                self.assertRaises(guard.WriteBlocked),
            ):
                write.create_search_campaign(CID, "Test", amount)
        self.service.mutate.assert_not_called()

    def test_missing_cap_blocks_non_budget_write(self):
        os.environ.pop("MAX_DAILY_BUDGET")
        with self.assertRaises(guard.WriteBlocked):
            write.create_ad_group(CID, "2", "Group")
        self.service.mutate.assert_not_called()

    def test_unwritable_audit_prevents_mutation(self):
        with patch.object(
            guard, "write_audit", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                write.create_search_campaign(CID, "Test", 5)
        self.service.mutate.assert_not_called()

    def test_started_record_precedes_api(self):
        def mutate(**kwargs):
            entries = [
                json.loads(line)
                for line in self.log_path.read_text().splitlines()
            ]
            self.assertEqual(entries[-1]["outcome"], "started")
            return self.client.get_type("MutateGoogleAdsResponse")

        self.service.mutate.side_effect = mutate
        write.create_search_campaign(CID, "Test", 5)

    def test_child_activation_checks_budget(self):
        self.mock_budget_row(80_000_000)
        self.service.search.return_value[0].campaign.id = 2
        with self.assertRaises(guard.WriteBlocked):
            write.set_status(CID, "ad", "33~44", "ENABLED", "ATIVAR 33~44")
        self.service.mutate.assert_not_called()


class TestEnvironmentCredentials(unittest.TestCase):
    def test_no_adc_fallback(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("google.auth.default") as adc,
        ):
            with self.assertRaises(guard.WriteBlocked):
                guard.get_write_client()
            adc.assert_not_called()


class TestWriteMounting(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_opt_in(self):
        from fastmcp import FastMCP
        from ads_mcp.config import ToolsConfig
        from ads_mcp.coordinator import initialize_and_mount_tools

        for enabled in (False, True):
            with patch(
                "ads_mcp.config.ToolsConfig.load",
                return_value=ToolsConfig({"namespaces": {"write": enabled}}),
            ):
                server = FastMCP("test")
                initialize_and_mount_tools(server)
                names = [t.name for t in await server.list_tools()]
                self.assertEqual(
                    len([n for n in names if n.startswith("write_")]),
                    5 if enabled else 0,
                )
        self.assertFalse(ToolsConfig().is_namespace_enabled("write"))
