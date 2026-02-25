from __future__ import annotations

import os
import re


ANCHOR_STYLE_ENV = "GO_DOC_ANCHOR_STYLE"
ANCHOR_STYLE_BITBUCKET = "bitbucket"
ANCHOR_STYLE_COMMONMARK = "commonmark"


def resolve_anchor_style(raw: str | None = None) -> str:
    value = (raw if raw is not None else os.getenv(ANCHOR_STYLE_ENV, "")).strip().lower()
    if value in {"commonmark", "standard", "vscode"}:
        return ANCHOR_STYLE_COMMONMARK
    return ANCHOR_STYLE_BITBUCKET


def slugify_anchor_text(text: str, style: str | None = None) -> str:
    style = resolve_anchor_style(style)
    text = text.replace("`", "").strip().lower()
    if style == ANCHOR_STYLE_BITBUCKET:
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"[^a-z0-9-]", "", text)
    else:
        text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "func"


def function_anchor_fragment(name: str, style: str | None = None) -> str:
    style = resolve_anchor_style(style)
    slug = slugify_anchor_text(name, style)
    if style == ANCHOR_STYLE_BITBUCKET:
        return f"markdown-header-func-{slug}"
    return f"func-{slug}"


def header_anchor_fragment(header_text: str, style: str | None = None) -> str:
    style = resolve_anchor_style(style)
    slug = slugify_anchor_text(header_text, style)
    if style == ANCHOR_STYLE_BITBUCKET:
        return f"markdown-header-{slug}"
    return slug
