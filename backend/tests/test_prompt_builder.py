from app.models.schemas import ChatMessage, RetrievedChunk
from app.services.prompt_builder import DEFAULT_SYSTEM_PROMPT, PromptBuilder


def test_prompt_builder_includes_grounding_and_citations() -> None:
    builder = PromptBuilder()
    chunks = [
        RetrievedChunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            document_id="22222222-2222-2222-2222-222222222222",
            title="Service Catalog",
            url="https://example.com/catalog",
            source_type="md",
            content="We offer AI chatbot implementation for SME customers.",
            metadata={"section_title": "Services"},
            similarity_score=0.92,
        )
    ]

    prompt = builder.build(
        user_message="What services do we offer?",
        chat_history=[ChatMessage(role="user", content="Hi")],
        retrieved_chunks=chunks,
        max_history_messages=8,
        max_context_chars=4000,
        max_context_tokens=1000,
        max_chunk_chars=2000,
    )

    system_content = prompt.messages[0].content
    assert system_content == DEFAULT_SYSTEM_PROMPT
    assert "You are the official SNAIC website assistant." in system_content
    assert "You answer questions exclusively about SNAIC using only the KNOWLEDGE BASE provided." in system_content
    assert "You may combine directly related facts that are explicitly listed" in system_content
    assert "When the user asks for an implication, benefit, relevance, fit, or likely role of something in SNAIC" in system_content
    assert "Reply as SNAIC Bot speaking directly to the person in the chat." in system_content
    assert "Do not say phrases like \"The user's question\"" in system_content
    assert "Do not refer to the person as \"the user\"." in system_content
    assert "- Do not end responses with emoji or celebratory symbols." in system_content
    assert prompt.messages[1].role == "assistant"
    assert prompt.messages[1].content == "Conversation history:\nHi"
    assert prompt.messages[2].content == "User question:\nWhat services do we offer?"
    assert prompt.messages[3].content.startswith("Retrieved context:\n")
    assert "We offer AI chatbot implementation for SME customers." in prompt.messages[3].content
    assert prompt.citations[0].title == "Service Catalog"


def test_prompt_builder_caps_context_budget() -> None:
    builder = PromptBuilder()
    chunks = [
        RetrievedChunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            document_id="22222222-2222-2222-2222-222222222222",
            title="Chunk A",
            url=None,
            source_type="text",
            content="A" * 5000,
            metadata={},
            similarity_score=0.9,
        ),
        RetrievedChunk(
            chunk_id="33333333-3333-3333-3333-333333333333",
            document_id="44444444-4444-4444-4444-444444444444",
            title="Chunk B",
            url=None,
            source_type="text",
            content="B" * 5000,
            metadata={},
            similarity_score=0.8,
        ),
    ]

    prompt = builder.build(
        user_message="Summarize the sources",
        chat_history=[],
        retrieved_chunks=chunks,
        max_history_messages=8,
        max_context_chars=800,
        max_context_tokens=200,
        max_chunk_chars=300,
    )

    assert len(prompt.messages[-1].content) < 2000
    assert "A" * 301 not in prompt.messages[-1].content
    assert "B" * 301 not in prompt.messages[-1].content


def test_prompt_builder_keeps_late_evidence_when_chunk_budget_allows() -> None:
    builder = PromptBuilder()
    late_evidence = ("Intro text. " * 80) + "Monash University"
    chunks = [
        RetrievedChunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            document_id="22222222-2222-2222-2222-222222222222",
            title="Partner Organisations",
            url=None,
            source_type="markdown",
            content=late_evidence,
            metadata={},
            similarity_score=0.9,
        ),
    ]

    prompt = builder.build(
        user_message="Is Monash University listed as a partner organisation?",
        chat_history=[],
        retrieved_chunks=chunks,
        max_history_messages=8,
        max_context_chars=8000,
        max_context_tokens=2500,
        max_chunk_chars=1800,
    )

    assert "Monash University" in prompt.messages[-1].content


def test_prompt_builder_uses_custom_system_prompt() -> None:
    builder = PromptBuilder()

    prompt = builder.build(
        user_message="Summarize the sources",
        chat_history=[],
        retrieved_chunks=[],
        max_history_messages=8,
        max_context_chars=800,
        max_context_tokens=200,
        max_chunk_chars=300,
        system_prompt="Custom system prompt",
    )

    assert prompt.messages[0].content == "Custom system prompt"


