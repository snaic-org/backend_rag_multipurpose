from app.services import contentful

PROJECT_CFG = next(c for c in contentful.DEFAULT_CONTENT_TYPES if c["content_type"] == contentful.PROJECT_CONTENT_TYPE)


def _project(entry_id: str, title: str, status: str, industries: list[str], techs: list[str]) -> dict:
    return {
        "sys": {"id": entry_id},
        "fields": {
            "title": title,
            "summary": f"{title} summary.",
            "status": status,
            "industryTags": industries,
            "techTags": techs,
        },
    }


ENTRIES = [
    _project("a", "AIDISA: Situation Awareness", "Completed", ["TRANS"], ["NLP", "LLM"]),
    _project("b", "GENESIS: Safety Investigation", "Completed", ["TRANS"], ["RAG", "LLM"]),
    _project("c", "AutoVA: Visual Acuity Testing", "In Progress", ["HC"], ["ASR", "RE"]),
]


def test_project_document_shows_tag_names_not_codes() -> None:
    doc = contentful.entry_to_document(ENTRIES[2], {}, PROJECT_CFG, "https://www.snaic.net", "system")

    assert "Industries: Healthcare" in doc.content
    # "RE" means Real Estate for industries but Response Evaluation for technologies.
    assert "Technologies: Automatic Speech Recognition (ASR), Response Evaluation (RE)" in doc.content
    assert "IndustryTags" not in doc.content
    assert doc.url == "https://www.snaic.net/projects/c"


def test_project_roster_groups_projects_with_counts() -> None:
    roster = contentful.build_roster_document(PROJECT_CFG, ENTRIES, "https://www.snaic.net", "system")

    assert roster.title == "SNAIC Projects Directory"
    assert "The 3 SNAIC" in roster.content
    assert "a selection of SNAIC's work" in roster.content
    assert [section["heading"] for section in roster.sections][-4:] == [
        "All SNAIC projects",
        "SNAIC projects grouped by industry",
        "SNAIC projects grouped by technology",
        "SNAIC projects grouped by status",
    ]
    assert "- AIDISA: Situation Awareness - Status: Completed." in roster.content
    assert "- Transportation (2): AIDISA, GENESIS" in roster.content
    assert "- Healthcare (1): AutoVA" in roster.content
    assert "- Large Language Models (LLM) (2): AIDISA, GENESIS" in roster.content
    assert "- Completed (2): AIDISA, GENESIS" in roster.content
    assert "- In Progress (1): AutoVA" in roster.content


def test_unknown_tag_codes_are_kept() -> None:
    assert contentful._tag_names(["XYZ", " hc "], contentful.INDUSTRY_TAGS) == ["XYZ", "Healthcare"]
    assert contentful._tag_names(None, contentful.INDUSTRY_TAGS) == []
