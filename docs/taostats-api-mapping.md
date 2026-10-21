# Taostats API v1: offline mapping (PR #5D)

`taostats_pipeline.api_v1_mapping` — самостоятельный чистый модуль этого
публичного учебного проекта. Он не читает файлы или окружение, не импортирует
HTTP-транспорт, модели HTML pipeline, CLI или Google Sheets и не запускает I/O.
Входные данные передаются явно. Fixtures полностью синтетические и не являются
подтверждением live-схемы API.

## Вход и результат

`normalize_pages(endpoint, pages, *, netuid, expected_block=None,
loaded_at_utc=None)` принимает конечный непустой list/tuple декодированных
JSON envelopes одной выборки либо `CompletedQuery` (см. ниже).
Генераторы не принимаются. Пустой результат
представляется полной пустой страницей, а не отсутствием страниц.

Envelope, порядок, totals, полнота и дубликаты проверяются существующим
`PageMerger`/`parse_envelope`. Созданный внутри `Budget` ограничивает обработку
явно переданных страниц; его счётчики здесь не означают сетевые вызовы или кредиты.
Возврата частичного результата нет. Каждая строка обязана иметь nonnegative int
`netuid`, совпадающий с явно заданным значением; boolean вместо int запрещён.
Страницы должны относиться к одному набору фильтров. Envelope не содержит
доказательства происхождения/фильтров: вызывающий код отвечает за это условие.
Mapper не может обнаружить подмену страниц, если все проверяемые метаданные
подделаны или случайно совпадают.

Результат — `NormalizedBatch` с endpoint, netuid, block_number, временем загрузки
и tuple записей `APIRecord`. В каждой записи отдельно находятся netuid, uid,
block_number, исходный source_timestamp и неизменяемый словарь fields. В fields
используются исходные имена API и только наблюдавшиеся поля из research:

| Endpoint `/v1/subnets/...` | fields |
| --- | --- |
| identities | subnet_name, discord, github_repo |
| metrics | registration_timestamp, registration_cost, emission |
| pools | price, market_cap, liquidity, root_prop |
| pools/aggregate | sentiment, tao_volume_24_hr, alpha_volume_24_hr, price_change_1_hour/day/week/month |
| metagraph | active, validator_permit, validator_trust, incentive, is_immune, is_owner_hotkey, in_danger |
| metagraph/aggregate | max_incentive, max_immune_incentive, min_non_immune_incentive, immune_count |

Неизвестные поля игнорируются. Отсутствующие и null optional-поля дают `None`,
реальные нули сохраняются. Текстовые поля не преобразуются в URL и не открываются.
`sentiment` сохраняется как исходный scalar без приведения к строке или категории.

Для metagraph обязательны uid и block_number: nonnegative int, uid уникален,
все строки имеют один блок. Это строгий локальный контракт для вычислений,
а не доказательство серверной атомарности. Для остальных endpoints принят узкий
контракт одной строки на netuid; повтор subnet отклоняется. История и несколько
pools одной подсети требуют отдельно доказанного ключа и здесь не поддерживаются.
У не-metagraph endpoints блоки могут отсутствовать; неизвестный блок обозначается
`None`. Разные блоки и смешение известных/неизвестных блоков запрещены.
Если передан expected_block, каждая непустая строка должна подтвердить его.
У пустой выборки batch.block_number остаётся None: доказательства блока нет.
Для identities наличие timestamp/block_number не подтверждено; эти поля не
извлекаются, даже если встретились во входе.

## Decimal и локальная валидация

Числовые метрики принимают Decimal, int или десятичную строку; float и boolean
отклоняются. Промежуточного binary float и округления исходных Decimal нет.
Текстовый литерал ограничен 128 символами; явная степень -308..308 проверяется
до создания Decimal. У готового Decimal максимум 128 цифр коэффициента,
exponent tuple -436..308 и порядок ненулевого значения -308..308. Для int перед
Decimal проверяется bit_length <=425. NaN/Infinity отклоняются.
Это консервативные ресурсные границы проекта, не спецификация Taostats.

uid/netuid/блоки/immune_count неотрицательны; validator_trust и incentive, включая
наблюдавшиеся incentive-агрегаты, принимаются как неотрицательные величины.
Boolean-флаги принимаются только как настоящий bool. Это локальные требования,
типы и nullable-контракт API ещё не доказаны. Верхняя граница 1 для VTrust или
incentive не вводится. Знаки прочих величин не угадываются; отрицательные
price changes допустимы. Никаких процентов, валютных пересчётов или масштабирования.

## Время и семантика

- loaded_at_utc — только явно переданный aware datetime, нормализуемый в UTC.
  Без него результат содержит None; текущие часы модуль не читает.
- source_timestamp — исходный timestamp пяти наблюдавшихся endpoints.
- registration_timestamp сохраняется отдельно в fields как кандидат времени
  события регистрации, но его формат, timezone и единицы неизвестны.
- block_number — высота, не время блока. Поле времени блока не изобретается.

timestamp/registration_timestamp сохраняют исходный str/int/Decimal, без
предположений об epoch, секундах, миллисекундах или timezone. Числовые scalar
проверяются на конечность и ресурсные ограничения; bool/float/контейнеры запрещены.
Смысл source_timestamp как времени события либо блока не устанавливается.
Нет join между endpoints и утверждения об общей атомарности.

