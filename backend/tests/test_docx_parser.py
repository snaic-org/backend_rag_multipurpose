from __future__ import annotations

import asyncio


from app.parsers.docx_parser import DocxParser


class _FakeStyle:
    def __init__(self, name: str | None) -> None:
        self.name = name


class _FakeParagraph:
    def __init__(self, text: str, style_name: str | None = None) -> None:
        self.text = text
        self.style = _FakeStyle(style_name) if style_name is not None else None


class _FakeDocument:
    def __init__(self, paragraphs: list[_FakeParagraph]) -> None:
        self.paragraphs = paragraphs


def test_docx_parser_detects_plain_text_headings(monkeypatch) -> None:
    paragraphs = [
        _FakeParagraph("Introduction"),
        _FakeParagraph("The SIT Centre for AI offers comprehensive services."),
        _FakeParagraph("Collaboration Models with SNAIC"),
        _FakeParagraph("Step 1 PRE-ENGAGEMENT QUESTIONNAIRE"),
        _FakeParagraph("Begin by filling in this questionnaire."),
        _FakeParagraph("Step 2 1-1 CONSULTATION"),
        _FakeParagraph("Have an in-depth discussion about AI opportunities."),
    ]

    monkeypatch.setattr(
        "app.parsers.docx_parser.Document",
        lambda _bytes_io: _FakeDocument(paragraphs),
    )

    parser = DocxParser()
    parsed = asyncio.run(
        parser.parse(
            filename="sample.docx",
            content=b"ignored",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    )

    document = parsed.documents[0]
    headings = [section["heading"] for section in document.sections]

    assert "Introduction" in headings
    assert "Collaboration Models with SNAIC" in headings
    assert "Step 1 PRE-ENGAGEMENT QUESTIONNAIRE" in headings
    assert "Step 2 1-1 CONSULTATION" in headings
