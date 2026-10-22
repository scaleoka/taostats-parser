# Taostats API v1: bounded orchestration (PR #5G)

`taostats_pipeline.api_v1_orchestration` соединяет существующие публичные API
для одной операции metagraph. Endpoint фиксирован: `/v1/subnets/metagraph`.
Другие endpoints, обход подсетей, публикация, retries и фоновые задачи отсутствуют.
Новых зависимостей и изменений существующих API нет.

## Получение offline

```python
query = complete_metagraph(envelopes, budget=Budget(2, 2))
result = analyze_metagraph(query, netuid=7, expected_block=1000)
```

`complete_metagraph(envelopes, *, budget: Budget) -> CompletedQuery` принимает
только непустой материализованный list/tuple из одной или двух полных envelopes.
Полный пустой metagraph — одна пустая страница с корректной pagination.
JSON-объекты представлены обычными dict, data — list. Генераторов и callbacks нет.

До merger проверяются форма data, сумма фактических строк <=400 и единый
nonnegative integer netuid (0..65535, boolean запрещён). Эти проверки не доверяют
заявленному total_items и не копируют глубоко данные. Pagination, блоки и UID
проверяет настоящий PageMerger с переданным Budget. Другой Budget не создаётся.
Неуспех может израсходовать уже выполненные резервации; возврата частичного query нет.
LiveAllowance, Transport, credentials и часы offline-пути не нужны. Одна offline
страница может содержать до 400 строк; ограничение limit относится к live-запросу.

## Ограниченный live-план

`fetch_metagraph(transport, *, netuid, page_count, limit=1,
allow_live=False) -> CompletedQuery` отказывает без точного `allow_live is True`
до любого обращения к транспорту. Разрешённый Transport должен быть явно создан
вызывающим кодом; orchestration его не создаёт и не заменяет Budget, LiveAllowance
или LocalRateLimiter. Ключ передавать в функции нельзя.

До создания PageSession проверяются netuid, обязательный integer page_count
(только 1 или 2), integer limit (1..200), page_count * limit <=400 и тип Transport.
Затем выполняется ровно заданное число fetch_next_page(allow_live=True) и один
finish_query(). Новых резерваций поверх PageSession нет. Нельзя автоматически
пересоздавать Transport или allowance после отказа.

Это план точного числа страниц, а не поиск до next_page=null. Ранний finish_query
делает незавершённый merger failed, поэтому он не используется как проба.
Малый page_count приводит к отказу неполноты. Лишняя страница отклоняется
существующими guards/лимитами; HTTP сверх завершённой выборки не выполняется.
План не расширяется, limit не увеличивается и retry не делается.

После finish_query число фактически полученных записей проверяется по его
сводке: <=400, даже если сервер проигнорировал limit. При превышении rows
повторно не копируется. Для допустимого размера читается одна копия query.rows,
проверяется совпадение каждой строки с запрошенным netuid, затем выдаётся query.
Пустая выборка допустима, но не доказывает наличие subnet или блока.

Ограничение обнаруживает превышение после полного приёма заданного плана.
Первоначальные JSON-разбор и копии PageSession/CompletedQuery уже могли состояться;
они ограничены существующим максимумом HTTP-ответа, а не строгим лимитом памяти.
У HTTP-запросов неизвестна стоимость: два запроса не означают два кредита и не
гарантируют сохранность кредитного баланса. Limiter локален одному экземпляру;
другие процессы и клиенты с тем же ключом не координируются.

## Чистый анализ

```python
result = analyze_metagraph(
    query,
    netuid=7,
    expected_block=1000,
    compute_vtrust=True,
    incentive_filters={"is_immune": True},
)
```

`analyze_metagraph(query: CompletedQuery, *, netuid, expected_block=None,
loaded_at_utc=None, compute_vtrust=True, incentive_filters=None)` принимает только
CompletedQuery нужного endpoint, максимум двух страниц и 400 строк. netuid
проверяется явно. normalize_pages вызывается первым с netuid, expected_block и
loaded_at_utc. Затем существующий vtrust_avg вызывается при compute_vtrust=True;
incentive_bounds — только если incentive_filters не None. Пустые/невалидные
фильтры передаются существующей проверке и вызывают ошибку, а не пропуск расчёта.
Тип compute_vtrust — настоящий bool.

Все вычисления остаются в mapper; формулы не копируются. Decimal не проходит
через float, среднее сохраняет действующий Decimal Context/ROUND_HALF_EVEN.
Оба расчёта получают тот же netuid и expected_block. На непустом metagraph mapper
проверяет наличие общего блока; разные subnet/блоки и неполные метрики запрещены.
Без expected_block общий блок всё равно обязателен, но его значение не закреплено
вызывающей стороной. loaded_at_utc — только явное время загрузки, не время блока.

Результат — frozen dataclass MetagraphAnalysis с полями:

- batch: NormalizedBatch;
- vtrust_avg: Decimal | None;
- vtrust_computed: bool;
- incentive_bounds: IncentiveBounds | None.

vtrust_computed=False и vtrust_avg=None означают «не вычислялось»; True и None —
«расчёт выполнен, подходящая группа пуста». incentive_bounds=None означает
«не запрошено», а IncentiveBounds(None, None, 0) — рассчитанную пустую группу.
Raw query, Transport, HTTP-response, headers и credentials в результат не входят.
Исходные поля API остаются недоверенными данными. Incentive-фильтры задаёт и
сохраняет вызывающий код; соответствие групп orange/green не объявляется доказанным.

Нормализация и все запрошенные метрики должны успешно завершиться до создания
MetagraphAnalysis. Ошибки не превращаются в частично заполненный результат.
Существующие ResponseError/MappingError/BudgetError/TransportError проходят
вызывающему коду без добавления HTTP-контекста. Восстановления failed session нет.

CompletedQuery исключает повторную пагинацию и Budget при анализе. Однако каждая
метрика повторяет нормализацию и глубокую копию query.rows: с одним VTrust это
два прохода, с incentive — три. Ограничение 400 строк сдерживает объём; ненужные
метрики можно отключить. Нового batch-API, кеша и обхода private-state не вводится.

## Проверки

`tests/test_api_v1_orchestration.py` использует синтетические envelopes и HTTPS
на границе urllib, тестовый ключ-заглушку и управляемые часы. Общий conftest
блокирует реальные сокеты и subprocess. Проверяются лимиты, порядок стадий,
точность Decimal, отсутствие повторной пагинации, частичных результатов и
дополнительных запросов. Реальные Taostats/Google Sheets вызовы не нужны.
