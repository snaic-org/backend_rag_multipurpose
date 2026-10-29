"""Contentful CMS: fetch published entries and turn them into knowledge-base documents.

The SNAIC site (snaic.net) is rendered with JavaScript, so its content is read
from the Contentful Content Delivery API instead of being scraped.
"""

import httpx

from app.models.schemas import NormalizedDocument

CDN_BASE = "https://cdn.contentful.com"

# Default Contentful content types to ingest, in priority order.
# Each maps a Contentful content_type id to a human label, the public page
# it renders on (used as the citation URL), and whether entries have their
# own detail page (id appended to the url).
DEFAULT_CONTENT_TYPES = [
    {"content_type": "jointCentreProjectNew", "label": "Projects", "url_path": "/projects", "detail_page": True,
     "frame": "This is a SNAIC (SIT x NVIDIA AI Centre) research project.", "roster": True},
    {"content_type": "jointCentreNews", "label": "News", "url_path": "/news", "detail_page": True,
     "frame": "This is a SNAIC (SIT x NVIDIA AI Centre) news item or announcement.", "roster": True},
    {"content_type": "team", "label": "Our Team", "url_path": "/our-team", "detail_page": False,
     "frame": "This is a SNAIC (SIT x NVIDIA AI Centre) team member profile.", "roster": True},
]

# Contentful fields that add noise rather than signal for retrieval.
SKIP_FIELDS = {
    "profilepicture", "order", "image", "thumbnail", "featuredimage", "slug",
    "heroimage", "architectureimage", "screenshots",
}


def resolve_content_types(requested: list[str]) -> list[dict]:
    if not requested:
        return list(DEFAULT_CONTENT_TYPES)

    known = {c["content_type"]: c for c in DEFAULT_CONTENT_TYPES}
    resolved: list[dict] = []
    for content_type in requested:
        if content_type in known:
            resolved.append(known[content_type])
        else:
            resolved.append({
                "content_type": content_type,
                "label": content_type,
                "url_path": "",
                "detail_page": False,
            })
    return resolved


async def fetch_entries(
    client: httpx.AsyncClient,
    space: str,
    env: str,
    token: str,
    content_type: str,
) -> tuple[list[dict], dict]:
    url = f"{CDN_BASE}/spaces/{space}/environments/{env}/entries"
    all_items: list[dict] = []
    includes: dict = {"Entry": [], "Asset": []}
    skip = 0
    limit = 100

    while True:
        params = {
            "content_type": content_type,
            "access_token": token,
            "limit": limit,
            "skip": skip,
            "include": 1,
        }
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        items = data.get("items", [])
        all_items.extend(items)

        inc = data.get("includes", {})
        includes["Entry"].extend(inc.get("Entry", []))
        includes["Asset"].extend(inc.get("Asset", []))

        total = data.get("total", 0)
        skip += limit
        if not items or skip >= total:
            break

    return all_items, includes


def entry_to_document(
    entry: dict,
    includes: dict,
    cfg: dict,
    base: str,
    created_by: str,
) -> NormalizedDocument | None:
    fields = entry.get("fields", {})
    if not fields:
        return None

    entry_id = entry.get("sys", {}).get("id", "")
    content_type = cfg["content_type"]
    is_team = content_type == "team"

    # For people, the document title should be the person's NAME, not their
    # job title. For everything else the entry title/heading is correct.
    name = _field(fields, "name", "fullName")
    role = _field(fields, "title", "role", "position")
    member_type = _field(fields, "memberType", "category", "group")

    if is_team and name:
        title = name
    else:
        title = _entry_title(fields, cfg["label"])

    body = _entry_text(entry, includes)
    if not body.strip():
        return None

    # Prepend framing so every entry contains the "SNAIC" anchor and the key
    # descriptors people actually search for (name, role, member type).
    frame_lines = [cfg.get("frame", f"This is SNAIC {cfg['label']} content.")]
    if is_team:
        descriptor = ", ".join(part for part in (name, role) if part)
        if descriptor:
            frame_lines.append(f"{descriptor}.")
        if member_type:
            frame_lines.append(f"Member type at SNAIC: {member_type}.")
    header = " ".join(frame_lines)

    content = f"{header}\n\n{body}".strip()

    url = base + cfg.get("url_path", "")
    if cfg.get("detail_page") and entry_id:
        url = f"{url}/{entry_id}"

    metadata = {"source_kind": "cms", "title": title, "created_by": created_by}
    metadata["source_url"] = url
    metadata["contentful_content_type"] = content_type
    metadata["contentful_entry_id"] = entry_id
    if member_type:
        metadata["member_type"] = member_type

    return NormalizedDocument(
        title=title,
        source_type="cms",
        content=content,
        metadata=metadata,
        url=url,
    )


