#!/usr/bin/env python3
"""Validate a filled Go documentation template."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import List, Dict, Set, Tuple

try:
    from go_template.generator import generate_documentation
except ModuleNotFoundError:
    from go_documentation.go_template.generator import generate_documentation  # type: ignore


# Regular expression for finding placeholders
PLACEHOLDER_PATTERN = re.compile(r"<[^>]+>")
FUNCTION_HEADER_PATTERN = re.compile(r"^## `func\s+(.+?)`$")
FUNCTION_HEADER_FALLBACK_PATTERN = re.compile(r"^### `func\s+(.+?)`$")
FIELD_HEADING_PATTERN = re.compile(r"^### (.+)$")
FIELD_PATTERN = re.compile(r"^- (.+?):\s*(.*)$")

NO_VALUE_MARKERS = {"нет", "<нет>"}
RELATION_FIELD_NAME = "Взаимосвязь с другими функциями файла"
RELATION_OTHER_FIELD_NAME = "Взаимосвязь с другими функциями из других файлов"
RELATION_FIELD_NAMES = {RELATION_FIELD_NAME, RELATION_OTHER_FIELD_NAME}


def generate_reference_template(go_file: Path) -> List[str]:
    """Create a fresh template for the provided Go file."""
    with tempfile.NamedTemporaryFile(suffix=".doc.md", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    try:
        # Silence informational logging emitted by generator
        root_logger.setLevel(max(previous_level, logging.WARNING))
        generate_documentation(go_file, tmp_path)
    finally:
        root_logger.setLevel(previous_level)
    content = tmp_path.read_text(encoding="utf-8").splitlines()
    tmp_path.unlink(missing_ok=True)
    return content


def extract_function_name(header: str) -> str:
    """Extract function name from header."""
    match = FUNCTION_HEADER_PATTERN.match(header)
    if match:
        return match.group(1).strip()
    match = FUNCTION_HEADER_FALLBACK_PATTERN.match(header)
    if match:
        return match.group(1).strip()
    return ""


def is_placeholder(text: str) -> bool:
    """Check if text is a placeholder."""
    return bool(PLACEHOLDER_PATTERN.search(text))


def normalize_marker_value(text: str) -> str:
    """Normalize text for comparison with no-value markers."""
    return text.strip().strip("`").lower()


def is_no_value(text: str) -> bool:
    """Check if text represents an explicit 'no value' marker."""
    return normalize_marker_value(text) in NO_VALUE_MARKERS


def is_empty_relation_value(text: str) -> bool:
    """Relations field may be empty, marked with dash, or an explicit no-value marker."""
    normalized = normalize_marker_value(text)
    return not normalized or normalized == "—" or normalized in NO_VALUE_MARKERS


def _normalize_relation_lines(value: str) -> List[str]:
    """Normalize relation field content for comparison."""
    if is_empty_relation_value(value):
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def find_extra_relation_lines(template_value: str, doc_value: str) -> List[str]:
    """Return lines that are present in the document but absent in the template relations block."""
    template_lines = _normalize_relation_lines(template_value)
    doc_lines = _normalize_relation_lines(doc_value)
    return [line for line in doc_lines if line not in template_lines]


def is_valid_description(text: str) -> bool:
    """Check that description is valid (not empty and not a placeholder)."""
    text = text.strip()
    if not text:
        return False
    # Allow special values
    if text == "—" or is_no_value(text):
        return True
    # Check that it's not a placeholder (except <нет>)
    if is_placeholder(text) and not is_no_value(text):
        return False
    return True


def parse_document(lines: List[str]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, int]], Dict[str, int]]:
    """Parse document and extract functions with their fields and line numbers."""
    functions: Dict[str, Dict[str, str]] = {}
    line_numbers: Dict[str, Dict[str, int]] = {}  # function_name -> {field_name: line_number}
    function_line_numbers: Dict[str, int] = {}  # function_name -> line_number
    current_function: str | None = None
    current_fields: Dict[str, str] = {}
    i = 0
    
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        line_num = i + 1  # 1-based line numbers
        
        # Check function header
        if line.startswith("## `func") or line.startswith("### `func"):
            # Save previous function if exists
            if current_function is not None:
                functions[current_function] = current_fields
            
            # Start new function
            current_function = extract_function_name(line)
            current_fields = {}
            function_line_numbers[current_function] = line_num
            line_numbers[current_function] = {}
            i += 1
            continue
        
        # Parse function fields
        if current_function is not None:
            heading_match = FIELD_HEADING_PATTERN.match(line)
            if heading_match:
                field_name = heading_match.group(1).strip()
                line_numbers[current_function][field_name] = line_num
                i += 1
                multiline_value = []
                while i < len(lines):
                    next_raw = lines[i]
                    next_line = next_raw.strip()
                    if not next_line:
                        i += 1
                        if multiline_value:
                            break
                        continue
                    if next_line.startswith("## `func") or next_line.startswith("### "):
                        break
                    multiline_value.append(next_line)
                    i += 1
                current_fields[field_name] = "\n".join(multiline_value) if multiline_value else ""
                continue
            match = FIELD_PATTERN.match(line)
            if match:
                field_name = match.group(1).strip()
                field_value = match.group(2).strip()
                line_has_trailing_colon = line.endswith(":")
                
                # Store line number for this field
                line_numbers[current_function][field_name] = line_num
                
                # Handle multiline fields
                if line_has_trailing_colon and not field_value.endswith("—"):
                    # Multiline field (e.g., "Взаимосвязь с другими функциями файла:")
                    i += 1
                    multiline_value = []
                    while i < len(lines):
                        raw_next_line = lines[i]
                        stripped_next_line = raw_next_line.strip()
                        if not stripped_next_line:
                            break
                        # Allow nested bullet points that are indented to belong to the current field
                        if raw_next_line.startswith("  ") or not stripped_next_line.startswith("- "):
                            multiline_value.append(stripped_next_line)
                            i += 1
                            continue
                        break
                    field_value = "\n".join(multiline_value) if multiline_value else "—"
                    current_fields[field_name] = field_value
                else:
                    current_fields[field_name] = field_value
            elif line.startswith("  "):
                # Continuation of multiline field
                if current_fields:
                    last_field = list(current_fields.keys())[-1]
                    current_fields[last_field] += "\n" + line.strip()
        
        i += 1
    
    # Save last function
    if current_function is not None:
        functions[current_function] = current_fields
    
    return functions, line_numbers, function_line_numbers


def validate_placeholders(template_lines: List[str], doc_lines: List[str]) -> List[str]:
    """Check that all placeholders are replaced."""
    issues: List[str] = []
    
    # Find all placeholders in document with their line numbers
    for line_num, line in enumerate(doc_lines, start=1):
        matches = PLACEHOLDER_PATTERN.finditer(line)
        for match in matches:
            placeholder = match.group()
            # Allow <нет> as valid value
            if placeholder.lower() == "<нет>":
                continue
            # All other placeholders (including <описание>) are considered errors
            issues.append(f"Line {line_num}: found unfilled placeholder: {placeholder}")
    
    return issues


def validate_functions(
    template_functions: Dict[str, Dict[str, str]],
    doc_functions: Dict[str, Dict[str, str]],
    doc_line_numbers: Dict[str, Dict[str, int]],
    doc_function_line_numbers: Dict[str, int],
    allow_partial: bool = False,
) -> List[str]:
    """Check that all functions are present and have all fields."""
    issues: List[str] = []
    
    # Check that all functions from template are present in document
    template_func_names = set(template_functions.keys())
    doc_func_names = set(doc_functions.keys())
    
    if not allow_partial:
        missing_functions = template_func_names - doc_func_names
        for func_name in missing_functions:
            issues.append(f"Function '{func_name}' is missing from the filled document")
    
    extra_functions = doc_func_names - template_func_names
    for func_name in extra_functions:
        line_num = doc_function_line_numbers.get(func_name, 0)
        if line_num:
            issues.append(f"Line {line_num}: function '{func_name}' is present in document but missing from template")
        else:
            issues.append(f"Function '{func_name}' is present in document but missing from template")
    
    # Check fields for each function
    for func_name in template_func_names & doc_func_names:
        template_fields = set(template_functions[func_name].keys())
        doc_fields = set(doc_functions[func_name].keys())
        
        missing_fields = template_fields - doc_fields
        for field_name in missing_fields:
            func_line = doc_function_line_numbers.get(func_name, 0)
            if func_line:
                issues.append(
                    f"Line {func_line}: function '{func_name}': missing field '{field_name}'"
                )
            else:
                issues.append(
                    f"Function '{func_name}': missing field '{field_name}'"
                )
        
        # Check that field values are filled (not placeholders)
        for field_name in template_fields & doc_fields:
            template_value = template_functions[func_name][field_name]
            doc_value = doc_functions[func_name][field_name]
            field_line = doc_line_numbers.get(func_name, {}).get(field_name, 0)

            # Detect extra lines in the relations-within-file block
            if field_name in RELATION_FIELD_NAMES:
                extra_lines = find_extra_relation_lines(template_value, doc_value)
                if extra_lines:
                    if field_line:
                        issues.append(
                            f"Line {field_line}: function '{func_name}', field '{field_name}': "
                            f"contains extra lines not in template: {', '.join(extra_lines)}"
                        )
                    else:
                        issues.append(
                            f"Function '{func_name}', field '{field_name}': "
                            f"contains extra lines not in template: {', '.join(extra_lines)}"
                        )
            
            # If template had a placeholder, check that it's replaced
            if is_placeholder(template_value):
                # Remove backticks for comparison
                template_clean = normalize_marker_value(template_value)
                doc_clean = normalize_marker_value(doc_value)
                
                # If template had <нет>, document should also have <нет>
                if template_clean in NO_VALUE_MARKERS:
                    if not is_no_value(doc_value):
                        if field_line:
                            issues.append(
                                f"Line {field_line}: function '{func_name}', field '{field_name}': "
                                f"expected 'нет', but found '{doc_value}'"
                            )
                        else:
                            issues.append(
                                f"Function '{func_name}', field '{field_name}': "
                                f"expected 'нет', but found '{doc_value}'"
                            )
                    continue
                
                # For other placeholders (e.g., <описание>) check that they're replaced
                if not is_valid_description(doc_value):
                    if field_line:
                        issues.append(
                            f"Line {field_line}: function '{func_name}', field '{field_name}': "
                            f"placeholder not replaced with description"
                        )
                    else:
                        issues.append(
                            f"Function '{func_name}', field '{field_name}': "
                            f"placeholder not replaced with description"
                        )
                elif is_placeholder(doc_value) and not is_no_value(doc_value):
                    if field_line:
                        issues.append(
                            f"Line {field_line}: function '{func_name}', field '{field_name}': "
                            f"still contains placeholder"
                        )
                    else:
                        issues.append(
                            f"Function '{func_name}', field '{field_name}': "
                            f"still contains placeholder"
                        )
    
    return issues


def validate_document(go_file: Path, doc_file: Path, allow_partial: bool = False) -> List[str]:
    """Validate filled template (or its fragment) and return discovered issues."""
    issues: List[str] = []
    
    # Generate reference template
    try:
        template_lines = generate_reference_template(go_file)
    except Exception as exc:
        return [f"Error generating template: {exc}"]
    
    # Read filled document
    try:
        doc_lines = doc_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [f"Documentation file '{doc_file}' not found"]
    except Exception as exc:
        return [f"Error reading documentation file: {exc}"]
    
    # 1. Check placeholders
    issues.extend(validate_placeholders(template_lines, doc_lines))
    
    # 2. Parse functions from template and document
    template_functions, _, _ = parse_document(template_lines)
    doc_functions, doc_line_numbers, doc_function_line_numbers = parse_document(doc_lines)
    
    # 3. Check functions and their fields
    issues.extend(
        validate_functions(
            template_functions,
            doc_functions,
            doc_line_numbers,
            doc_function_line_numbers,
            allow_partial=allow_partial,
        )
    )
    
    return issues


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a filled Go documentation template."
    )
    parser.add_argument(
        "go_file",
        type=Path,
        help="Path to the original .go file used to generate the template",
    )
    parser.add_argument(
        "doc_file",
        type=Path,
        help="Path to the completed documentation file",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging verbosity (default: WARNING)",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow validating a partial document (missing functions/sections are ignored).",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )
    
    issues = validate_document(args.go_file, args.doc_file, allow_partial=args.allow_partial)
    
    if issues:
        print("Template validation failed:", file=sys.stderr)
        for msg in issues:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    
    print("Template validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
