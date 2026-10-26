# RouterAI Proxy

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)

**Прозрачный прокси для [RouterAI](https://routerai.ru/) с интеллектуальным контролем провайдеров, автоматической защитой от дорогих запросов и удобным каталогом моделей.**

> 🛡️ **PriceGuard** автоматически блокирует дорогих провайдеров, чтобы вы не потратили 390₽ за 1M токенов случайно.

---

## 📋 Содержание

- [Зачем это нужно](#-зачем-это-нужно)
- [Возможности](#-возможности)
- [Быстрый старт](#-быстрый-старт)
- [Настройка Cline (VS Code)](#-настройка-cline-vs-code)
- [Конфигурация](#-конфигурация)
- [Профили](#-профили)
- [Поиск провайдеров и цен](#-поиск-провайдеров-и-цен)
- [API Endpoints](#-api-endpoints)
- [Архитектура](#-архитектура)
- [Статистика и мониторинг](#-статистика-и-мониторинг)
- [Безопасность](#-безопасность)
- [Troubleshooting](#-troubleshooting)

---

## 🎯 Зачем это нужно

### Проблема

[RouterAI](https://routerai.ru/) — мощный роутер для LLM API, который автоматически выбирает провайдеров по цене и доступности. Но клиенты типа **Cline**, **Continue**, **aider** не умеют передавать параметры роутинга (`provider.order`, `provider.ignore`, `provider.country`) в теле запроса.

**Результат:** RouterAI может случайно отправить ваш запрос к дорогому провайдеру (например, DeepSeek за 390₽/1M токенов), и вы узнаете об этом только когда деньги уже списаны.

### Решение

Этот прокси решает проблему, добавляя слой контроля **между клиентом и RouterAI**:

```
Cline → [RouterAI Proxy] → RouterAI → Провайдеры
         ↓
    PriceGuard + Provider Routing + Catalog
```

---

## ✨ Возможности

### 🛡️ Защита от дорогих провайдеров

**PriceGuard** автоматически блокирует провайдеров дороже ценового потолка:

```bash
# Попытка запросить Claude через профиль default (потолок 30₽/M)
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "my-sonnet", "messages": [...]}'

# Ответ: 409 Conflict
{
  "detail": "PriceGuard: ВСЕ провайдеры модели anthropic/claude-sonnet-4.5 дороже потолка профиля 'default'.\nПотолок: 30 руб/1M prompt токенов.\nЦены провайдеров (руб/1M): anthropic=314.1; google-vertex=314.1; ...\nВарианты: 1) заголовок X-Proxy-Price-Override: true; 2) переключить профиль..."
}
```

### 🎯 Интеллектуальный роутинг

- **`provider.order`** — приоритет провайдеров (дешёвые первыми)
- **`provider.ignore`** — чёрный список (исключить дорогих/нестабильных)
- **`provider.country`** — жёсткий фильтр по стране (например, только `ru`)
- **Model-specific overrides** — разные настройки для разных моделей

### 📚 Удобный каталог

- **Алиасы моделей** — `my-sonnet` вместо `anthropic/claude-sonnet-4.5`
- **Dropdown в Cline** — все модели + алиасы сразу доступны
- **Hot reload** — изменения в конфигах применяются без перезапуска

### 🏗️ Модульные профили

Каждый профиль — отдельный YAML-файл в `profiles/`:

```
profiles/
├── default.yaml       # Строгий профиль (потолок 30₽/M)
├── experiments.yaml   # Мягкий профиль для тестов
├── qwen.yaml         # Бюджетный профиль для Qwen
└── anthropic.yaml    # Профиль для Claude
```

### 📊 Статистика без БД

- **`requests.jsonl`** — append-only журнал каждого запроса
- **`stats.json`** — периодический снапшот агрегатов
- **`GET /stats`** — статистика через API

### 🛡️ Отказоустойчивость

**Circuit Breaker** защищает от каскадных сбоев:
- Автоматически открывает circuit после 5 подряд ошибок
- Восстанавливается через 60 секунд
- Возвращает понятную ошибку клиенту

---

## 🚀 Быстрый старт

### 1. Локальный запуск (рекомендуется)

**Linux/macOS:**

```bash
# Клонируйте репозиторий
git clone https://github.com/Vik154/routerai-proxy.git
cd routerai-proxy

# Создайте виртуальное окружение
python -m venv .venv
source .venv/bin/activate

# Установите зависимости
pip install -r requirements.txt

# Скопируйте и настройте конфиг
cp config.example.yaml config.yaml
# Отредактируйте config.yaml под свои нужды

# Установите API ключ RouterAI
export ROUTERAI_API_KEY="ваш_ключ_из_routerai"

# Запустите прокси
python run.py
```

**Windows (PowerShell):**

```powershell
# Клонируйте репозиторий
git clone https://github.com/Vik154/routerai-proxy.git
cd routerai-proxy

# Создайте виртуальное окружение
python -m venv .venv
.venv\Scripts\activate

# Установите зависимости
pip install -r requirements.txt

# Скопируйте и настройте конфиг
Copy-Item config.example.yaml config.yaml
# Отредактируйте config.yaml под свои нужды

# Установите API ключ RouterAI
$env:ROUTERAI_API_KEY = "ваш_ключ_из_routerai"

# Запустите прокси
python run.py
```

**Проверка:**

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","uptime_s":...,"circuit_breaker":{"state":"closed","failures":0},"profiles":["default","experiments","qwen","anthropic"]}
```

**Остановка:**
- `Ctrl+C` в терминале
- Или закрыть окно терминала (сервер автоматически остановится)

### 2. Docker запуск

```bash
# Скопируйте и настройте конфиг
cp config.example.yaml config.yaml

# Установите API ключ
export ROUTERAI_API_KEY="ваш_ключ_из_routerai"

# Запустите
docker-compose up -d

# Проверка
curl http://127.0.0.1:8000/health
```

---

## 🔧 Настройка Cline (VS Code)

### Базовая настройка

1. Откройте настройки Cline
2. Выберите **API Provider**: `OpenAI Compatible`
3. Укажите:
   - **Base URL**: `http://127.0.0.1:8000/v1`
   - **API Key**: `dummy` (любой текст, прокси использует свой ключ)
4. В dropdown **Model ID** появятся модели из RouterAI + ваши алиасы
5. Выберите нужную модель (например, `my-sonnet` или `qwen3.8`)

### Переключение профилей

В настройках Cline → **Custom Headers** добавьте:

| Header Name | Header Value |
|-------------|--------------|
| `X-Proxy-Profile` | `qwen` (или `anthropic`, `experiments`, ...) |

Теперь Cline будет использовать указанный профиль.

---

## ⚙️ Конфигурация

### Структура конфигов

```
config.yaml              # Общие настройки (server, routerai, circuit_breaker)
profiles/
├── default.yaml         # Профиль по умолчанию
├── experiments.yaml     # Профиль для экспериментов
├── qwen.yaml           # Профиль для Qwen моделей
└── anthropic.yaml      # Профиль для Claude
```

### config.yaml (общие настройки)

```yaml
routerai:
  api_url: "https://routerai.ru/api/v1"
  api_key_env: "ROUTERAI_API_KEY"  # Имя env-переменной с ключом

server:
  host: "127.0.0.1"  # ТОЛЬКО localhost для безопасности
  port: 8000
  log_level: "info"

catalog:
  models_ttl: 300      # TTL кэша списка моделей, сек
  price_ttl: 3600      # TTL кэша цен endpoint'ов, сек
  unknown_model: "reject"  # reject = 404 с did-you-mean | passthrough

profile_selection:
  method: "header"              # header | default_only
  header_name: "X-Proxy-Profile"
  default_profile: "default"
  unknown_profile: "reject"     # reject | fallback_default

circuit_breaker:
  failure_threshold: 5   # Ошибок до открытия circuit breaker
  recovery_timeout: 60   # Секунд до HALF_OPEN
  success_threshold: 1   # Успехов до закрытия

stats:
  requests_log: "data/requests.jsonl"
  stats_snapshot: "data/stats.json"
  flush_interval: 60
```

### profiles/default.yaml (пример профиля)

```yaml
# Строгий профиль по умолчанию

provider:
  order: []            # Приоритет провайдеров (пусто = по цене)
  ignore: ["deepseek"] # Чёрный список
  only: []             # Белый список (осторожно!)
  country: null        # "ru" = только РФ серверы
  allow_fallbacks: true

price_ceiling:
  prompt_per_mtoken: 30        # Потолок 30₽/1M prompt токенов
  on_all_expensive: "block"    # block | allow_cheapest | warn

model_map:
  # Алиасы для удобства
  "my-sonnet": "anthropic/claude-sonnet-4.5"
  "my-gpt": "openai/gpt-4o"
  "qwen3.8": "qwen/qwen3.8-27b"

hide_over_ceiling: false  # Прятать из dropdown модели дороже потолка

# Model-specific overrides
models:
  "anthropic/claude-sonnet-4.5":
    price_ceiling:
      prompt_per_mtoken: 50  # Для Claude потолок выше
  
  "qwen/qwen3.8-27b":
    provider:
      order: ["tenstorrent", "darkbloom"]  # Для Qwen предпочитаем дешёвых
```

---

## 📁 Профили

Проект включает готовые профили в `profiles/`:

### default.yaml

**Строгий профиль** с потолком 30₽/1M prompt токенов. Подходит для production.

### experiments.yaml

**Мягкий профиль** с потолком 200₽/1M. Подходит для тестов и экспериментов.

### qwen.yaml

**Бюджетный профиль** для Qwen моделей:
- Потолок: 25₽/1M
- Приоритет: `tenstorrent` (2.1₽), `darkbloom` (3.7₽), `chutes` (4.1₽)
- Исключены: `coreweave` (41.9₽), `cerebras` (103.7₽)

### anthropic.yaml

**Профиль для Claude**:
- Потолок: 400₽/1M (Claude дорогие, но качественные)
- Приоритет: `anthropic`, `google-vertex`, `amazon-bedrock`

### Создание своего профиля

Создайте файл `profiles/my_profile.yaml`:

```yaml
provider:
  order: ["openai", "anthropic"]
  ignore: ["deepseek", "google"]
  country: "us"

price_ceiling:
  prompt_per_mtoken: 100
  on_all_expensive: "warn"

model_map:
  "fast": "openai/gpt-4o-mini"
  "smart": "anthropic/claude-sonnet-4.5"
```

Используйте через заголовок: `X-Proxy-Profile: my_profile`

---

## 🔍 Поиск провайдеров и цен

### Через CLI

```bash
# Провайдеры для конкретной модели
python -m proxy.cli providers anthropic/claude-sonnet-4.5

# С фильтром по стране
python -m proxy.cli providers qwen/qwen3.8-27b --country ru

# Все провайдеры
python -m proxy.cli all-providers

# Все провайдеры в РФ
python -m proxy.cli all-providers --country ru
```

**Пример вывода:**

```
Модель: qwen/qwen3.8-27b
Провайдеров: 21

Tag                  Name                      Prompt ₽/Mtok   Completion ₽/Mtok    Countries
===============================================================================================
tenstorrent          Tenstorrent               2.1             52.4                 us
darkbloom            Darkbloom                 3.7             230.3                N/A
chutes               Chutes                    4.1             130.9                us
...
```

### Через API

```bash
# Провайдеры для модели
curl "http://127.0.0.1:8000/providers?model=anthropic/claude-sonnet-4.5"

# Только провайдеры с серверами в РФ
curl "http://127.0.0.1:8000/providers?model=qwen/qwen-72b-chat&country=ru"

# Все провайдеры
curl "http://127.0.0.1:8000/providers"
```

### Workflow настройки конфига

1. **Найдите нужную модель:**
   ```bash
   python -m proxy.cli all-providers | grep -i qwen
   ```

2. **Посмотрите провайдеров:**
   ```bash
   python -m proxy.cli providers qwen/qwen3.8-27b
   ```

3. **Скопируйте теги в профиль:**
   ```yaml
   profiles:
     qwen:
       provider:
         order: ["tenstorrent", "darkbloom", "chutes"]
         ignore: ["coreweave", "cerebras"]
       price_ceiling:
         prompt_per_mtoken: 25
   ```

---

## 🌐 API Endpoints

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/health` | GET | Проверка работоспособности |
| `/stats` | GET | Статистика запросов |
| `/v1/models` | GET | Список моделей (для dropdown Cline) |
| `/providers` | GET | Список провайдеров с ценами |
| `/v1/chat/completions` | POST | Прокси к RouterAI (и другие OpenAI API) |

**Примеры:**

```bash
# Health check
curl http://127.0.0.1:8000/health

# Статистика
curl http://127.0.0.1:8000/stats

# Список моделей
curl http://127.0.0.1:8000/v1/models

# Запрос через прокси
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Proxy-Profile: qwen" \
  -d '{
    "model": "my-qwen",
    "messages": [{"role": "user", "content": "Привет!"}]
  }'
```

---

## 🏗️ Архитектура

```
┌─ Clients: Cline / Continue / aider / curl ─────────────────┐
│        (profile via X-Proxy-Profile header)                │
└──────────────────────┬─────────────────────────────────────┘
                       ▼
┌─ Transport Layer: FastAPI ─────────────────────────────────┐
│  • catch-all роутинг + SSE pass-through                    │
│  • GET /v1/models, /health, /stats, /providers              │
├────────────────────────────────────────────────────────────┤
│  Transformer Pipeline (async)                              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 1. ModelRewriter      — алиасы + валидация          │  │
│  │ 2. PriceGuard         — ignore дорогих (409 block)  │  │
│  │ 3. ProviderRouting    — order/ignore/country        │  │
│  │ 4. CostLogger         — статистика + usage parsing  │  │
│  └─────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────┤
│  Upstream Layer                                            │
│  • httpx.AsyncClient (connection pool)                     │
│  • Circuit Breaker (closed/open/half_open)                 │
│  • Автоматическая подмена Authorization                    │
├────────────────────────────────────────────────────────────┤
│  Config Layer                                              │
│  • YAML (config.yaml + profiles/*.yaml)                    │
│  • Hot reload (file watcher)                               │
│  • Profile resolver (model-specific overrides)             │
├────────────────────────────────────────────────────────────┤
│  Stats Layer                                               │
│  • requests.jsonl (append-only)                            │
│  • stats.json (periodic snapshot)                          │
│  • In-memory агрегаты                                      │
└────────────────────────────────────────────────────────────┘
```

### Ключевые принципы

- **Прозрачность** — всё, что не требует вмешательства, проходит как есть
- **Стриминг** — SSE-чанки передаются без буферизации
- **Fail-safe** — ошибки в конфигах не роняют прокси (остаётся старый конфиг)
- **Informed Refusal** — денежные отказы возвращают понятные сообщения с путями обхода

---

## 📊 Статистика и мониторинг

### Структура данных

**`data/requests.jsonl`** — append-only журнал:

```json
{"ts": "2026-10-26T03:25:06+00:00", "model": "anthropic/claude-sonnet-4.5", "profile": "default", "provider": "anthropic", "status": 200, "prompt_tokens": 1234, "completion_tokens": 567, "total_tokens": 1801, "latency_ms": 2341.5}
```

**`data/stats.json`** — агрегаты (обновляется раз в 60с):

```json
{
  "total_requests": 42,
  "total_prompt_tokens": 51828,
  "total_completion_tokens": 23814,
  "total_tokens": 75642,
  "by_model": {
    "anthropic/claude-sonnet-4.5": {"requests": 25, "tokens": 45000},
    "qwen/qwen3.8-27b": {"requests": 17, "tokens": 30642}
  },
  "by_provider": {
    "anthropic": {"requests": 25, "tokens": 45000},
    "tenstorrent": {"requests": 17, "tokens": 30642}
  }
}
```

### Анализ статистики

```bash
# Количество запросов
jq -s 'length' data/requests.jsonl

# Токенов по моделям
jq -s 'group_by(.model) | map({model: .[0].model, tokens: map(.total_tokens) | add})' data/requests.jsonl

# Среднее время ответа
jq -s '[.[].latency_ms] | add / length' data/requests.jsonl

# Запросы по профилям
jq -s 'group_by(.profile) | map({profile: .[0].profile, count: length})' data/requests.jsonl
```

### Мониторинг через API

```bash
# Текущая статистика
curl http://127.0.0.1:8000/stats

# Health check с состоянием circuit breaker
curl http://127.0.0.1:8000/health
```

---

## 🔒 Безопасность

### По умолчанию

- Прокси слушает **только `127.0.0.1`** (localhost)
- В Docker publish только `127.0.0.1:8000:8000`
- Клиентский API-ключ **игнорируется**, используется серверный ключ из `ROUTERAI_API_KEY`

### Что НЕ коммитить в Git

Файл `.gitignore` уже настроен правильно, но проверьте:

```bash
# Эти файлы НЕ должны попасть в коммит:
config.yaml      # содержит настройки (но не секреты)
.env             # если используете
data/*.jsonl     # статистика
data/*.json      # снапшоты
*.log            # логи
```

### Рекомендации

1. **Не меняйте `host: 0.0.0.0`** в `config.yaml` без необходимости
2. **Используйте отдельные API-ключи** RouterAI для разных сред (dev/prod)
3. **Установите лимиты** в личном кабинете RouterAI как дополнительную защиту
4. **Регулярно проверяйте** `data/requests.jsonl` на аномалии

---

## 🔧 Troubleshooting

### "Модель не найдена"

**Симптом:** 404 с сообщением `Модель 'xxx' не найдена в каталоге RouterAI`

**Причины:**
1. Модель не существует в RouterAI
2. Опечатка в названии
3. Каталог ещё не обновился

**Решение:**

```bash
# Проверьте существование модели
curl http://127.0.0.1:8000/v1/models | grep -i "название"

# Принудительно обновите каталог (подождите 5 минут или перезапустите прокси)
```

### "Все провайдеры дороже потолка"

**Симптом:** 409 с таблицей цен

**Решение (выберите одно):**

1. **Переключить профиль:**
   ```bash
   curl -H "X-Proxy-Profile: experiments" ...
   ```

2. **Повысить потолок** в `profiles/default.yaml`:
   ```yaml
   price_ceiling:
     prompt_per_mtoken: 100  # было 30
   ```
   Hot reload применит изменения автоматически.

3. **Override на запрос:**
   ```bash
   curl -H "X-Proxy-Price-Override: true" ...
   ```

### Circuit breaker открыт

**Симптом:** 503 `RouterAI временно недоступен (circuit breaker OPEN)`

**Причина:** RouterAI вернул 5+ ошибок подряд

**Решение:**
- Подождите 60 секунд (circuit breaker перейдёт в `HALF_OPEN`)
- Проверьте статус RouterAI: https://routerai.ru/status
- Проверьте логи прокси: `python run.py` (если запущен локально)

### Статистика не обновляется

**Симптом:** `stats.json` не обновляется

**Решение:**

```bash
# Проверьте что директория data/ существует
ls -la data/

# Проверьте права доступа
chmod 755 data/

# Принудительный flush (перезапустите прокси)
```

### Docker контейнер не стартует

**Симптом:** `docker-compose up` падает

**Проверка:**

```bash
# Логи контейнера
docker-compose logs

# Проверьте что config.yaml существует
ls -la config.yaml

# Проверьте что ROUTERAI_API_KEY установлен
echo $ROUTERAI_API_KEY
```

---

## 📄 Лицензия

MIT © [Viktor](https://github.com/Vik154)

Полный текст лицензии в файле [LICENSE](LICENSE).