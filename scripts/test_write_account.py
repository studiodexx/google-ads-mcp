"""Opt-in integration test. Creates PAUSED resources only in a test account."""

import os
import uuid

from ads_mcp import write_guard as guard
from ads_mcp.tools import write


def main():
    cid = os.environ["GOOGLE_ADS_TEST_CUSTOMER_ID"]
    # Never permit this script to target production.
    os.environ[guard.REQUIRE_TEST_ACCOUNT_ENV] = "true"
    os.environ[guard.ALLOWED_CUSTOMERS_ENV] = cid
    amount = min(1.0, guard.get_max_daily_budget())
    name = "MCP integration " + uuid.uuid4().hex[:12]
    write.create_search_campaign(cid, name, amount, dry_run=True)
    campaign = write.create_search_campaign(cid, name, amount)[
        "resource_names"
    ][-1]
    campaign_id = campaign.rsplit("/", 1)[1]
    group = write.create_ad_group(cid, campaign_id, name, cpc_bid=amount)[
        "resource_names"
    ][0]
    group_id = group.rsplit("/", 1)[1]
    ad = write.create_responsive_search_ad(
        cid,
        group_id,
        ["Teste de integracao", "Campanha de teste", "Anuncio de teste"],
        [
            "Este anuncio e apenas um teste.",
            "Validacao da integracao Google Ads.",
        ],
        ["https://example.com"],
    )["resource_names"][0]
    write.update_campaign_budget(cid, campaign_id, amount / 2)
    # Validate activation, without actually enabling anything.
    for kind, rid in (
        ("campaign", campaign_id),
        ("ad_group", group_id),
        ("ad", ad.rsplit("/", 1)[1]),
    ):
        write.set_status(
            cid,
            kind,
            rid,
            "ENABLED",
            confirm=guard.activation_phrase(rid),
            dry_run=True,
        )
        write.set_status(cid, kind, rid, "PAUSED")
    service = guard.get_write_client().get_service("GoogleAdsService")
    rows = list(
        service.search(
            customer_id=cid,
            query=(
                "SELECT campaign.status, ad_group.status, ad_group_ad.status, "
                "campaign_budget.amount_micros FROM ad_group_ad "
                f"WHERE campaign.id = {campaign_id}"
            ),
        )
    )
    assert len(rows) == 1, "Expected one created ad"
    row = rows[0]
    assert all(
        x.status.name == "PAUSED"
        for x in (row.campaign, row.ad_group, row.ad_group_ad)
    )
    assert row.campaign_budget.amount_micros == guard.micros(amount / 2)
    print("Verified PAUSED resources:", campaign, group, ad)


if __name__ == "__main__":
    main()
