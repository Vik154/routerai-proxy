# RouterAI Proxy под Cline VS Code

Прокси для [RouterAI](https://routerai.ru/) с контролем провайдеров, цен и каталога моделей.

## Зачем это нужно

RouterAI — роутер для LLM API, который автоматически выбирает провайдеров по цене и доступности. Но клиенты типа Cline не умеют передавать параметры роутинга (`provider.order`, `provider.ignore`, `provider.country`) в теле запроса.

Этот прокси решает проблему:
- **Автоматический контроль провайдеров** — задавайте приоритеты и чёрные списки
- **Защита от дорогих провайдеров** — автоматический ignore по ценовому потолку
- **Удобный каталог** — алиасы моделей в dropdown Cline
- **Прозрачность** — работает с любым OpenAI-совместимым клиентом
- **Переиспользуемость** — один прокси для Cline, Continue, aider, скриптов

## Быстрый старт

### 1. Локальный запуск (рекомендуется)

```bash
# Клонируйте репозиторий
git clone <repo-url>
cd routerai-proxy

# Создайте виртуальное окружение
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Установите зависимости
pip install -r requirements.txt

# Скопируйте и настройте конфиг
cp config.example.yaml config.yaml
# Отредактируйте config.yaml под свои нужды

# Установите API ключ RouterAI
export ROUTERAI_API_KEY="ваш_ключ_из_routerai"

# Запустите прокси
python run.py

# → Прокси запустится на http://127.0.0.1:8000.

# Отключение:
# Либо ctrl+c в терминале
# Либо закрыть окно терминала (сервер автоматически остановится)
```

## 2. Docker запуск

```bash
# Скопируйте и настройте конфиг
cp config.example.yaml config.yaml

# Установите API ключ
export ROUTERAI_API_KEY="ваш_ключ_из_routerai"

# Запустите
docker-compose up -d
```


## Настройка Cline (VS Code)

1. Откройте настройки Cline
2. Выберите **API Provider**: `OpenAI Compatible`
3. Укажите:
    - **Base URL**: `http://127.0.0.1:8000/v1`
    - **API Key**: `dummy` (любой текст, прокси использует свой ключ)
4. В dropdown **Model ID** появятся модели из RouterAI + ваши алиасы из `model_map`
5. Выберите нужную модель (например, `my-sonnet` или `anthropic/claude-sonnet-4.5`)

### Переключение профилей (опционально)

В настройках Cline → **Custom Headers** добавьте:

- **Header Name**: `X-Proxy-Profile`
- **Header Value**: `experiments` (или другое имя профиля из конфига)

Теперь Cline будет использовать профиль `experiments` вместо `default`.

## Конфигурация

Полный пример в `config.example.yaml`. Основные разделы:

### Профили
```yaml
# Конфиг профилей
profiles:
  default:
    provider:
      order: ["anthropic", "openai"]  # Приоритет провайдеров
      ignore: ["deepseek"]             # Чёрный список
      country: "ru"                    # Только РФ серверы
    
    price_ceiling:
      prompt_per_mtoken: 30            # Макс. цена за 1M токенов
      on_all_expensive: "block"        # block | allow_cheapest | warn
    
    model_map:
      "my-sonnet": "anthropic/claude-sonnet-4.5"  # Алиас
```


### Выбор профиля

```yaml
profile_selection:
  method: "header"                      # header | default_only
  header_name: "X-Proxy-Profile"
  default_profile: "default"
  unknown_profile: "reject"             # reject | fallback_default
```

### Circuit Breaker

```yaml
circuit_breaker:
  failure_threshold: 5                  # Ошибок до открытия
  recovery_timeout: 60                  # Секунд до retry
  success_threshold: 1                  # Успехов до закрытия
```

## Архитектура

```
┌─ Clients: Cline / Continue / aider / curl ─┐
│        (profile via X-Proxy-Profile)       │
└──────────────────────┬─────────────────────┘
                       ▼
┌─ Transport: catch-all + SSE pass-through ──┐
│  GET /v1/models  /health  /stats           │
├────────────────────────────────────────────┤
│  Transformer pipeline (async)              │
│  1. ModelRewriter — алиасы                 │
│  2. PriceGuard — ignore дорогих            │
│  3. ProviderRouting — order/ignore/country │
│  4. CostLogger — статистика                │
├────────────────────────────────────────────┤
│  Upstream: httpx pool + circuit breaker    │
├────────────────────────────────────────────┤
│  Config (YAML, profiles, hot reload)       │
│  Stats (jsonl + periodic snapshot)         │
└────────────────────────────────────────────┘
```

## API Endpoints

- `GET /health` — проверка работоспособности
- `GET /stats` — статистика запросов
- `GET /v1/models` — список моделей (для dropdown Cline)
- `POST /v1/chat/completions` — прокси к RouterAI (и другие OpenAI API)

## Поиск провайдеров и цен

### Через API
```bash
# Провайдеры для конкретной модели
curl "http://127.0.0.1:8000/providers?model=anthropic/claude-sonnet-4.5"

# Только провайдеры с серверами в РФ
curl "http://127.0.0.1:8000/providers?model=qwen/qwen-72b-chat&country=ru"

# Все провайдеры по всем моделям
curl "http://127.0.0.1:8000/providers"
```

### Через CLI
```bash
# Провайдеры для модели
python -m proxy.cli providers anthropic/claude-sonnet-4.5

# С фильтром по стране
python -m proxy.cli providers anthropic/claude-sonnet-4.5 --country ru

# Все провайдеры
python -m proxy.cli all-providers

# Все провайдеры в РФ
python -m proxy.cli all-providers --country ru
```

### Workflow настройки конфига
- Найдите нужную модель:
```bash 
curl http://127.0.0.1:8000/v1/models | grep -i qwen
```
- Посмотрите провайдеров:
```bash
python -m proxy.cli providers qwen/qwen-72b-chat
```
- Скопируйте теги в config.yaml:
```yaml
profiles:
  default:
    provider:
      order: ["yandex", "sber"]   # предпочитаем РФ провайдеров
      ignore: ["deepseek"]        # исключаем дорогих
      country: "ru"               # только серверы в РФ
```

## Статистика

Прокси ведёт статистику:
- `requests.jsonl` — append-only журнал каждого запроса (модель, токены, латентность)
- `stats.json` — периодический снапшот агрегатов (раз в 60с)

Пример анализа:
```bash
# Количество запросов
jq -s 'length' requests.jsonl

# Токенов по моделям
jq -s 'group_by(.model) | map({model: .[0].model, tokens: map(.total_tokens) | add})' requests.jsonl
```

## Безопасность

- Прокси слушает **только `127.0.0.1`** по умолчанию
- В Docker publish только `127.0.0.1:8000:8000`
- Клиентский API-ключ игнорируется, используется серверный ключ из `ROUTERAI_API_KEY`
- Никогда не коммитьте `config.yaml` и `.env` в Git

## Troubleshooting

### "Модель не найдена"

Проверьте:
1. Модель существует в RouterAI: `curl https://routerai.ru/api/v1/models`
2. Алиас правильно указан в `model_map`
3. Каталог обновился: подождите `models_ttl` (5 минут по умолчанию)

### "Все провайдеры дороже потолка"

Варианты:
1. Переключить профиль на `experiments` (менее строгий)
2. Повысить `price_ceiling` в `config.yaml` (hot reload)
3. Добавить `X-Proxy-Price-Override: true` в заголовок запроса

### Circuit breaker открыт

RouterAI временно недоступен. Подождите `recovery_timeout` (60с по умолчанию) или проверьте статус RouterAI.

## Лицензия

MIT