## Проектные вычисления отдельно от API fields

`vtrust_avg(pages, *, netuid, expected_block=None)` проверяет полную метаграфную
выборку и считает среднее validator_trust только для active=true AND
validator_permit=true, включая нули. Оба флага обязательны у каждой строки,
иначе классификация неоднозначна. У выбранных строк validator_trust обязателен;
пропуск не уменьшает знаменатель. Пустая выбранная группа даёт None.

Сумма Decimal вычисляется с precision не менее 50, расширенной по диапазону
порядков, дробных разрядов и количеству значений для точного сложения.
Деление выполняется с той же precision и ROUND_HALF_EVEN в независимом Context.
Бесконечные десятичные средние округляются явно; входные значения не округляются.
Это не HTML-округление до 6 знаков и не доказательство формулы сайта/агрегата API.

`incentive_bounds(pages, *, netuid, filters, expected_block=None)` возвращает
`IncentiveBounds(minimum, maximum, count)`. Обязателен непустой явный набор
boolean-фильтров по active/validator_permit/is_immune/is_owner_hotkey/in_danger;
все предикаты соединяются AND. Используемые флаги обязательны у всех строк,
incentive обязателен у выбранных. Пустая группа даёт None/None/0; нули участвуют.
UID 0, owner, dual-role, inactive и permit не исключаются автоматически.

**UI mapping не подтверждён.** Фильтр is_immune=true сам по себе не доказывает
группу IMMUNE MINERS/orange; is_immune=false не доказывает MINERS/green.
Поэтому incentive_orange_min/max и incentive_green_min/max автоматически не
заполняются, цветовых функций нет. Вычисления по явным фильтрам доступны как
низкоуровневый инструмент исследования. API max_incentive, max_immune_incentive,
min_non_immune_incentive и immune_count остаются извлечёнными fields и не заменяют
проектные вычисления.

Никакие существующие Metadata, SubnetSnapshot, ValidatorMetrics или форматы
экспорта не изменены и автоматически не заполняются. Семантика единиц всех
числовых API-полей, выбор TAO/alpha volume, network_key, временные форматы,
классификация miners/owner и соответствие API-агрегатов UI остаются открытыми
вопросами исследовательского документа.


## Публичная передача завершённой выборки (PR #5F)

`PageMerger.finish_query()` и `PageSession.finish_query()` возвращают
`CompletedQuery`: endpoint, total_items, total_pages и изолированные decoded
строки. Публичного конструктора из произвольных строк нет; объект создаётся
только после успешной общей проверки завершённости merger. Envelopes, HTTP-байты,
headers, ключ, Request/Response и URL с query в объект не добавляются.
Неизвестные поля внутри самих data сохраняются как прежде и остаются недоверенными.

```python
query = session.finish_query()
batch = normalize_pages("/v1/subnets/metagraph", query, netuid=7, expected_block=1000)
average = vtrust_avg(query, netuid=7, expected_block=1000)
bounds = incentive_bounds(query, netuid=7, filters={"is_immune": True})
```

Все три функции принимают как прежние envelopes, так и CompletedQuery.
Для нового типа проверяется совпадение endpoint; повторного создания PageMerger,
проверки пагинации и резервирования Budget нет. Все проверки содержимого
сохраняются: netuid, блок, optional/required поля, типы, Decimal и фильтры.
Mapper по-прежнему запрещает несколько строк одной подсети для не-metagraph,
даже если исходный merger использовал другие key_fields. CompletedQuery
подтверждает локальный контракт пагинации, а не нормализацию или атомарность API.
Отдельные вызовы mapper/метрик могут повторно проверять поля; страницы не проигрываются.

`finish()` сохраняет прежний list[dict] и поведение ошибок. Оба метода завершения
можно вызывать многократно и в любом порядке, пока сессия/merger здоровы, без
HTTP, ожидания limiter, чтения ключа и списаний Budget/LiveAllowance.
Каждый finish_query создаёт независимый snapshot. Его публичные metadata
неизменяемы; каждое обращение к rows возвращает новую глубокую копию, включая
вложенные dict/list. Входные ответы, старый finish() и другие snapshot не
разделяют доступное пользователю изменяемое состояние; Decimal не округляется.
Цена изоляции — дополнительная копия строк и O(N) на экспорт/чтение rows.

До первой страницы, при pending/неполной пагинации и после ошибок snapshot
не выдаётся. Раннее завершение сохраняет прежнее поведение: merger становится
failed, продолжение запрещено. Ошибки merger остаются ResponseError;
после неудачного fetch сессия выдаёт TransportError, как и раньше.
После полной выборки добавление страниц запрещено уже существующими guard:
попытка reserve/accept портит merger; неудачный fetch портит сессию.
Такой лишний fetch может израсходовать разрешение LiveAllowance до отказа
merger, но не открывает HTTP и не резервирует ещё один запрос/страницу в Budget.
После этого новые результаты запрещены, даже если ранее завершение было успешным.
Уже выданные snapshot остаются независимыми корректными снимками.

Интерфейс рассчитан на обычное использование Python, а не на враждебный код,
изменяющий private-поля или обходящий конструктор через object.__new__.
Общий Budget и прямое использование PageMerger не получают новых гарантий
межпоточной/межпроцессной координации. Сетевые и кредитные ограничения #5C
сохраняются; массовый обход и UI mapping этим интерфейсом не разрешаются.