def build_roster_document(
    cfg: dict,
    entries: list[dict],
    base: str,
    created_by: str,
) -> NormalizedDocument | None:
    content_type = cfg["content_type"]
    url = base + cfg.get("url_path", "")

    if content_type == "team":
        # Group members by MemberType, listing leadership groups first.
        groups: dict[str, list[str]] = {}
        for entry in entries:
            fields = entry.get("fields", {})
            name = _field(fields, "name", "fullName") or "Unknown"
            role = _field(fields, "title", "role", "position")
            member_type = _field(fields, "memberType", "category", "group") or "Member"
            line = f"- {name}" + (f" - {role}" if role else "")
            groups.setdefault(member_type, []).append(line)

        def group_priority(member_type: str) -> tuple[int, str]:
            lowered = member_type.lower()
            if any(word in lowered for word in ("director", "lead", "head", "chair", "advis", "principal")):
                return (0, member_type)
            return (1, member_type)

        lines = [
            "SNAIC (SIT x NVIDIA AI Centre) - Team and Leadership Directory.",
            "The following people make up the SNAIC team, grouped by their role. "
            "Directors and leadership are listed first.",
            "",
        ]
        for member_type in sorted(groups, key=group_priority):
            heading = member_type if member_type.endswith("s") else f"{member_type}s"
            lines.append(f"{heading}:")
            lines.extend(groups[member_type])
            lines.append("")
        content = "\n".join(lines).strip()
        title = "SNAIC Team and Leadership Directory"
    else:
        entry_lines = []
        for entry in entries:
            fields = entry.get("fields", {})
            t = _entry_title(fields, "")
            if not t:
                continue
            summary = _field(fields, "summary", "brief", "description")
            if summary:
                summary = " ".join(summary.split())[:200]
                entry_lines.append(f"- {t}: {summary}")
            else:
                entry_lines.append(f"- {t}")
        if not entry_lines:
            return None
        content = "\n".join(
            [f"Complete list of all SNAIC (SIT x NVIDIA AI Centre) {cfg['label']}:", ""] + entry_lines
        ).strip()
        title = f"SNAIC {cfg['label']} Directory"

    metadata = {"source_kind": "cms", "title": title, "created_by": created_by}
    metadata["source_url"] = url
    metadata["contentful_content_type"] = content_type
    metadata["cms_roster"] = True

    return NormalizedDocument(
        title=title,
        source_type="cms",
        content=content,
        metadata=metadata,
        url=url,
    )


def _field(fields: dict, *names: str) -> str:
    """Case-insensitive lookup returning the first matching string field."""
    lowered = {k.lower(): v for k, v in fields.items()}
    for name in names:
        value = lowered.get(name.lower())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _entry_title(fields: dict, fallback: str) -> str:
    for key in ("title", "name", "heading", "projectTitle", "newsTitle", "fullName", "publicationTitle"):
        for actual_key, value in fields.items():
            if actual_key.lower() == key and isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def _entry_text(entry_or_asset: dict, includes: dict, depth: int = 0) -> str:
    fields = entry_or_asset.get("fields", {})
    lines: list[str] = []
    for key, value in fields.items():
        if key.lower() in SKIP_FIELDS:
            continue
        text = _field_to_text(value, includes, depth)
        if text and text.strip():
            label = key[0].upper() + key[1:]
            lines.append(f"{label}: {text.strip()}")
    return "\n".join(lines)


def _field_to_text(value, includes: dict, depth: int = 0) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_field_to_text(v, includes, depth) for v in value]
        return "\n".join(p for p in parts if p and p.strip())
    if isinstance(value, dict):
        # Contentful rich text document
        if value.get("nodeType") == "document":
            return _richtext_to_text(value)
        sys_info = value.get("sys", {})
        if sys_info.get("type") == "Link":
            if depth < 1:
                resolved = _resolve_link(sys_info, includes)
                if resolved is not None:
                    return _entry_text(resolved, includes, depth + 1)
            return ""
        # Plain JSON object field (e.g. a project's techStack) - flatten it.
        if not sys_info:
            parts = []
            for sub_key, sub_value in value.items():
                sub_text = _field_to_text(sub_value, includes, depth)
                if sub_text and sub_text.strip():
                    parts.append(f"{sub_key}: {sub_text.strip()}")
            return "; ".join(parts)
    return ""


def _resolve_link(link_sys: dict, includes: dict) -> dict | None:
    link_type = link_sys.get("linkType")
    link_id = link_sys.get("id")
    for item in includes.get(link_type, []):
        if item.get("sys", {}).get("id") == link_id:
            return item
    return None


def _richtext_to_text(node) -> str:
    """Recursively flatten a Contentful rich-text document node into plain text."""
    if not isinstance(node, dict):
        return ""
    if node.get("nodeType") == "text":
        return node.get("value", "")
    parts = [_richtext_to_text(child) for child in node.get("content", [])]
    return " ".join(p for p in parts if p).strip()
