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

"""Write tools for Search campaigns.

This namespace is disabled by default; enable it in tools_config.yaml with
`write: true`. All guards live in ads_mcp/write_guard.py:
  * everything is created PAUSED (not configurable),
  * budgets and CPC bids are capped by MAX_DAILY_BUDGET,
  * enabling requires confirm="ATIVAR <id>",
  * writes are limited to test accounts unless explicitly allowed,
  * every attempt is written to the audit log.
"""

import re
from typing import Any, Dict, List, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

import ads_mcp.utils as utils
import ads_mcp.write_guard as guard
from ads_mcp.mcp_header_interceptor import MCPHeaderInterceptor

write_mcp = FastMCP("write")

_CREATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False
)
_UPDATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True
)

_HEADLINE_MAX_LEN = 30
_DESCRIPTION_MAX_LEN = 90
_PATH_MAX_LEN = 15


def _client_and_service(login_customer_id: str | int | None):
    client = guard.get_write_client(login_customer_id=login_customer_id)
    service = client.get_service(
        "GoogleAdsService", interceptors=[MCPHeaderInterceptor()]
    )
    return client, service


def _numeric_id(value: str | int, label: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"\d+", value):
        raise ToolError(f"{label} must be numeric, got '{value}'.")
    return value


def _mutate(client, service, customer_id: str, operations, dry_run: bool):
    """Sends the operations atomically and returns the created/updated names."""
    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = customer_id
    request.mutate_operations.extend(operations)
    request.validate_only = dry_run
    response = service.mutate(request=request, retry=None)
    if dry_run:
        return {"dry_run": True, "validated": True}
    names = []
    for r in response.mutate_operation_responses:
        which = type(r).pb(r).WhichOneof("response")
        names.append(getattr(r, which).resource_name)
    return {"dry_run": False, "resource_names": names}


@write_mcp.tool(annotations=_CREATE)
def create_search_campaign(
    customer_id: str | int,
    name: str,
    daily_budget: float,
    bidding_strategy: Literal["MANUAL_CPC", "MAXIMIZE_CLICKS"] = "MANUAL_CPC",
    contains_eu_political_advertising: bool = False,
    dry_run: bool = False,
    login_customer_id: str | int | None = None,
) -> Dict[str, Any]:
    """Creates a Search campaign and its own daily budget. The campaign is always created PAUSED.

    The budget and campaign are created atomically. No location or language
    targeting is added, so the campaign targets all locations and languages
    until targeting is configured.

    Args:
        customer_id: The id of the customer, digits only.
        name: Campaign name, must be unique in the account.
        daily_budget: Average daily budget in the account currency (not
          micros). Must not exceed MAX_DAILY_BUDGET.
        bidding_strategy: MANUAL_CPC or MAXIMIZE_CLICKS.
        contains_eu_political_advertising: Required declaration of whether the
          campaign has EU political advertising.
        dry_run: If true, the request is only validated by the API.
        login_customer_id: Optional manager customer ID.

    Returns:
        The resource names of the created budget and campaign.
    """
    params = {
        "name": name,
        "daily_budget": daily_budget,
        "bidding_strategy": bidding_strategy,
        "contains_eu_political_advertising": contains_eu_political_advertising,
        "dry_run": dry_run,
    }

    def operation(cid: str):
        client, service = _client_and_service(login_customer_id)
        budget_name = f"customers/{cid}/campaignBudgets/-1"

        budget_op = client.get_type("MutateOperation")
        budget = budget_op.campaign_budget_operation.create
        budget.resource_name = budget_name
        budget.name = f"{name} - budget"
        budget.amount_micros = guard.micros(daily_budget)
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget.explicitly_shared = False

        campaign_op = client.get_type("MutateOperation")
        campaign = campaign_op.campaign_operation.create
        campaign.name = name
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.advertising_channel_type = (
            client.enums.AdvertisingChannelTypeEnum.SEARCH
        )
        campaign.campaign_budget = budget_name
        if bidding_strategy == "MANUAL_CPC":
            campaign.manual_cpc = client.get_type("ManualCpc")
        else:
            campaign.target_spend = client.get_type("TargetSpend")
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = False
        campaign.network_settings.target_content_network = False
        campaign.network_settings.target_partner_search_network = False
        eu = client.enums.EuPoliticalAdvertisingStatusEnum
        campaign.contains_eu_political_advertising = (
            eu.CONTAINS_EU_POLITICAL_ADVERTISING
            if contains_eu_political_advertising
            else eu.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        )

        return _mutate(client, service, cid, [budget_op, campaign_op], dry_run)

    return guard.run_write(
        "create_search_campaign",
        customer_id,
        params,
        operation,
        login_customer_id=login_customer_id,
        pre_checks=lambda: guard.check_budget(daily_budget),
    )


