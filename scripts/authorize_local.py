"""Interactive OAuth bootstrap; credentials stay in process memory/environment."""

import argparse
import getpass
import logging
import os
import re
import sys
import warnings

from google_auth_oauthlib.flow import InstalledAppFlow


def authorize():
    if not sys.stdin.isatty():
        raise RuntimeError("Execute em um terminal interativo.")
    # Never fall back to an echoed secret prompt.
    warnings.simplefilter("error", getpass.GetPassWarning)
    for name, label in (
        ("GOOGLE_ADS_CLIENT_ID", "ID do cliente OAuth"),
        ("GOOGLE_ADS_CLIENT_SECRET", "Segredo do cliente OAuth"),
    ):
        if not os.environ.get(name):
            os.environ[name] = getpass.getpass(
                label + " (entrada oculta): "
            ).strip()
        if not os.environ[name]:
            raise ValueError("Credencial vazia.")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/adwords"],
        autogenerate_code_verifier=True,
    )
    print(
        "O navegador abrirá o consentimento Google para acesso ao Google Ads."
    )
    print("Confira a conta e as permissões antes de autorizar.")
    # Suppress HTTP/debug logs which can contain authorization codes.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1",
            bind_addr="127.0.0.1",
            port=0,
            timeout_seconds=300,
            open_browser=True,
            authorization_prompt_message="Se não abrir, acesse: {url}",
            success_message="Autorização recebida. Volte ao terminal.",
            access_type="offline",
            prompt="consent",
        )
    finally:
        logging.disable(previous)
    if not credentials.refresh_token:
        raise RuntimeError("Google não retornou refresh token.")
    os.environ["GOOGLE_ADS_REFRESH_TOKEN"] = credentials.refresh_token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-campaign", help="Read-only verification; no new resources"
    )
    parser.add_argument("--expected-budget", type=float, default=0.5)
    args = parser.parse_args()
    try:
        authorize()
        print("Autorização concluída; nenhum segredo foi salvo ou impresso.")
        cid = input("ID da conta CLIENTE DE TESTE Google Ads: ").strip()
        if not re.fullmatch(r"[0-9]{3}-?[0-9]{3}-?[0-9]{4}", cid):
            raise ValueError("ID de conta inválido.")
        os.environ["GOOGLE_ADS_TEST_CUSTOMER_ID"] = cid.replace("-", "")
        manager = input(
            "ID do administrador de teste (Enter se não necessário): "
        ).strip()
        if manager:
            if not re.fullmatch(r"[0-9]{3}-?[0-9]{3}-?[0-9]{4}", manager):
                raise ValueError("ID do administrador inválido.")
            os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = manager.replace(
                "-", ""
            )
        os.environ["MAX_DAILY_BUDGET"] = input(
            "Teto de orçamento diário na moeda da conta (ex.: 10): "
        ).strip()
        from scripts.test_write_account import main as test_account

        if args.verify_campaign:
            from scripts.test_write_account import verify_existing

            verify_existing(
                os.environ["GOOGLE_ADS_TEST_CUSTOMER_ID"],
                args.verify_campaign,
                args.expected_budget,
            )
        else:
            test_account()
        print("Integração validada. Recursos permanecem PAUSED.")
    except (Exception, KeyboardInterrupt) as exc:
        # No traceback or provider messages: they may contain secrets.
        print(
            "Não concluído ("
            + type(exc).__name__
            + "). Confira a configuração."
        )
        return 1
    finally:
        for name in (
            "GOOGLE_ADS_CLIENT_ID",
            "GOOGLE_ADS_CLIENT_SECRET",
            "GOOGLE_ADS_REFRESH_TOKEN",
        ):
            os.environ.pop(name, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
