```markdown
# Taostats Poller → Google Sheets

Простой скрипт на Python, который опрашивает RPC-ноду Bittensor и записывает каждый новый блок в Google Sheets.

## Секреты и окружение

Все персональные или чувствительные данные (Spreadsheet ID, JSON сервисного аккаунта, WS_URL, POLL_INTERVAL) должны храниться в системе секретов (GitHub Secrets, Vault и т.п.) и не попадать в репозиторий.

### Переменные окружения (через Secrets)

- `WS_URL` — URL WebSocket RPC (по умолчанию: `wss://mainnet-rpc.bittensor.com`)
- `POLL_INTERVAL` — интервал опроса в секундах (по умолчанию: `5`)
- `SPREADSHEET_ID` — ID Google Sheets таблицы
- `GOOGLE_SERVICE_ACCOUNT_JSON` — полный JSON сервисного аккаунта

### Установка зависимостей

```bash
git clone https://github.com/your-name/taostats-poller.git
cd taostats-poller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Локальный запуск

```bash
export WS_URL="wss://mainnet-rpc.bittensor.com"
export POLL_INTERVAL=5
export SPREADSHEET_ID="your_spreadsheet_id"
export GOOGLE_SERVICE_ACCOUNT_JSON="$(< /path/to/service-account.json)"
python src/main.py
```
```

---
