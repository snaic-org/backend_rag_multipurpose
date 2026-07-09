# Ingestion

## Knowledge-base scoping (important)

Only ingest **SNAIC-specific** content. The chatbot answers strictly from what has been
ingested and faithfully relays retrieved text, so any non-SNAIC dataset in the knowledge
base will surface as SNAIC "fact". Generic SIT website scrapes and SIT admissions/finance
FAQs (e.g. `sit_sitescrapped_oct25.csv`, `oti_chatbot_faq_oct25.xlsx`) previously caused
the bot to quote SIT application fees and intake dates as SNAIC registration details.
Keep the knowledge base limited to SNAIC CMS content, the SNAIC overview/FAQ, and the
SNAIC website. Remove stray sources with `GET /admin/documents?source_type=` +
`DELETE /admin/documents/{id}`.

## Supported input types

Implemented:

- Text JSON with `title` and `content`
- `.txt`
- `.md`
- `.docx`
- `.csv`
- `.xlsx`
- website URLs (HTML scraped with BeautifulSoup4 + lxml)
- Contentful CMS entries (Content Delivery API)

Not implemented:

- PDF
- OCR
- image parsing
- JavaScript-rendered pages (use the CMS ingestion path instead)

## Endpoints

- `POST /ingest/text`
- `POST /ingest/files`
- `POST /ingest/websites` - scrape and ingest 1-50 URLs; partial-success safe with per-URL errors
- `POST /ingest/cms` - ingest published content from the Contentful Content Delivery API
- `DELETE /admin/documents/{id}` - delete a document and all its chunks (admin)
- `POST /admin/documents/{id}/reingest` - re-scrape and replace a website document (admin)
- `GET /admin/documents?source_type=` - list documents, optionally filtered by source type (admin)

### Website ingestion

- Fetches each URL (60s timeout, follows redirects) and extracts readable text
- Strips `script`, `style`, `nav`, `header`, `footer`, `aside`, `iframe`, `noscript`, `form`
- Extracts heading-anchored sections; rejects error pages, non-HTML content, and pages with insufficient text
- One failing URL does not abort the batch; each URL's outcome is reported individually
- `force_reingest` re-scrapes and replaces a previously ingested URL

### Contentful CMS ingestion

- Reads from `https://cdn.contentful.com` using `CONTENTFUL_SPACE_ID` and `CONTENTFUL_DELIVERY_TOKEN`
- Default content types: `team`, `jointCentreProjectNew` (Projects), `jointCentreNews` (News)
- Send an empty `content_types` list to ingest all defaults, or specific content-type ids to target
- Each entry is flattened (rich text, one-level links, nested objects such as project `techStack`); noise fields (images, `order`, `slug`, `screenshots`) are dropped
- Team profiles are titled by member name and framed with role and member type; a per-type roster document is emitted so aggregation questions resolve in one retrieval hit
- `force_reingest` wipes existing CMS documents first (full wipe on a full refresh, per-type wipe on a targeted refresh) so content and content-type changes do not leave duplicates

## Normalized document model

All ingestion sources are converted into a common internal representation before chunking:

- `title`
- `source_type`
- `content`
- `metadata`
- `url`
- `original_filename`
- `mime_type`
- `created_by`
- `created_at`
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
- `created_by`
- `source_kind`
- `source_type`
- `chunk_index`
- `original_filename`
- `mime_type`
- `section_title`
- `sheet_name`
- `row_start`
- `row_end`
- `column_headers`
- `source_url` (website and CMS)
- `contentful_content_type`, `contentful_entry_id`, `member_type`, `cms_roster` (CMS)

## Batch behavior

`POST /ingest/files` is partial-success safe:

- one bad file does not fail the entire request
- unsupported file types return per-file errors
- empty files return per-file errors
- remaining files continue processing
- exact duplicate uploads are deduplicated by normalized content hash plus embedding profile, so the same knowledge base is not indexed twice for the same embedding setup

Client payload notes:

- `POST /ingest/text` accepts only `items[].title` and `items[].content`
- `POST /ingest/files` accepts only uploaded files
- source type, file name, MIME type, metadata, `created_by`, and `created_at` are populated by the backend

## Canonical embedding enforcement

The active embedding profile comes from the model-selection record in PostgreSQL and must match one of the configured entries in `backend/app/core/config.py`.

If the profile uses a new embedding dimension, the app creates the matching Qdrant collection automatically on first use.
