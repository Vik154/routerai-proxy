"""CLI для работы с прокси: поиск провайдеров, проверка цен.

Использование:
    python -m proxy.cli providers anthropic/claude-sonnet-4.5
    python -m proxy.cli providers anthropic/claude-sonnet-4.5 --country ru
    python -m proxy.cli all-providers
    python -m proxy.cli all-providers --country ru
"""

import argparse
import sys
from typing import Optional

import httpx


def cmd_providers(base: str, model: str, country: Optional[str]) -> int:
    """Показать провайдеров для модели."""
    with httpx.Client(timeout=30.0) as client:
        params = {"model": model}
        if country:
            params["country"] = country

        try:
            resp = client.get(f"{base}/providers", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            return 1

        data = resp.json()

        print(f"\nМодель: {data['model']}")
        print(f"Провайдеров: {data['count']}\n")

        if not data["providers"]:
            print("Нет доступных провайдеров" + (f" для страны {country}" if country else ""))
            return 0

        print(f"{'Tag':<20} {'Name':<25} {'Prompt ₽/Mtok':<15} {'Completion ₽/Mtok':<20} {'Countries':<15}")
        print("=" * 95)

        for p in data["providers"]:
            prompt = f"{p['prompt_per_mtok_rub']:.1f}" if p.get("prompt_per_mtok_rub") else "N/A"
            completion = f"{p['completion_per_mtok_rub']:.1f}" if p.get("completion_per_mtok_rub") else "N/A"
            countries = ",".join(p.get("countries", [])) or "N/A"
            print(f"{p['tag']:<20} {p['name']:<25} {prompt:<15} {completion:<20} {countries:<15}")

        print("\nДля использования в config.yaml:")
        print("  provider:")
        print(f"    ignore: []  # или список тегов: {[p['tag'] for p in data['providers'][:2]]}")
        print(f"    order: []   # приоритет: {[p['tag'] for p in data['providers'][:3]]}")
        print(f"    country: '{country or 'null'}'")

        return 0


def cmd_all_providers(base: str, country: Optional[str]) -> int:
    """Показать всех провайдеров."""
    with httpx.Client(timeout=30.0) as client:
        params = {}
        if country:
            params["country"] = country

        try:
            resp = client.get(f"{base}/providers", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            return 1

        data = resp.json()

        print(f"\nВсего провайдеров: {data['count']}\n")

        print(f"{'Tag':<20} {'Name':<25} {'Sample ₽/Mtok':<15} {'Countries':<15} {'Models':<10}")
        print("=" * 85)

        for p in data["providers"]:
            sample = f"{p['sample_prompt_per_mtok_rub']:.1f}" if p.get("sample_prompt_per_mtok_rub") else "N/A"
            countries = ",".join(p.get("countries", [])) or "N/A"
            models_count = len(p.get("models", []))
            print(f"{p['tag']:<20} {p['name']:<25} {sample:<15} {countries:<15} {models_count:<10}")

        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLI для RouterAI Proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python -m proxy.cli providers anthropic/claude-sonnet-4.5
  python -m proxy.cli providers qwen/qwen-72b-chat --country ru
  python -m proxy.cli all-providers
  python -m proxy.cli all-providers --country ru
        """,
    )
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="Base URL прокси")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # providers <model>
    p_providers = subparsers.add_parser("providers", help="Показать провайдеров для модели")
    p_providers.add_argument("model", help="ID модели (например, anthropic/claude-sonnet-4.5)")
    p_providers.add_argument("--country", help="Фильтр по стране (например, ru)")

    # all-providers
    p_all = subparsers.add_parser("all-providers", help="Показать всех провайдеров")
    p_all.add_argument("--country", help="Фильтр по стране (например, ru)")

    args = parser.parse_args()

    if args.command == "providers":
        return cmd_providers(args.base, args.model, args.country)
    elif args.command == "all-providers":
        return cmd_all_providers(args.base, args.country)

    return 1


if __name__ == "__main__":
    sys.exit(main())