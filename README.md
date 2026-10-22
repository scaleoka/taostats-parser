# Taostats Parser — Python Automation / Data Infrastructure

Портфолио-проект с двумя отдельными направлениями: контролируемая обработка
Taostats API v1 Metagraph и модульный pipeline для локальных HTML. В центре
проекта — проверка полноты данных, явные ограничения операций, точные Decimal
и воспроизводимые offline-демонстрации на синтетических данных.

| Направление | Реализовано |
| --- | --- |
| Taostats API v1 Metagraph | Контролируемое получение, проверка пагинации, `CompletedQuery`, нормализация, вычисление VTrust и incentive по явным фильтрам |
| Local HTML Pipeline | Локальные HTML: subnet snapshot, metadata, validator metrics; валидация, версионированный экспорт CSV/JSON, опциональный Google Sheets exporter |

API v1 и HTML pipeline имеют отдельные модели и пути обработки. API-результаты
не передаются автоматически в существующие HTML export или Google Sheets.

## Quick Start: offline API v1 demo (Windows PowerShell)

Нужен Python 3.10+ с launcher `py`. Из корня репозитория:

```powershell
py -m pip install -e .
py -m taostats_pipeline.api_v1_demo
```

Установка может потребовать сети для загрузки зависимостей. Сам demo полностью
offline: использует встроенную синтетическую fixture, не читает API-ключи,
не создаёт HTTP transport и не обращается к Google Sheets. Результат — JSON
в stdout, без файлов экспорта. Демонстрационный entry point поддерживает только
offline-режим; live-параметров у него нет.

Фактический формат результата demo из завершённого PR #12:

```json
{
  "status": "ok",
  "mode": "offline",
  "synthetic": true,
  "message": "Synthetic demo only; not real Bittensor data.",
  "netuid": 7,
  "block": 1000,
  "record_count": 6,
  "vtrust_avg": "0.35000000000000000000000000001",
  "vtrust_computed": true
}
```

**Все значения в примере вымышленные и относятся к учебной fixture, а не
к реальным данным Bittensor или Taostats.** `status: ok` означает успешную
проверку fixture. `netuid`, UID и блок выбраны для примера; `block` — номер
вымышленного блока, не timestamp. Из шести записей четыре проходят фильтр
`active=true AND validator_permit=true`; их VTrust усредняется с учётом нуля.
Этот demo вычисляет только VTrust; incentive доступен отдельно через
`analyze_metagraph(..., incentive_filters=...)` с явными boolean-фильтрами.

## Engineering Highlights

- Проверка полноты и порядка пагинации, totals и дубликатов UID.
- `Budget`, отдельный `LiveAllowance` и локальный `LocalRateLimiter` ограничивают
  попытки и частоту HTTP-вызовов; они не являются бюджетом API-кредитов.
- Явный live opt-in при создании транспорта и выполнении запросов;
  автоматических retries нет.
- `CompletedQuery` передаёт завершённую выборку без повторной пагинации;
  fail-closed обработка исключает выдачу частичного анализа при ошибке.
- Decimal без промежуточного float, проверка `netuid` и согласованности блоков.
  API VTrust использует независимый Decimal Context с precision не менее 50
  и `ROUND_HALF_EVEN`, без HTML-округления до 6 знаков.
- Воспроизводимые синтетические offline-тесты с блокировкой реальной сети.
- Атомарная локальная публикация версионированных CSV/JSON в отдельном HTML pipeline.

## Архитектура API v1 Metagraph

```text
Синтетические envelopes                 Явный live opt-in (по умолчанию выключен)
  complete_metagraph + Budget             fetch_metagraph → Transport / PageSession
                 \                       /  Budget + LiveAllowance + LocalRateLimiter
                  PageMerger: пагинация / totals / UID
                                  ↓
                            CompletedQuery
                                  ↓
                        analyze_metagraph
                                  ↓
             normalize_pages → VTrust / incentive по явным фильтрам
                                  ↓
                         MetagraphAnalysis
```

Offline demo использует левую ветку и печатает JSON-отчёт по VTrust.
Схема не включает HTML exporter или Sheets: интеграции с ними нет.
`CompletedQuery` подтверждает локальную проверку завершённости пагинации,
но не атомарность серверного снимка или соответствие метрик интерфейсу Taostats.

