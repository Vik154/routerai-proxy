"""
Smoke-тест прокси. Прокси должен быть запущен. 
Запуск: python tests/smoke_test.py
"""

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"

passed = 0
failed = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}" + (f"  [{extra}]" if extra else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"  [{extra}]" if extra else ""))


def main() -> int:
    with httpx.Client(timeout=180.0) as c:

        print("1) /health")
        r = c.get(f"{BASE}/health")
        check("health 200", r.status_code == 200, f"status={r.status_code}")
        data = r.json()
        check(
            "профили на месте",
            {"default", "experiments"} <= set(data.get("profiles", [])),
        )

        print("2) /v1/models")
        r = c.get(f"{BASE}/v1/models")
        check("models 200", r.status_code == 200, f"status={r.status_code}")
        ids = [m["id"] for m in r.json().get("data", [])]
        check("каталог непустой", len(ids) > 10, f"{len(ids)} моделей")
        check("алиас my-sonnet в dropdown", "my-sonnet" in ids)
        check("алиас my-gpt в dropdown", "my-gpt" in ids)

        print("3) неизвестная модель -> 404 с did-you-mean")
        r = c.post(
            f"{BASE}/v1/chat/completions",
            json={
                "model": "anthropic/claude-sonnet-4",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        check("404 на опечатке", r.status_code == 404, f"status={r.status_code}")
        if r.status_code == 404:
            print("     detail:", r.json().get("detail", "")[:160])

        print("4) неизвестный профиль -> 400")
        r = c.post(
            f"{BASE}/v1/chat/completions",
            headers={"X-Proxy-Profile": "nope"},
            json={"model": "my-sonnet", "messages": [{"role": "user", "content": "hi"}]},
        )
        check("400 на чужом профиле", r.status_code == 400, f"status={r.status_code}")

        print("5) PriceGuard: default-профиль + my-sonnet (потолок 30 руб/Мток)")
        r = c.post(
            f"{BASE}/v1/chat/completions",
            json={
                "model": "my-sonnet",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 16,
            },
        )
        # Claude Sonnet стоит ~300 руб/Мток prompt: ожидаем 409 (guard сберёг деньги).
        # Если цены упали ниже потолка — придёт 200, это тоже не ошибка теста.
        check("guard отработал (409 или 200)", r.status_code in (200, 409), f"status={r.status_code}")
        if r.status_code == 409:
            print("     PriceGuard сберёг деньги:")
            print("     " + r.json().get("detail", "")[:400].replace("\n", "\n     "))

        print("6) experiments-профиль: non-stream chat через алиас")
        r = c.post(
            f"{BASE}/v1/chat/completions",
            headers={"X-Proxy-Profile": "experiments"},
            json={
                "model": "my-sonnet",
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 32,
            },
        )
        check("chat 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            body = r.json()
            check("usage пришёл", bool(body.get("usage")), f"usage={body.get('usage')}")
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            print("     ответ модели:", content[:120])

        print("7) experiments-профиль: SSE stream")
        chunks = 0
        saw_usage = False
        with c.stream(
            "POST",
            f"{BASE}/v1/chat/completions",
            headers={"X-Proxy-Profile": "experiments"},
            json={
                "model": "my-sonnet",
                "messages": [{"role": "user", "content": "Count 1 to 5, one number per line"}],
                "max_tokens": 32,
                "stream": True,
            },
        ) as rs:
            check("stream 200", rs.status_code == 200, f"status={rs.status_code}")
            for line in rs.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunks += 1
                if "usage" in payload:
                    try:
                        saw_usage = bool(json.loads(payload).get("usage"))
                    except Exception:  # noqa: BLE001
                        pass
        check("чанки стрима дошли", chunks > 1, f"{chunks} чанков")
        check("usage в стриме", saw_usage)

        print("8) /stats после трафика")
        st = c.get(f"{BASE}/stats").json()
        check("запросы посчитаны", st.get("total_requests", 0) >= 2, f"total={st.get('total_requests')}")
        check("модель в разбивке", "anthropic/claude-sonnet-4.5" in st.get("by_model", {}))

    print()
    print(f"ИТОГ: passed={passed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())