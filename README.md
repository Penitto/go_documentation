# Документация генератора шаблонов для Go

## Описание

Этот проект содержит инструмент, который анализирует исходные файлы на Go и готовит Markdown‑шаблон для последующего заполнения документами или LLM. Шаблон включает сведения о структуре файла, глобальных объявлениях, взаимосвязях функций, а также о связях с другими пакетами проекта.

## Структура

- `generate_template.py` — CLI‑обёртка, принимающая путь к `.go` файлу и создающая шаблон.
- `go_template/parser.py` — утилиты для разбора исходников: поиск типов, констант, переменных, функций и импортов.
- `go_template/repository.py` — сбор индекса по всему модулю Go и построение графа вызовов.
- `go_template/template_renderer.py` — формирование Markdown‑шаблона.
- `go_template/generator.py` — координирует разбор файла, построение связей и запись результата.

## Требования

- Python 3.10+

## Использование

```bash
python3 generate_template.py path/to/file.go
```

По умолчанию шаблон располагается рядом с исходником и получает имя `<file>.doc.md`. Чтобы указать другой путь:

```bash
python3 generate_template.py path/to/file.go --out docs/file-doc.md
```

Чтобы увидеть диагностические сообщения (например, при проблемах с парсингом), можно включить логирование:

```bash
python3 generate_template.py --log-level DEBUG path/to/file.go
```

### Обход плейсхолдеров в готовом шаблоне

Когда шаблон уже создан, можно перечислить все `<<FILL ...>>` с номерами строк:

```bash
python3 iterate_template.py path/to/file.doc.md
```

Для машинной обработки есть JSON-вывод:

```bash
python3 iterate_template.py --json path/to/file.doc.md
```

### Поочередный обход функций с учётом правок

Скрипт `iterate_functions.py` возвращает следующую функцию в шаблоне и её диапазон строк,
перечитывая файл при каждом запуске, поэтому сдвиги строк после ручных правок учитываются:

```bash
python3 iterate_functions.py path/to/file.doc.md            # первая функция
python3 iterate_functions.py --after-line 42 path/to/file.doc.md  # следующая после 42-й строки
```

Итератор также включает блоки верхнего уровня: "Назначение файла" и "Внутренняя структура".

Можно сохранять курсор между запусками:

```bash
python3 iterate_functions.py --state-file .iter_state.json path/to/file.doc.md
# следующий вызов возьмёт курсор из файла состояния
python3 iterate_functions.py --state-file .iter_state.json path/to/file.doc.md
```

Для автоматизации есть JSON-вывод:

```bash
python3 iterate_functions.py --json --state-file .iter_state.json path/to/file.doc.md
```

Использовать как функцию (например, в своём пайплайне LLM):

```python
from pathlib import Path
from iterate_functions import next_function_segment, IteratorState

state = IteratorState()
while True:
    block, state = next_function_segment(Path("path/to/file.doc.md"), state)
    if not block:
        break
    # передайте блок.start_line и блок.length в LLM, затем снова вызовите next_function_segment
```

## Проверка заполненного шаблона

Когда документация готова, убедитесь, что подрядчик заполнил все поля и не изменил структуру:

```bash
python3 validate_template.py path/to/file.go docs/file.doc.md
```

Скрипт сравнит итоговый файл с эталонным шаблоном и сообщит, если остались заглушки (`<описание>`, `—` и т.д.) или были удалены обязательные блоки.

## Примечания

- При первом запуске для каждого файла выполняется сканирование всего Go-модуля, чтобы вычислить взаимосвязи между функциями. Повторные вызовы в рамках одного процесса можно ускорить кэшированием индекса.
- В `.gitignore` добавлен шаблон `*.go.md`, чтобы исключить сгенерированные шаблоны из индекса Git. Удалите правило, если хотите версионировать готовую документацию.