@write_mcp.tool(annotations=_CREATE)
def create_ad_group(
    customer_id: str | int,
    campaign_id: str | int,
    name: str,
    cpc_bid: float | None = None,
    dry_run: bool = False,
    login_customer_id: str | int | None = None,
) -> Dict[str, Any]:
    """Creates a standard Search ad group in a campaign. The ad group is always created PAUSED.

    Args:
        customer_id: The id of the customer, digits only.
        campaign_id: The numeric id of the parent Search campaign.
        name: Ad group name, must be unique in the campaign.
        cpc_bid: Optional default max CPC in the account currency (not micros),
          used with MANUAL_CPC campaigns. Must not exceed MAX_DAILY_BUDGET.
        dry_run: If true, the request is only validated by the API.
        login_customer_id: Optional manager customer ID.

    Returns:
        The resource name of the created ad group.
    """
    params = {
        "campaign_id": campaign_id,
        "name": name,
        "cpc_bid": cpc_bid,
        "dry_run": dry_run,
    }

    def pre_checks():
        if cpc_bid is not None:
            guard.check_budget(cpc_bid, label="CPC bid")

    def operation(cid: str):
        client, service = _client_and_service(login_customer_id)
        op = client.get_type("MutateOperation")
        ad_group = op.ad_group_operation.create
        ad_group.name = name
        parent_id = _numeric_id(campaign_id, "campaign_id")
        ad_group.campaign = f"customers/{cid}/campaigns/{parent_id}"
        ad_group.status = client.enums.AdGroupStatusEnum.PAUSED
        ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        if cpc_bid is not None:
            ad_group.cpc_bid_micros = guard.micros(cpc_bid)
        return _mutate(client, service, cid, [op], dry_run)

    return guard.run_write(
        "create_ad_group",
        customer_id,
        params,
        operation,
        login_customer_id=login_customer_id,
        pre_checks=pre_checks,
    )


def _validate_rsa(
    headlines: List[str],
    descriptions: List[str],
    final_urls: List[str],
    path1: str | None,
    path2: str | None,
) -> None:
    errors = []
    if not 3 <= len(headlines) <= 15:
        errors.append("headlines: between 3 and 15 required")
    if not 2 <= len(descriptions) <= 4:
        errors.append("descriptions: between 2 and 4 required")
    errors += [
        f"headline too long ({len(h)}>{_HEADLINE_MAX_LEN}): '{h}'"
        for h in headlines
        if len(h) > _HEADLINE_MAX_LEN
    ]
    errors += [
        f"description too long ({len(d)}>{_DESCRIPTION_MAX_LEN}): '{d}'"
        for d in descriptions
        if len(d) > _DESCRIPTION_MAX_LEN
    ]
    if not final_urls:
        errors.append("final_urls: at least one URL required")
    errors += [
        f"final URL must start with http:// or https://: '{u}'"
        for u in final_urls
        if not re.match(r"https?://", u)
    ]
    for label, path in (("path1", path1), ("path2", path2)):
        if path and len(path) > _PATH_MAX_LEN:
            errors.append(f"{label} too long ({len(path)}>{_PATH_MAX_LEN})")
    if path2 and not path1:
        errors.append("path2 requires path1")
    if errors:
        raise ToolError("Invalid responsive search ad:\n" + "\n".join(errors))