## Навигация по документации

| Документ | Содержание |
| --- | --- |
| [Metagraph demo](docs/taostats-metagraph-demo.md) | Запуск, вымышленная fixture, JSON, расчёт VTrust и ошибки |
| [API orchestration](docs/taostats-api-orchestration.md) | Ограниченная операция metagraph, offline/live пути и анализ |
| [API mapping](docs/taostats-api-mapping.md) | Нормализация, `CompletedQuery`, Decimal, фильтры и семантика метрик |
| [API transport](docs/taostats-api-transport.md) | Live opt-in, HTTP guards, бюджеты и локальные ограничения |
| [API research](docs/taostats-api-research.md) | Исторические наблюдения PR #5A, гипотезы и открытые вопросы; не текущий статус реализации |
| [HTML architecture](docs/architecture.md) | Локальный HTML pipeline, валидация и гарантии экспорта |
| [Google Sheets setup](docs/google-sheets-setup.md) | Настройка и явная публикация результатов HTML pipeline |

## Local HTML Pipeline: установка и запуск

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
python -m taostats_pipeline demo --strict --output ./output
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

`validate` разбирает и проверяет вход без экспорта. Команды выводят JSON-отчёт:
`status`, `errors`, `record_counts`, `missing_ids`, `duplicate_ids`, `unexpected_ids`,
`generation`. Exit code: 0 — успех, 1 — ошибка обработки/экспорта,
2 — некорректные аргументы CLI.

Следующие разделы о входе, схемах, валидации и экспорте относятся к HTML pipeline.

## HTML: вход и воспроизводимость

В каталоге входа нужен `manifest.json`:

```json
{
  "expected_ids": [7, 42],
  "strict_validation": false,
  "collected_at_utc": "2026-01-01T00:00:00Z",
  "snapshot": "subnets.html",
  "subnets": [
    {"netuid": 7, "metadata": "metadata-7.html", "metrics": ["metrics.html"], "expected_metric_pages": 1},
    {"netuid": 42, "metadata": "metadata-42.html", "metrics": ["metrics.html"]}
  ]
}
```

Пути должны указывать на файлы внутри входного каталога после разрешения symlinks.
URL не загружаются. Manifest явно связывает страницы метрик с `netuid`; ID из
metadata дополнительно сверяется с manifest. Несколько файлов `metrics`
агрегируются как страницы одной подсети. Опциональное поле `expected_metric_pages`
в записи подсети задаёт ожидаемое количество страниц: положительный integer,
без `null`, boolean и дробных чисел. Если длина списка `metrics` отличается,
весь экспорт блокируется; каждое указанное имя также должно быть читаемым файлом.
Без поля сохраняется прежнее поведение: принимается любое непустое число страниц.
Это проверка количества, а не уникальности/содержимого страниц: правильность
manifest и принадлежность HTML остаются ответственностью автора.
Demo использует фиксированное время:
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
В HTML pipeline VTrust усредняется и округляется до 6 знаков (Decimal half-even);
это отдельный расчёт, не API metagraph VTrust. Min/max incentive
сохраняют точность входа. Emission/price берутся из snapshot, дубли из metadata
не экспортируются. Порядковая колонка и график за 7 дней не входят в модели.

## Валидация и ошибки

- Непустые три набора; обязательные корректные `netuid` и UTC-время.
- Уникальность ID; точное совпадение с `expected_ids`.
- Missing/unexpected IDs и связность наборов, без предположения о `0..N−1`.
- Конечные Decimal; неотрицательные показатели, кроме знаковых изменений цены.
- Согласованные пары min/max incentive. Неизвестная структура строки, роли метрик
  или некорректное число — ошибка, а не пропуск строки или значение `ERROR`.

Строгий режим включается через `"strict_validation": true` на верхнем уровне
manifest либо `--strict` в любой команде CLI. `--no-strict` явно отключает его;
оба CLI-флага имеют приоритет над manifest. Без флагов используется manifest,
а при отсутствии настройки — обычный режим. Настройка manifest принимает только
JSON boolean. Python API: `run(..., strict=True)`, `validate(..., strict=True)`
и `export(..., strict=True)`; `run` с `strict=None` читает настройку manifest.

