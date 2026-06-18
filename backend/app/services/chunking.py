from app.core.config import Settings
from app.models.schemas import ChunkUpsert, NormalizedDocument


class ChunkingService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build_chunks(self, document: NormalizedDocument) -> list[dict]:
        source_type = document.source_type.lower()
        if source_type in {"csv", "xlsx"}:
            parts = self._split_text(document.content)
            if not parts:
                parts = [document.content]
            return [{"content": part, "metadata": dict(document.metadata)} for part in parts]

        if document.sections:
            chunks: list[dict] = []
            for section in document.sections:
                heading = str(section.get("heading", "")).strip() or document.title
                content = str(section.get("content", "")).strip()
                if not content:
                    continue

                for part in self._split_text(content):
                    metadata = dict(document.metadata)
                    metadata["section_title"] = heading
                    chunks.append(
                        {
                            "content": f"Title: {document.title}\nSection: {heading}\n{part}",
                            "metadata": metadata,
                        }
                    )
            if chunks:
                return chunks

        return [
            {"content": f"Title: {document.title}\n{part}", "metadata": dict(document.metadata)}
            for part in self._split_text(document.content)
        ]

    def build_chunk_upserts(
        self,
        document: NormalizedDocument,
        embeddings: list[list[float]],
    ) -> list[ChunkUpsert]:
        chunks = self.build_chunks(document)
        if len(chunks) != len(embeddings):
            raise ValueError("chunk and embedding counts do not match")

        upserts: list[ChunkUpsert] = []
        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            metadata = dict(chunk["metadata"])
            metadata.update(
                {
                    "title": document.title,
                    "url": document.url,
                    "source_type": document.source_type,
                    "chunk_index": index,
                    "original_filename": document.original_filename,
                    "mime_type": document.mime_type,
                }
            )
            upserts.append(
                ChunkUpsert(
                    chunk_index=index,
                    content=chunk["content"],
                    metadata=metadata,
                    embedding=embedding,
                )
            )
        return upserts

    def _split_text(self, text: str) -> list[str]:
        normalized = text.strip()
        if not normalized:
            return []

        chunk_size = self._settings.chunk_size
        overlap = self._settings.chunk_overlap
        chunks: list[str] = []
        start = 0

        while start < len(normalized):
            end = min(start + chunk_size, len(normalized))
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(end - overlap, start + 1)

        return chunks
