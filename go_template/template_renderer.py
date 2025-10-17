from __future__ import annotations

import datetime
from pathlib import Path
from typing import List


def render_template(
    file_path: Path,
    types: List[str],
    consts: List[str],
    vars_: List[str],
    funcs: List[dict],
    internal_imports: List[str],
) -> str:
    today = datetime.date.today().strftime("%Y-%m-%d")
    try:
        rel_path = file_path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        rel_path = file_path.as_posix()

    lines: List[str] = []
    lines.append("# Название файла")
    lines.append(f"- Полный путь: `{rel_path}`")
    lines.append(f"- Дата/версия документации: `<{today} | vX.Y>`")
    lines.append("")
    lines.append("## Назначение файла")
    lines.append("- Общая роль в проекте: `<описание>`")
    lines.append("")
    lines.append("## Внутренняя структура")
    if types:
        lines.append("- Ключевые типы (структуры, интерфейсы, алиасы) и их задачи:")
        for name in types:
            lines.append(f"  - `{name}` — `<назначение>`")
    else:
        lines.append("- Ключевые типы (структуры, интерфейсы, алиасы) и их задачи: `<нет>`")
    if consts:
        lines.append("- Глобальные константы и их значение:")
        for name in consts:
            lines.append(f"  - `{name}` — `<назначение>`")
    else:
        lines.append("- Глобальные константы и их значение: `<нет>`")
    if vars_:
        lines.append("- Глобальные переменные и их значение:")
        for name in vars_:
            lines.append(f"  - `{name}` — `<назначение>`")
    else:
        lines.append("- Глобальные переменные и их значение: `<нет>`")
    lines.append("")
    lines.append("## Функции и методы")
    if funcs:
        lines.append("")
        for func in funcs:
            receiver_display = f"{func['receiver']} " if func.get("receiver") else ""
            param_display = func.get("params") or "—"
            return_display = func.get("returns") or "—"
            lines.append(f"### `func {receiver_display}{func.get('full_name', func.get('name', ''))}`")
            lines.append("- Назначение: `<описание>`")
            lines.append(f"- Входные данные: `{param_display}` — `<описание>`")
            lines.append(f"- Выходные данные: `{return_display}` — `<описание>`")
            lines.append("- Внутренние переменные: `<описание>`")
            lines.append("- Внутренняя логика: `<описание>`")
            same_rel = func.get("relationship_same_file", "—")
            other_rel = func.get("relationship_other_files", "—")
            lines.append(f"- Взаимосвязь с другими функциями файла: {same_rel}")
            lines.append(f"- Взаимосвязь с другими функциями из других файлов: {other_rel}")
            lines.append("- Связь с бизнес-процессом: `<описание>`")
            lines.append("- Предусловия: `<описание>`")
            lines.append("- Постусловия: `<описание>`")
            lines.append("")
    else:
        lines.append("`<Функции не обнаружены>`")
        lines.append("")

    lines.append("## Взаимодействие в проекте")
    if internal_imports:
        imports_str = ", ".join(f"`{imp}`" for imp in internal_imports)
        lines.append(f"- Внутренние пакеты и компоненты, с которыми связан файл: {imports_str}")
    else:
        lines.append("- Внутренние пакеты и компоненты, с которыми связан файл: `<нет>`")
    lines.append("- Кто вызывает этот файл/его функции: `<список>`")
    lines.append("- Какие модули/сервисы зависят от результатов: `<описание>`")

    return "\n".join(lines).strip() + "\n"