Точный список обязательных значений в строгом режиме (для каждой записи):

| Набор | Обязательные метрики |
| --- | --- |
| `subnets` | `emission`, `price` |
| `validator_metrics` | `vtrust_avg`, `incentive_orange_max`, `incentive_orange_min`, `incentive_green_max`, `incentive_green_min` |
| `metadata` | Дополнительных обязательных метрик нет |

`None` в этих полях становится ошибкой с ID и именем поля. Нулевое значение
допустимо. Остальные необязательные поля по-прежнему допускают `None`;
обычные проверки ID, времени и чисел действуют в обоих режимах. Проверка касается
итоговых агрегатов: она не доказывает наличие значения у каждого neuron.

Ошибки блокируют экспорт всех наборов. Файлы сначала пишутся во временный каталог
внутри output. После полной записи он переименовывается, затем атомарно заменяется
`current.json` через `os.replace`. До попытки публикации сбой сохраняет предыдущий
результат и позволяет удалить неопубликованное поколение. С момента начала замены
указателя готовое поколение больше не удаляется при ошибках: замена могла уже
выполниться, даже если вызов завершился исключением. `current.json` может указывать
на новое полное поколение при отчёте об ошибке; отката указателя нет. Предыдущее
поколение тоже сохраняется. При отказе до фактической замены может остаться готовый
каталог, на который current не ссылается. Эти сценарии покрыты regression-тестами.
Это атомарная публикация на локальной файловой системе без гарантии сохранности
при отключении питания; подробнее в архитектуре.

## Структура и проверки

```text
src/taostats_pipeline/
  api_v1.py                # Budget, envelopes, PageMerger, CompletedQuery
  api_v1_transport.py      # opt-in Transport, PageSession, allowance, limiter
  api_v1_mapping.py        # APIRecord, нормализация, VTrust и incentive
  api_v1_orchestration.py  # complete/fetch/analyze_metagraph, MetagraphAnalysis
  api_v1_demo.py           # отдельный synthetic offline module entry point
  models.py                # типизированные записи HTML pipeline
  parsers/                 # чистый разбор HTML и явная нормализация
  validation.py            # отчёт и целостность HTML-наборов
  pipeline.py              # локальный load → parse → normalize → validate → export
  exporters/
    __init__.py            # CSV/JSON и публикация поколения
    google_sheets.py       # явная публикация HTML-наборов
  cli.py                   # HTML: demo / run / validate / publish-sheets
  __main__.py              # module entry point HTML CLI
  fixtures/                # HTML, manifest и synthetic metagraph-demo.json
tests/                     # HTML/Sheets/API offline и characterization-тесты PR #1
docs/                      # API demo/research/transport/mapping/orchestration,
                           # HTML architecture и Google Sheets setup
```

Pytest блокирует TCP, UDP, DNS и дочерние процессы, включая end-to-end вызов demo.
Тесты PR #1 извлекают выбранные чистые функции через AST: тела старых модулей
не импортируются и не запускаются. Новые тесты прямо импортируют пакет.
Ruff проверяет пакет, тесты и конфигурацию. CI работает на pull requests,
без расписания и секретов.

## HTML → Google Sheets: только явная публикация

На Windows рекомендуется безопасная обёртка (Windows PowerShell 5.1 / PowerShell 7):

```powershell
.\scripts\publish_sheets.ps1
# Только после настройки внешнего JSON и отдельного allowlist:
.\scripts\publish_sheets.ps1 -Apply -ConfigPath $configPath -RunId demo-001
```

