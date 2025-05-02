@echo off
REM Проверка Python 3.10
py -3.10 --version || (
    echo Python 3.10 не найден. Установи его отсюда: https://www.python.org/downloads/release/python-3100/
    pause
    exit /b
)

REM Создание и активация виртуального окружения
py -3.10 -m venv btcli_env
call btcli_env\Scripts\activate.bat

REM Установка зависимостей
pip install --upgrade pip
pip install -r requirements.txt

REM Запуск примера
python scripts\fetch_subnets.py
pause
