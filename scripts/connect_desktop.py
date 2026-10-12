"""Authorize the desktop clients for this macOS login session only."""

import argparse
import os
import subprocess
import sys

from scripts.authorize_local import authorize
from scripts.desktop_server import SECRET_NAMES


def clear():
    for name in SECRET_NAMES:
        subprocess.run(
            ["/bin/launchctl", "unsetenv", "ADS_MCP_" + name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    if args.clear:
        clear()
        print("Credenciais da sessão removidas. Reinicie os clientes MCP.")
        return 0
    try:
        authorize()
        clear()
        for name in SECRET_NAMES:
            subprocess.run(
                [
                    "/bin/launchctl",
                    "setenv",
                    "ADS_MCP_" + name,
                    os.environ[name],
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        print("Sessão pronta. Reinicie Codex e Claude Desktop.")
        print(
            "Credenciais em variáveis da sessão macOS; nenhum arquivo de segredo criado."
        )
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        clear()
        print("Autorização não concluída: " + type(exc).__name__)
        return 1
    finally:
        for name in SECRET_NAMES:
            os.environ.pop(name, None)


if __name__ == "__main__":
    sys.exit(main())