По умолчанию она выполняет только локальный dry-run на синтетических fixtures;
конфигурация и ключ не нужны. Apply требует явный RunId, отдельную переменную
`GOOGLE_ALLOWED_SPREADSHEET_ID`, существующий внешний ключ и точный ввод
`PUBLISH demo-001`. Python запускается через `py -m`; автоматического повтора нет.
Пошаговая [настройка Windows](docs/google-sheets-setup.md#windows-powershell-launcher)
включает формат JSON, проверку launcher и восстановление после ошибок.

```bash
python -m taostats_pipeline publish-sheets --input ./src/taostats_pipeline/fixtures --dry-run
```

Без `--apply` команда всегда работает локально, без credentials и Google-зависимостей.
Для записи установите `pip install -e '.[google]'`, задайте `GOOGLE_SPREADSHEET_ID`,
точно совпадающий `GOOGLE_ALLOWED_SPREADSHEET_ID` и `GOOGLE_CREDENTIALS_FILE`, подготовьте
четыре листа с заголовками и выполните:

```bash
python -m taostats_pipeline publish-sheets --input ./src/taostats_pipeline/fixtures --apply --run-id demo-001
```

Сначала проверяются данные и удалённая структура, затем записываются и проверяются
staging-листы. Три набора и строка `run_status` публикуются одним финальным batch;
после этого проверяется результат и удаляется staging. Все значения хранятся текстом
для сохранения точности Decimal и защиты от интерпретации строк как формул.
Повторный успешный `run_id` с тем же SHA не создаёт новую публикацию.
Он возвращает `already_published` и отдельно `current_data_status: matches/differs`:
исторический успех не означает актуальность старых данных после следующего запуска.
После подтверждённой публикации сбой очистки сохраняет `success` и добавляет
`cleanup_warning`. Проверка различает несоответствие данных и недоступность API;
числовые ячейки не приравниваются к текстовым. Перед batch повторно проверяется
структура, однако эти проверки не устраняют гонки без внешней блокировки.
`failed`/`partial` возвращают ненулевой exit code. Поддерживается один писатель;
при потере ответа исход может быть неопределённым, удалённый журнал не гарантирован
при недоступном API. Весь процесс не является транзакцией.

Пошаговая настройка, заголовки, ограничения и восстановление:
[docs/google-sheets-setup.md](docs/google-sheets-setup.md).
Существующие `demo`, `run`, `validate` не записывают в Sheets.

## Ограничения и следующие этапы

- **API v1:** metagraph orchestration ограничен одной подсетью, максимум двумя
  страницами и 400 фактическими записями. Live-план задаёт точное число страниц,
  без автоматического расширения. Live transport существует, но по умолчанию
  выключен; демонстрационный entry point работает только offline.
- Нет подтверждённого соответствия рассчитанных VTrust/incentive интерфейсу
  Taostats. Orange/green группы HTML не заполняются из API автоматически.
  Синтетические тесты не доказывают live-схему или атомарность данных сервера.
- Стоимость запросов в API-кредитах неизвестна: число HTTP-попыток не гарантирует
  число списанных кредитов или сохранность баланса. Limiter действует только
  в рамках одного экземпляра и не координирует другие процессы или клиентов.
- **HTML pipeline/demo:** используются локальные HTML, демонстрация — только
  на синтетических fixtures; актуальность селекторов живого сайта не проверяется.
  Не реализованы сбор HTML-страниц и автоматическое определение полноты их
  пагинации; manifest проверяет явно заданное количество файлов.
- Нет production deployment и автоматического массового сбора данных.
  Для HTML pipeline также нет локального постоянного журнала запусков и очистки
  поколений. CLI-отчёт описывает текущий запуск. При изменении HTML нужны изменения
  парсеров и тестов.

`src/main.py`, `src/subnets_full_parser.py`, `src/subnets_vtrust_incentive.py`
сохранены как legacy: **не запускайте их**. Они содержат внешние операции и могут
изменять таблицы; новый пакет их не использует. `requirements.txt` относится к legacy
и для demo не нужен. `.env.example` — образец переменных окружения; пакет её не читает.
Credentials, рабочие ресурсы и данные прежней команды не используются.

Отдельные offline-тесты обёртки: `python tests/launcher_checks.py`.
Они запускают PowerShell с подменёнными `py` и подтверждением, без Google API.
Основной pytest сохраняет запрет дочерних процессов. В Linux проверки могут
пропускаться без PowerShell; отдельный Windows job обязательно проверяет PowerShell
5.1 и 7. `python tests/launcher_checks.py --require-powershell` запрещает отсутствие
shell и пропуск всего набора (`LAUNCHER_TEST_SHELL` выбирает executable).
Обычный запуск обёртки выполняется без ExecutionPolicy Bypass; локальный smoke-test
с настоящим `py.exe` в режиме dry-run описан в setup docs.

Архитектура и границы гарантий описаны в [docs/architecture.md](docs/architecture.md).
