from __future__ import annotations

import re

from app.models.schemas import ParsedFile
from app.parsers.base import FileParser

_MIN_CONTENT_CHARS = 100

_ERROR_TITLE_PATTERNS = [
    r"^404\b",
    r"^403\b",
    r"^401\b",
    r"^500\b",
    r"\bnot found\b",
    r"\baccess denied\b",
    r"\bforbidden\b",
    r"\bpage not found\b",
    r"\bunauthorized\b",
    r"\bservice unavailable\b",
]
_ERROR_TITLE_RE = re.compile("|".join(_ERROR_TITLE_PATTERNS), re.IGNORECASE)


class WebsiteParser(FileParser):
    supported_types = ("website",)

    async def parse(
        self,
        filename: str,
        content: bytes,
        mime_type: str | None,
        source_type_override: str | None = None,
        shared_metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> ParsedFile:
        from bs4 import BeautifulSoup

        # Reject non-HTML content types up front
        if mime_type and not any(t in mime_type for t in ("html", "text", "xml")):
            raise ValueError(
                f"URL returned non-HTML content ({mime_type}). "
                "Only HTML pages can be scraped."
            )

        soup = BeautifulSoup(content, "lxml")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript", "form"]):
            tag.decompose()

        title_tag = soup.find("title")
        h1_tag = soup.find("h1")
        page_title = (
            (title_tag.get_text(strip=True) if title_tag else None)
            or (h1_tag.get_text(strip=True) if h1_tag else None)
            or filename
        )

        # Detect error pages by title
        if _ERROR_TITLE_RE.search(page_title):
            raise ValueError(
                f"Page appears to be an error page (title: {page_title!r}). "
                "Check the URL is correct and publicly accessible."
            )

        metadata = dict(shared_metadata or {})
        metadata["source_url"] = filename
        metadata["source_kind"] = "website"

        body = soup.find("body") or soup
        sections = self._extract_sections(body, page_title)
        full_text = "\n\n".join(
            f"{s['heading']}\n{s['content']}" if s["heading"] != page_title else s["content"]
            for s in sections
        ) if sections else body.get_text(separator="\n", strip=True)
        full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()

        # Reject pages with too little content to be useful
        if len(full_text) < _MIN_CONTENT_CHARS:
            raise ValueError(
                f"Page has insufficient content after extraction ({len(full_text)} characters). "
                "The page may be behind a login wall, use JavaScript rendering, or be empty."
            )

        document = self.build_document(
            title=page_title,
            source_type="website",
            content=full_text,
            metadata=metadata,
            original_filename=filename,
            mime_type=mime_type or "text/html",
            sections=sections or None,
        )

        return ParsedFile(
            filename=filename,
            detected_type="website",
            documents=[document],
        )

    def _extract_sections(self, body, default_heading: str) -> list[dict]:
        HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
        CONTENT_TAGS = {"p", "li", "td", "th", "dt", "dd", "blockquote", "figcaption"}

        sections: list[dict] = []
        state = {"heading": default_heading}
        text_parts: list[str] = []

        def flush() -> None:
            combined = re.sub(r'\s+', ' ', ' '.join(text_parts)).strip()
            if combined:
                sections.append({"heading": state["heading"], "content": combined})
            text_parts.clear()

        for el in body.find_all(list(HEADING_TAGS | CONTENT_TAGS)):
            if el.name in HEADING_TAGS:
                flush()
                state["heading"] = el.get_text(strip=True)
            else:
                if el.find_parent(CONTENT_TAGS):
                    continue
                text = el.get_text(separator=" ", strip=True)
                if text:
                    text_parts.append(text)

        flush()
        return sections
