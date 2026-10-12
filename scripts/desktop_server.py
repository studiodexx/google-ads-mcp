"""Start stdio MCP using OAuth variables from the current macOS login session."""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SECRET_NAMES = (
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
)


def load_session_credentials():
    for name in SECRET_NAMES:
        if not os.environ.get(name):
            result = subprocess.run(
                ["/bin/launchctl", "getenv", "ADS_MCP_" + name],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                os.environ[name] = result.stdout.strip()


def main():
    load_session_credentials()
    # Tool discovery works even before login. Writes still fail closed.
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from ads_mcp import utils
    from ads_mcp.write_guard import get_write_client

    # Both reading and writing use the same env-only OAuth credentials.
    utils._get_googleads_client = get_write_client
    from ads_mcp.coordinator import mcp
    from ads_mcp.resources import discovery, metrics, release_notes, segments

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
