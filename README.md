# Taostats Parser

Модульный Python pipeline для локальных HTML: subnet snapshot, metadata и
агрегированные validator metrics. Реализованы нормализация, валидация, CLI,
CSV/JSON и воспроизводимая демонстрация на синтетических данных.
Портфолио-проект Python Automation / Data Infrastructure Engineering.

## Установка и запуск

Python 3.10+; CI использует 3.12. Из корня репозитория:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -e . -r requirements-dev.txt
python -m taostats_pipeline demo --output ./output
```

Без инструментов тестирования достаточно `pip install -e .`. Установка может
загружать зависимости из PyPI. Сам demo не требует сети, секретов, браузера,
Google API или переменных окружения. Альтернативный entry point после установки:
`taostats-pipeline demo --output ./output`.

```bash
python -m taostats_pipeline validate --input ./src/taostats_pipeline/fixtures
python -m taostats_pipeline run --input ./src/taostats_pipeline/fixtures --output ./output
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

`validate` разбирает и проверяет вход без экспорта. Команды выводят JSON-отчёт:
`status`, `errors`, `record_counts`, `missing_ids`, `duplicate_ids`, `unexpected_ids`,
`generation`. Exit code: 0 — успех, 1 — ошибка обработки/экспорта,
2 — некорректные аргументы CLI.

## Вход и воспроизводимость

В каталоге входа нужен `manifest.json`:

```json
{
  "expected_ids": [7, 42],
  "collected_at_utc": "2026-01-01T00:00:00Z",
  "snapshot": "subnets.html",
  "subnets": [
    {"netuid": 7, "metadata": "metadata-7.html", "metrics": ["metrics.html"]},
    {"netuid": 42, "metadata": "metadata-42.html", "metrics": ["metrics.html"]}
  ]
}
```

Пути должны указывать на файлы внутри входного каталога после разрешения symlinks.
URL не загружаются. Manifest явно связывает страницы метрик с `netuid`; ID из
metadata дополнительно сверяется с manifest. Несколько файлов `metrics`
агрегируются как страницы одной подсети. Автор manifest отвечает за полноту
страниц и правильность их принадлежности. Demo использует фиксированное время:
содержимое результатов воспроизводимо, имя поколения уникально при каждом запуске.

## Результат и схемы

```text
output/
  current.json                 # указатель на последнее успешное поколение
  run-<unique-id>/
    subnets.csv
    subnets.json
    metadata.csv
    metadata.json
    validator_metrics.csv
    validator_metrics.json
```

Прочитайте `current.json` один раз, затем все файлы из указанного `generation`.
Старые поколения сохраняются. JSON набора — массив объектов;
CSV — UTF-8 с заголовком и теми же полями. Порядок полей определён моделью.

| Набор | Поля |
| --- | --- |
| Все наборы | `netuid`: неотрицательный integer; `collected_at_utc`: ISO 8601 UTC |
| `subnets` | `subnet_name`, `emission`, `price`, `market_cap`, `volume_24h`, `liquidity`, `root_proportion`, `sentiment`, `price_change_1h`, `price_change_24h`, `price_change_1w`, `price_change_1m` |
| `metadata` | `subnet_name`, `registration_date`, `network_key`, `discord_url`, `github_url`, `registration_cost` |
| `validator_metrics` | `vtrust_avg`, `incentive_orange_max`, `incentive_orange_min`, `incentive_green_max`, `incentive_green_min` |

Числовые поля внутри Python используют `Decimal`; JSON хранит их десятичными
строками для сохранения точности, CSV — десятичным текстом. `netuid` в JSON — число.
Отсутствующие необязательные значения — `None`, JSON `null`, пустое поле CSV.
Название, sentiment, дата регистрации, ключ и URL — строки.

Явные преобразования: `Bittensor`, `$`, `τ` удаляются только как префиксы денежных
полей; разделители тысяч принимаются группами по три; десятичный знак — точка.
Проценты сохраняются в процентных пунктах: `2.5% → Decimal("2.5")`, без деления на 100.
Число без `%` сохраняется как есть, включая `root_proportion`; единицы не угадываются.
Конвертации валют нет. `1.2M`, десятичная запятая, смешанные валюты, NaN, Infinity
и неоднозначный текст отвергаются. Маркеры пустоты: пустая строка, `—`, `N/A`.
VTrust усредняется и округляется до 6 знаков (Decimal half-even); min/max incentive
сохраняют точность входа. Emission/price берутся из snapshot, дубли из metadata
не экспортируются. Порядковая колонка и график за 7 дней не входят в модели.

## Валидация и ошибки

- Непустые три набора; обязательные корректные `netuid` и UTC-время.
- Уникальность ID; точное совпадение с `expected_ids`.
- Missing/unexpected IDs и связность наборов, без предположения о `0..N−1`.
- Конечные Decimal; неотрицательные показатели, кроме знаковых изменений цены.
- Согласованные пары min/max incentive. Неизвестная структура строки, роли метрик
  или некорректное число — ошибка, а не пропуск строки или значение `ERROR`.

Ошибки блокируют экспорт всех наборов. Файлы сначала пишутся во временный каталог
внутри output. После полной записи он переименовывается, затем атомарно заменяется
`current.json` через `os.replace`. Сбой записи/замены сохраняет предыдущий результат;
эти сценарии покрыты тестами. При первом неудачном экспорте может остаться пустой output.
Это атомарная публикация на локальной файловой системе без гарантии сохранности
при отключении питания; подробнее в архитектуре.

## Структура и проверки

```text
src/taostats_pipeline/
  models.py          # типизированные записи
  parsers/           # чистый разбор и явная нормализация
  validation.py      # отчёт и целостность
  pipeline.py        # load → parse → normalize → validate → export
  exporters/         # CSV/JSON и публикация поколения
  cli.py             # demo / run / validate
  fixtures/          # синтетические HTML и manifest
 tests/              # новые тесты и сохранённые characterization-тесты PR #1
 docs/architecture.md
```

Pytest блокирует TCP, UDP, DNS и дочерние процессы, включая end-to-end вызов demo.
Тесты PR #1 извлекают выбранные чистые функции через AST: тела старых модулей
не импортируются и не запускаются. Новые тесты прямо импортируют пакет.
Ruff проверяет пакет, тесты и конфигурацию. CI работает на pull requests,
без расписания и секретов.

## Ограничения и следующие этапы

Только локальные фикстуры; актуальность селекторов живого сайта не проверяется.
Не реализованы сбор страниц, обнаружение пропущенной пагинации, Google Sheets,
production deployment, постоянный журнал запусков и очистка поколений.
CLI-отчёт описывает текущий запуск. При изменении HTML нужны изменения парсеров и тестов.

`src/main.py`, `src/subnets_full_parser.py`, `src/subnets_vtrust_incentive.py`
сохранены как legacy: **не запускайте их**. Они содержат внешние операции и могут
изменять таблицы; новый пакет их не использует. `requirements.txt` относится к legacy
и для demo не нужен. `.env.example` — план конфигурации, пакет её не читает.
Credentials, рабочие ресурсы и данные прежней команды не используются.

Будущие интеграции и требования к Google Sheets описаны в
[docs/architecture.md](docs/architecture.md); в этом PR они не включены.
