import zipfile
from abc import ABC, abstractmethod
from io import BytesIO

from app.models.schemas import NormalizedDocument, ParsedFile

# DOCX/XLSX files are zip archives; a small upload can expand to gigabytes of
# XML when parsed ("zip bomb"), so the uncompressed size is checked first.
MAX_UNCOMPRESSED_ARCHIVE_BYTES = 200 * 1024 * 1024


def ensure_safe_archive(content: bytes, file_type: str) -> None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            uncompressed = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ValueError(f"file is not a valid {file_type} document") from exc
    if uncompressed > MAX_UNCOMPRESSED_ARCHIVE_BYTES:
        raise ValueError(
            f"{file_type} file expands to more than {MAX_UNCOMPRESSED_ARCHIVE_BYTES // (1024 * 1024)} MB and was not ingested"
        )


class FileParser(ABC):
    supported_types: tuple[str, ...]

    @abstractmethod
    async def parse(
        self,
        filename: str,
        content: bytes,
        mime_type: str | None,
        source_type_override: str | None = None,
        shared_metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> ParsedFile:
        raise NotImplementedError

    def build_document(
        self,
        *,
        title: str,
        source_type: str,
        content: str,
        metadata: dict,
        original_filename: str,
        mime_type: str | None,
        sections: list[dict] | None = None,
    ) -> NormalizedDocument:
        return NormalizedDocument(
            title=title,
            source_type=source_type,
            content=content,
            metadata=metadata,
            original_filename=original_filename,
            mime_type=mime_type,
            sections=sections or [],
        )