@write_mcp.tool(annotations=_CREATE)
def create_responsive_search_ad(
    customer_id: str | int,
    ad_group_id: str | int,
    headlines: List[str],
    descriptions: List[str],
    final_urls: List[str],
    path1: str | None = None,
    path2: str | None = None,
    dry_run: bool = False,
    login_customer_id: str | int | None = None,
) -> Dict[str, Any]:
    """Creates a responsive search ad in an ad group. The ad is always created PAUSED.

    Args:
        customer_id: The id of the customer, digits only.
        ad_group_id: The numeric id of the parent ad group.
        headlines: 3 to 15 headlines, up to 30 characters each.
        descriptions: 2 to 4 descriptions, up to 90 characters each.
        final_urls: Landing page URLs (http:// or https://).
        path1: Optional display URL path, up to 15 characters.
        path2: Optional second display URL path, requires path1.
        dry_run: If true, the request is only validated by the API.
        login_customer_id: Optional manager customer ID.

    Returns:
        The resource name of the created ad group ad.
    """
    params = {
        "ad_group_id": ad_group_id,
        "headlines": headlines,
        "descriptions": descriptions,
        "final_urls": final_urls,
        "path1": path1,
        "path2": path2,
        "dry_run": dry_run,
    }

    def operation(cid: str):
        client, service = _client_and_service(login_customer_id)
        op = client.get_type("MutateOperation")
        ad_group_ad = op.ad_group_ad_operation.create
        parent_id = _numeric_id(ad_group_id, "ad_group_id")
        _validate_rsa(headlines, descriptions, final_urls, path1, path2)
        ad_group_ad.ad_group = f"customers/{cid}/adGroups/{parent_id}"
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED
        ad = ad_group_ad.ad
        ad.final_urls.extend(final_urls)
        rsa = ad.responsive_search_ad
        for text in headlines:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            rsa.headlines.append(asset)
        for text in descriptions:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            rsa.descriptions.append(asset)
        if path1:
            rsa.path1 = path1
        if path2:
            rsa.path2 = path2
        return _mutate(client, service, cid, [op], dry_run)

    return guard.run_write(
        "create_responsive_search_ad",
        customer_id,
        params,
        operation,
        login_customer_id=login_customer_id,
    )


def _campaign_budget(service, cid: str, campaign_id: str) -> Dict[str, Any]:
    campaign_id = _numeric_id(campaign_id, "campaign_id")
    query = (
        "SELECT campaign.id, campaign_budget.resource_name, "
        "campaign_budget.amount_micros, campaign_budget.reference_count "
        f"FROM campaign WHERE campaign.id = {campaign_id}"
    )
    rows = list(service.search(customer_id=cid, query=query))
    if not rows:
        raise ToolError(f"Campaign {campaign_id} not found in customer {cid}.")
    budget = rows[0].campaign_budget
    return {
        "resource_name": budget.resource_name,
        "amount_micros": budget.amount_micros,
        "reference_count": budget.reference_count,
    }