def test_prompt_builder_groups_chunks_by_document() -> None:
    builder = PromptBuilder()
    chunks = [
        RetrievedChunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            document_id="22222222-2222-2222-2222-222222222222",
            title="sample-0001-ctx-1",
            url="https://example.com/shared",
            source_type="text",
            content=(
                "Title: Real Article Title\n"
                "Source: TechCrunch\n"
                "Published At: 2023-10-07\n"
                "First excerpt from the same document."
            ),
            metadata={},
            similarity_score=0.93,
        ),
        RetrievedChunk(
            chunk_id="33333333-3333-3333-3333-333333333333",
            document_id="22222222-2222-2222-2222-222222222222",
            title="sample-0001-ctx-2",
            url="https://example.com/shared",
            source_type="text",
            content=(
                "Title: Real Article Title\n"
                "Source: TechCrunch\n"
                "Published At: 2023-10-07\n"
                "Second excerpt from the same document."
            ),
            metadata={},
            similarity_score=0.91,
        ),
        RetrievedChunk(
            chunk_id="44444444-4444-4444-4444-444444444444",
            document_id="55555555-5555-5555-5555-555555555555",
            title="sample-0002-ctx-1",
            url="https://example.com/other",
            source_type="text",
            content=(
                "Title: Other Article Title\n"
                "Source: The Verge\n"
                "Published At: 2023-11-01\n"
                "Evidence from another document."
            ),
            metadata={},
            similarity_score=0.89,
        ),
    ]

    prompt = builder.build(
        user_message="Compare the sources",
        chat_history=[],
        retrieved_chunks=chunks,
        max_history_messages=8,
        max_context_chars=4000,
        max_context_tokens=1000,
        max_chunk_chars=500,
    )

    context_prompt = prompt.messages[-1].content
    assert context_prompt.count("[Document") == 2
    assert "Source: Real Article Title" in context_prompt
    assert "Title: sample-0001-ctx-1" not in context_prompt
    assert "First excerpt from the same document." in context_prompt
    assert "Second excerpt from the same document." in context_prompt
    assert "Evidence from another document." in context_prompt
    assert "Publisher: TechCrunch" not in context_prompt
    assert "Published At: 2023-10-07" not in context_prompt
    assert "Retrieved Chunks:" not in context_prompt
    assert "Similarity:" not in context_prompt


def test_prompt_builder_does_not_add_binary_decision_check_for_yes_no_questions() -> None:
    builder = PromptBuilder()
    chunks = [
        RetrievedChunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            document_id="22222222-2222-2222-2222-222222222222",
            title="TechCrunch Article",
            url="https://example.com/techcrunch",
            source_type="text",
            content=(
                "Title: TechCrunch Article\n"
                "Source: TechCrunch\n"
                "Published At: 2023-10-07\n"
                "Software companies reported more revenue from payment models."
            ),
            metadata={},
            similarity_score=0.92,
        ),
        RetrievedChunk(
            chunk_id="33333333-3333-3333-3333-333333333333",
            document_id="44444444-4444-4444-4444-444444444444",
            title="Hacker News Article",
            url="https://example.com/hackernews",
            source_type="text",
            content=(
                "Title: Hacker News Article\n"
                "Source: Hacker News\n"
                "Published At: 2023-11-01\n"
                "The Epoch Times article described subscription growth."
            ),
            metadata={},
            similarity_score=0.9,
        ),
    ]

    prompt = builder.build(
        user_message=(
            "Do the TechCrunch article on software companies and the Hacker News article "
            "on The Epoch Times both report an increase in revenue?"
        ),
        chat_history=[],
        retrieved_chunks=chunks,
        max_history_messages=8,
        max_context_chars=4000,
        max_context_tokens=1000,
        max_chunk_chars=500,
    )

    context_prompt = prompt.messages[-1].content
    assert "BINARY DECISION CHECK" not in context_prompt
    assert "Question Clauses:" not in context_prompt


def test_prompt_builder_prefers_highest_similarity_excerpt_within_document() -> None:
    builder = PromptBuilder()
    chunks = [
        RetrievedChunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            document_id="22222222-2222-2222-2222-222222222222",
            title="snaic_overview",
            url=None,
            source_type="markdown",
            content="Title: snaic_overview\nLow-priority excerpt.",
            metadata={},
            similarity_score=0.2,
        ),
        RetrievedChunk(
            chunk_id="33333333-3333-3333-3333-333333333333",
            document_id="22222222-2222-2222-2222-222222222222",
            title="snaic_overview",
            url=None,
            source_type="markdown",
            content="Title: snaic_overview\nNanyang Technological University, Singapore",
            metadata={},
            similarity_score=0.9,
        ),
    ]

    prompt = builder.build(
        user_message="How is NTU involved with SNAIC?",
        chat_history=[],
        retrieved_chunks=chunks,
        max_history_messages=8,
        max_context_chars=4000,
        max_context_tokens=1000,
        max_chunk_chars=500,
    )

    context_prompt = prompt.messages[-1].content
    assert "Nanyang Technological University, Singapore" in context_prompt
