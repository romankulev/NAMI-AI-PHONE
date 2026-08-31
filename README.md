# ElevenLabs NAMI Voice Agent Mini App

Python-приложение для голосового администратора NAMI BEAUTY. Оно работает в Telegram Mini App или обычном браузере, разговаривает через ElevenLabs Agents по WebRTC и использует существующие MCP-инструменты n8n/YCLIENTS.

Настраивать агента вручную в интерфейсе ElevenLabs не требуется: Python-скрипт создаёт и обновляет агента, голосовую модель, Qwen, промпт и MCP через API.

## Схема

```text
Telegram Mini App / браузер
  ├─ POST → Python → краткоживущий ElevenLabs WebRTC token
  └─ WebRTC-аудио ↔ ElevenLabs Agent
                         ├─ Qwen — логика администратора
                         └─ MCP → n8n → YCLIENTS
```

Постоянный `ELEVENLABS_API_KEY` остаётся на Python-сервере. Браузер получает только одноразовый токен разговора.

## Первый запуск

Требуется Python 3.11+ и аккаунт ElevenLabs с доступом к Agents.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Заполните в `.env`:

```dotenv
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

- `ELEVENLABS_API_KEY` — серверный API-ключ с правами на чтение и изменение ElevenAgents.
- `ELEVENLABS_VOICE_ID` — ID выбранного женского голоса. Сам голос можно взять из Voice Library или создать через ElevenLabs Voice API.
- `ELEVENLABS_AGENT_ID` оставьте пустым при первом запуске.

Создайте агента и подключите старые MCP-серверы:

```bash
set -a
source .env
set +a
python deploy/sync_elevenlabs_agent.py --env .env
```

Скрипт выполняет следующие действия:

1. Получает актуальный список LLM и выбирает hosted Qwen.
2. Читает прежний `MCP_SERVERS_JSON`.
3. Находит MCP-интеграцию по URL либо создаёт новую без дублей.
4. Подключает MCP к агенту с немедленным выполнением инструментов без промежуточной речи.
5. Создаёт приватного ElevenLabs Agent или обновляет существующего.
6. Записывает `ELEVENLABS_AGENT_ID` и ID MCP обратно в `.env`, не меняя секреты.

После изменения промпта, голоса, Qwen или MCP просто снова запустите эту команду. Кабинет ElevenLabs открывать не нужно.

> В новом ElevenLabs workspace MCP может быть выключен до принятия условий MCP. Если API вернёт соответствующую ошибку, условия придётся один раз принять владельцу workspace; дальнейшая настройка выполняется скриптом.

## Запуск приложения

```bash
set -a
source .env
set +a
uvicorn app.main:app --reload
```

Откройте `http://localhost:8000`.

## Голос и поведение

По умолчанию используются:

- `eleven_v3_conversational` — наиболее выразительная realtime-модель ElevenLabs;
- hosted Qwen — быстрая модель для диалога и вызова инструментов;
- `scribe_realtime` — распознавание речи;
- `turn_v3` — детектор окончания реплики и перебиваний;
- русский язык и подсказки произношения NAMI/YCLIENTS;
- приватный агент и краткоживущие WebRTC-токены.

Основные настройки находятся в `.env`:

- `ELEVENLABS_VOICE_ID` — голос;
- `ELEVENLABS_VOICE_STABILITY` — стабильность голоса, начните с `0.45`;
- `ELEVENLABS_VOICE_SPEED` — скорость речи;
- `ELEVENLABS_TURN_EAGERNESS` — `patient`, `normal` или `eager`;
- `ELEVENLABS_LLM_MODEL` — желаемая модель; скрипт сверяет её с доступными моделями workspace;
- `REALTIME_SYSTEM_PROMPT` и `REALTIME_GREETING_PROMPT` — поведение и первая реплика;
- `MCP_SERVERS_JSON` — прежний список MCP, совместимый с предыдущей OpenAI-версией.

Браузерный SDK работает через WebRTC. Это даёт эхоподавление, шумоподавление и возможность перебивать ассистента. Кнопка отключения микрофона использует штатное управление активной ElevenLabs-сессией.

## MCP NAMI

Сохранены прежние read-only инструменты:

- `nami_current_datetime`;
- `nami_get_services`;
- `nami_get_staff_for_service`;
- `nami_get_available_dates`;
- `nami_get_available_times`;
- `nami_check_slot`.

Скрипт выставляет `pre_tool_speech=off` и `execution_mode=immediate`, поэтому агент не должен произносить «сейчас подумаю» перед обращением к API. В системном промпте также указано сразу продолжать ответ после получения результата.

Для защищённого MCP можно использовать ссылку на переменную окружения:

```dotenv
N8N_MCP_TOKEN=...
MCP_SERVERS_JSON='[{"type":"mcp","server_label":"n8n","server_url":"https://n8n.example.com/mcp","authorization":"Bearer ${N8N_MCP_TOKEN}","require_approval":"never"}]'
```

Значение токена подставляется только во время Python-синхронизации и не передаётся в HTML.

## Тесты

```bash
pytest -q
```

Тесты используют поддельные ответы API и не расходуют баланс ElevenLabs.

## Docker и HTTPS

```bash
docker compose up -d --build
```

Caddy автоматически получает TLS-сертификат. Для микрофона за пределами `localhost` браузеру нужен HTTPS.

## Telegram Mini App

После публикации HTTPS-адреса:

1. Откройте `@BotFather`.
2. Выберите бота через `/mybots`.
3. Откройте Mini App или Menu Button.
4. Укажите публичный URL приложения.

Перед публичным запуском нужно добавить проверку `Telegram.WebApp.initData`, лимит времени и частоты создания разговоров. Сейчас endpoint токена является MVP и доступен всем посетителям страницы.
