from pathlib import Path

from app.parsers.base import FileParser
from app.parsers.csv_parser import CsvParser
from app.parsers.docx_parser import DocxParser
from app.parsers.markdown_parser import MarkdownParser
from app.parsers.text_parser import TextParser
from app.parsers.website_parser import WebsiteParser
from app.parsers.xlsx_parser import XlsxParser


class ParserFactory:
    def __init__(self) -> None:
        self._parsers: dict[str, FileParser] = {
            "txt": TextParser(),
            "md": MarkdownParser(),
            "docx": DocxParser(),
            "csv": CsvParser(),
            "xlsx": XlsxParser(),
            "website": WebsiteParser(),
        }

    def detect_type(self, filename: str, mime_type: str | None) -> str:
        suffix = Path(filename).suffix.lower().lstrip(".")
        if suffix in self._parsers:
            return suffix

        mime_map = {
            "text/plain": "txt",
            "text/markdown": "md",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "text/csv": "csv",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        }
        if mime_type in mime_map:
            return mime_map[mime_type]

        raise ValueError(f"Unsupported file type for '{filename}'")

    def get_parser(self, file_type: str) -> FileParser:
        try:
            return self._parsers[file_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported parser type '{file_type}'") from exc