@write_mcp.tool(annotations=_UPDATE)
def set_status(
    customer_id: str | int,
    resource_type: Literal["campaign", "ad_group", "ad"],
    resource_id: str,
    status: Literal["PAUSED", "ENABLED"],
    confirm: str | None = None,
    dry_run: bool = False,
    login_customer_id: str | int | None = None,
) -> Dict[str, Any]:
    """Pauses or enables a campaign, ad group or ad.

    Pausing needs no confirmation. Enabling starts spending money, so it
    requires confirm="ATIVAR <resource_id>" exactly. NEVER fill `confirm` on
    your own: ask the user first and only pass it after they agree. Enabling a
    campaign is also refused if its daily budget exceeds MAX_DAILY_BUDGET.

    Args:
        customer_id: The id of the customer, digits only.
        resource_type: campaign, ad_group or ad.
        resource_id: Numeric id for campaign/ad_group. For ad, use
          "<ad_group_id>~<ad_id>".
        status: PAUSED or ENABLED.
        confirm: Required for ENABLED: "ATIVAR <resource_id>".
        dry_run: If true, the request is only validated by the API.
        login_customer_id: Optional manager customer ID.

    Returns:
        The resource name of the updated resource.
    """
    resource_id = str(resource_id).strip()
    params = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "confirm": confirm,
        "dry_run": dry_run,
    }

    def pre_checks():
        pattern = r"\d+~\d+" if resource_type == "ad" else r"\d+"
        if not re.fullmatch(pattern, resource_id):
            expected = (
                "<ad_group_id>~<ad_id>" if resource_type == "ad" else "digits"
            )
            raise ToolError(
                f"resource_id for {resource_type} must be {expected}."
            )
        if status == "ENABLED":
            guard.check_activation(resource_id, confirm)

    def operation(cid: str):
        client, service = _client_and_service(login_customer_id)
        op = client.get_type("MutateOperation")
        if status == "ENABLED" and resource_type != "campaign":
            group_id = resource_id.split("~")[0]
            rows = list(
                service.search(
                    customer_id=cid,
                    query=f"SELECT campaign.id FROM ad_group WHERE ad_group.id = {group_id}",
                )
            )
            if not rows:
                raise guard.WriteBlocked("Parent campaign not found.")
            budget = _campaign_budget(service, cid, str(rows[0].campaign.id))
            guard.check_budget(budget["amount_micros"] / 1_000_000)
        if resource_type == "campaign":
            if status == "ENABLED":
                budget = _campaign_budget(service, cid, resource_id)
                guard.check_budget(
                    budget["amount_micros"] / 1_000_000,
                    label=f"daily budget of campaign {resource_id}",
                )
            resource = op.campaign_operation.update
            resource.resource_name = f"customers/{cid}/campaigns/{resource_id}"
            resource.status = getattr(client.enums.CampaignStatusEnum, status)
            mask = op.campaign_operation.update_mask
        elif resource_type == "ad_group":
            resource = op.ad_group_operation.update
            resource.resource_name = f"customers/{cid}/adGroups/{resource_id}"
            resource.status = getattr(client.enums.AdGroupStatusEnum, status)
            mask = op.ad_group_operation.update_mask
        else:
            resource = op.ad_group_ad_operation.update
            resource.resource_name = f"customers/{cid}/adGroupAds/{resource_id}"
            resource.status = getattr(client.enums.AdGroupAdStatusEnum, status)
            mask = op.ad_group_ad_operation.update_mask
        mask.paths.append("status")
        return _mutate(client, service, cid, [op], dry_run)

    return guard.run_write(
        "set_status",
        customer_id,
        params,
        operation,
        login_customer_id=login_customer_id,
        pre_checks=pre_checks,
    )


@write_mcp.tool(annotations=_UPDATE)
def update_campaign_budget(
    customer_id: str | int,
    campaign_id: str | int,
    daily_budget: float,
    dry_run: bool = False,
    login_customer_id: str | int | None = None,
) -> Dict[str, Any]:
    """Changes the daily budget of a campaign. Refuses shared budgets.

    Args:
        customer_id: The id of the customer, digits only.
        campaign_id: The numeric id of the campaign.
        daily_budget: New average daily budget in the account currency (not
          micros). Must not exceed MAX_DAILY_BUDGET.
        dry_run: If true, the request is only validated by the API.
        login_customer_id: Optional manager customer ID.

    Returns:
        The budget resource name with the previous and new amounts.
    """
    params = {
        "campaign_id": campaign_id,
        "daily_budget": daily_budget,
        "dry_run": dry_run,
    }

    def operation(cid: str):
        client, service = _client_and_service(login_customer_id)
        budget = _campaign_budget(service, cid, campaign_id)
        if budget["reference_count"] > 1:
            raise guard.WriteBlocked(
                f"Budget {budget['resource_name']} is shared by "
                f"{budget['reference_count']} campaigns; changing it would "
                "affect all of them."
            )
        op = client.get_type("MutateOperation")
        update = op.campaign_budget_operation.update
        update.resource_name = budget["resource_name"]
        update.amount_micros = guard.micros(daily_budget)
        op.campaign_budget_operation.update_mask.paths.append("amount_micros")
        result = _mutate(client, service, cid, [op], dry_run)
        result["previous_daily_budget"] = budget["amount_micros"] / 1_000_000
        result["new_daily_budget"] = daily_budget
        return result

    return guard.run_write(
        "update_campaign_budget",
        customer_id,
        params,
        operation,
        login_customer_id=login_customer_id,
        pre_checks=lambda: guard.check_budget(daily_budget),
    )
