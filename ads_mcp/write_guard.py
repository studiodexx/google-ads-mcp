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

"""Safety guards and audit logging for the write tools.

Every write tool goes through `run_write`, which:
  1. checks the customer allowlist (GOOGLE_ADS_WRITE_ALLOWED_CUSTOMERS),
  2. checks that the account is a Google Ads test account, unless
     GOOGLE_ADS_WRITE_REQUIRE_TEST_ACCOUNT is explicitly set to "false",
  3. runs the operation,
  4. appends one JSON line to the audit log for every attempt, including
     attempts blocked by a guard and attempts that failed at the API.

Budget amounts are always checked against MAX_DAILY_BUDGET (in the account
currency, not micros). If MAX_DAILY_BUDGET is unset or invalid, every write operation is refused.
"""

import datetime
import json
import math
import uuid
import os
import pathlib
import threading
from typing import Any, Callable, Dict

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

import ads_mcp.utils as utils

MAX_DAILY_BUDGET_ENV = "MAX_DAILY_BUDGET"
REQUIRE_TEST_ACCOUNT_ENV = "GOOGLE_ADS_WRITE_REQUIRE_TEST_ACCOUNT"
ALLOWED_CUSTOMERS_ENV = "GOOGLE_ADS_WRITE_ALLOWED_CUSTOMERS"
AUDIT_LOG_ENV = "GOOGLE_ADS_WRITE_AUDIT_LOG"
DEFAULT_AUDIT_LOG = "~/.google_ads_mcp/write_audit.jsonl"

_audit_lock = threading.Lock()


class WriteBlocked(ToolError):
    """Raised when a safety guard refuses a write operation."""


def get_write_client(login_customer_id=None):
    """Load OAuth credentials only from environment; never fall back to ADC."""
    from google.ads.googleads.client import GoogleAdsClient
    from google.oauth2.credentials import Credentials

    names = (
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_DEVELOPER_TOKEN",
    )
    if any(not os.environ.get(name) for name in names):
        raise WriteBlocked(
            "Write credentials must be configured in environment variables: "
            + ", ".join(names)
        )
    return GoogleAdsClient(
        credentials=Credentials(
            token=None,
            client_id=os.environ[names[0]],
            client_secret=os.environ[names[1]],
            refresh_token=os.environ[names[2]],
            token_uri="https://oauth2.googleapis.com/token",
        ),
        developer_token=os.environ[names[3]],
        use_proto_plus=True,
        login_customer_id=utils._get_login_customer_id(login_customer_id),
    )


def micros(amount: float) -> int:
    """Converts an amount in account currency to micros."""
    return int(round(amount * 1_000_000))


def get_max_daily_budget() -> float:
    """Returns MAX_DAILY_BUDGET, or raises if it is not a positive number."""
    raw = os.environ.get(MAX_DAILY_BUDGET_ENV)
    if not raw:
        raise WriteBlocked(
            f"{MAX_DAILY_BUDGET_ENV} is not set. Set it (account currency, "
            "e.g. 50 for 50.00/day) to enable budget-related write tools."
        )
    try:
        value = float(raw)
    except ValueError:
        raise WriteBlocked(f"{MAX_DAILY_BUDGET_ENV}='{raw}' is not a number.")
    if not math.isfinite(value) or value <= 0:
        raise WriteBlocked(f"{MAX_DAILY_BUDGET_ENV} must be greater than 0.")
    return value


def check_budget(amount: float, label: str = "daily budget") -> None:
    """Raises unless 0 < amount <= MAX_DAILY_BUDGET."""
    cap = get_max_daily_budget()
    if not math.isfinite(amount) or amount <= 0 or micros(amount) < 1:
        raise WriteBlocked(f"The {label} must be greater than 0.")
    if amount > cap or micros(amount) > cap * 1_000_000:
        raise WriteBlocked(
            f"The {label} {amount} exceeds {MAX_DAILY_BUDGET_ENV}={cap}."
        )


def activation_phrase(resource_id: str) -> str:
    """Returns the exact text the caller must pass to enable a resource."""
    return f"ATIVAR {resource_id}"


def check_activation(resource_id: str, confirm: str | None) -> None:
    """Raises unless `confirm` is exactly the activation phrase."""
    expected = activation_phrase(resource_id)
    if confirm != expected:
        raise WriteBlocked(
            "Enabling requires explicit confirmation. Ask the user, then call "
            f"again with confirm='{expected}'."
        )


