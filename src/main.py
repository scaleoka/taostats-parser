```python
#!/usr/bin/env python3
import os
import time
import datetime
import json
from substrateinterface import SubstrateInterface
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Настройки через переменные окружения
WS_URL = os.getenv("WS_URL", "wss://mainnet-rpc.bittensor.com")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 5))
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheets_service():
    # Создаём credentials из JSON строки
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def append_row(service, values):
    body = {"values": [values]}
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Sheet1!A:C",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()


def main():
    substrate = SubstrateInterface(
        url=WS_URL,
        ss58_format=42,
        type_registry_preset="substrate-node-template"
    )
    sheets_service = get_sheets_service()
    last_hash = None

    while True:
        try:
            head = substrate.get_chain_head()
            if head != last_hash:
                block = substrate.get_block(head)
                number = block['block']['header']['number']
                timestamp = datetime.datetime.utcnow().isoformat()
                print(f"[{timestamp}] New block #{number}")
                append_row(sheets_service, [timestamp, number, head])
                last_hash = head
        except Exception as e:
            print(f"[Error] {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
