# Ingestion

## Supported input types

Implemented:

- Raw text JSON
- Markdown JSON
- Unstructured text JSON
- `.txt`
- `.md`
- `.docx`
- `.csv`
- `.xlsx`

Not implemented:

- PDF
- OCR
- image parsing

## Endpoints

- `POST /ingest/text`
- `POST /ingest/files`

## Normalized document model

All ingestion sources are converted into a common internal representation before chunking:

- `title`
- `source_type`
- `content`
- `metadata`
- `url`
- `original_filename`
- `mime_type`
- `sections`

## Parser behavior

### TXT

- UTF-8 text is decoded and stored as plain text

### Markdown

- Preserves heading structure in `sections` when headings are present
- Falls back to plain content chunking if needed

### DOCX

- Reads paragraphs with `python-docx`
- Uses paragraph styles containing `heading` to derive sections

### CSV

- Reads rows with Python `csv.DictReader`
- Converts each readable row into retrieval-friendly text
- Includes:
  - row number
  - column headers
  - column values

### XLSX

- Reads workbook sheets with `openpyxl`
- Converts each readable row into retrieval-friendly text
- Includes:
  - workbook filename
  - sheet name
  - row number
  - column headers
  - row values

## Metadata captured

Depending on source type, stored metadata may include:

- `title`
- `url`
- `source_type`
- `chunk_index`
- `original_filename`
- `mime_type`
- `section_title`
- `sheet_name`
- `row_start`
- `row_end`
- `column_headers`
- `tags`

## Batch behavior

`POST /ingest/files` is partial-success safe:

- one bad file does not fail the entire request
- unsupported file types return per-file errors
- empty files return per-file errors
- remaining files continue processing
- exact duplicate uploads are deduplicated by normalized content hash plus embedding profile, so the same knowledge base is not indexed twice for the same embedding setup

Multipart form notes:

- `tags` may be omitted
- blank `tags` values are treated as empty
- `tags` accepts JSON array form like `["a","b"]`
- `tags` also accepts plain strings like `demo` or `demo,portfolio`
- `metadata` may be omitted or blank
- `metadata` accepts a JSON object when provided
- plain metadata strings are stored as `raw_metadata`
- Swagger placeholder values like `string` on optional form fields are ignored

## Canonical embedding enforcement

The ingestion endpoints expose:

- `embedding_provider`
- `embedding_model`
- `embedding_profile`

In the current implementation these values must match a configured embedding profile.

The active profile comes from the model-selection record in PostgreSQL and must match one of the configured entries in `backend/app/core/defaults.py`.

If the profile uses a new embedding dimension, the app creates the matching Qdrant collection automatically on first use.