def _require_test_account() -> bool:
    return os.environ.get(REQUIRE_TEST_ACCOUNT_ENV, "true").lower() not in (
        "false",
        "0",
        "no",
    )


def _check_allowed_customer(customer_id: str) -> None:
    raw = os.environ.get(ALLOWED_CUSTOMERS_ENV)
    if not raw:
        return
    allowed = {utils.clean_customer_id(c) for c in raw.split(",") if c.strip()}
    if customer_id not in allowed:
        raise WriteBlocked(
            f"Customer {customer_id} is not in {ALLOWED_CUSTOMERS_ENV}."
        )


def _is_test_account(
    customer_id: str, login_customer_id: str | int | None
) -> bool:
    service = get_write_client(login_customer_id).get_service(
        "GoogleAdsService"
    )
    rows = service.search(
        customer_id=customer_id,
        query="SELECT customer.test_account FROM customer LIMIT 1",
    )
    return any(row.customer.test_account is True for row in rows)


def _check_test_account(
    customer_id: str, login_customer_id: str | int | None
) -> None:
    if not _require_test_account():
        return
    if not _is_test_account(customer_id, login_customer_id):
        raise WriteBlocked(
            f"Customer {customer_id} is not a Google Ads test account. Writes "
            f"to real accounts require {REQUIRE_TEST_ACCOUNT_ENV}=false."
        )


def audit_log_path() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(AUDIT_LOG_ENV, DEFAULT_AUDIT_LOG)
    ).expanduser()


def write_audit(entry: Dict[str, Any]) -> None:
    """Appends one JSON line to the audit log and mirrors it to the logger."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **entry,
    }
    line = json.dumps(entry, ensure_ascii=False, default=str)
    utils.logger.info(f"ads_mcp.write {line}")
    path = audit_log_path()
    with _audit_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(
            path,
            "a",
            encoding="utf-8",
            opener=lambda name, flags: os.open(name, flags, 0o600),
        ) as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())


def run_write(
    tool: str,
    customer_id: str | int,
    params: Dict[str, Any],
    operation: Callable[[str], Any],
    login_customer_id: str | int | None = None,
    pre_checks: Callable[[], None] | None = None,
) -> Any:
    """Runs a write operation behind the guards and audits the outcome.

    Args:
        tool: Name of the tool, recorded in the audit log.
        customer_id: Target customer ID.
        params: Tool parameters, recorded in the audit log.
        operation: Callable receiving the cleaned customer ID; performs the
          mutate and returns a JSON-serializable result.
        login_customer_id: Optional manager account for the API header.
        pre_checks: Tool-specific guards (budget cap, confirmation) that run
          before the account checks.
    """
    customer_id = utils.clean_customer_id(customer_id)
    entry: Dict[str, Any] = {
        "operation_id": uuid.uuid4().hex,
        "tool": tool,
        "customer_id": customer_id,
        "login_customer_id": login_customer_id,
        "params": params,
    }
    write_audit({**entry, "outcome": "started"})
    try:
        get_max_daily_budget()
        if pre_checks:
            pre_checks()
        _check_allowed_customer(customer_id)
        _check_test_account(customer_id, login_customer_id)
    except WriteBlocked as ex:
        write_audit({**entry, "outcome": "blocked", "reason": str(ex)})
        raise
    except GoogleAdsException as ex:
        write_audit(
            {
                **entry,
                "outcome": "blocked",
                "reason": "test-account check failed",
                "request_id": ex.request_id,
                "errors": [e.message for e in ex.failure.errors],
            }
        )
        raise _to_tool_error(ex)

    except Exception as ex:
        write_audit(
            {**entry, "outcome": "error", "error_type": type(ex).__name__}
        )
        raise

    try:
        result = operation(customer_id)
    except WriteBlocked as ex:
        write_audit({**entry, "outcome": "blocked", "reason": str(ex)})
        raise
    except GoogleAdsException as ex:
        write_audit(
            {
                **entry,
                "outcome": "error",
                "request_id": ex.request_id,
                "errors": [e.message for e in ex.failure.errors],
            }
        )
        raise _to_tool_error(ex)
    except Exception as ex:
        write_audit(
            {**entry, "outcome": "error", "error_type": type(ex).__name__}
        )
        raise

    write_audit({**entry, "outcome": "ok", "result": result})
    return result


def _to_tool_error(ex: GoogleAdsException) -> ToolError:
    msgs = [f"Google Ads API Error: {e.message}" for e in ex.failure.errors]
    return ToolError(f"Request ID: {ex.request_id}\n" + "\n".join(msgs))
