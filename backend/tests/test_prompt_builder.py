from app.models.schemas import ChatMessage, RetrievedChunk
from app.services.prompt_builder import (
    BINARY_ADJUDICATION_SYSTEM_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    PromptBuilder,
)


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

    assert prompt.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert "You are the official SNAIC website assistant." in prompt.system_prompt
    assert "IDENTITY & CONSTRAINTS" in prompt.system_prompt
    assert "KNOWLEDGE BASE" in prompt.system_prompt
    assert "USER QUESTION" in prompt.system_prompt
    assert "Do not use emoji anywhere in the response." in prompt.system_prompt
    assert "For questions about change, consistency, difference, portrayal, comparison" in prompt.system_prompt
    assert "KNOWLEDGE BASE" in prompt.messages[-1].content
    assert "<kb>" in prompt.messages[-1].content
    assert "What services do we offer?" in prompt.messages[-1].content
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

    assert prompt.system_prompt == "Custom system prompt"
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

    user_prompt = prompt.messages[-1].content
    assert user_prompt.count("[Document") == 2
    assert "Retrieved Chunks: 2" in user_prompt
    assert "Title: Real Article Title" in user_prompt
    assert "Publisher: TechCrunch" in user_prompt
    assert "Published At: 2023-10-07" in user_prompt
    assert "Title: sample-0001-ctx-1" not in user_prompt
    assert "First excerpt from the same document." in user_prompt
    assert "Second excerpt from the same document." in user_prompt
    assert "Evidence from another document." in user_prompt


def test_prompt_builder_adds_binary_decision_check_for_yes_no_questions() -> None:
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

    user_prompt = prompt.messages[-1].content
    assert "BINARY DECISION CHECK" in user_prompt
    assert "Answer `Yes.` only if every required part is supported" in user_prompt
    assert "Question Clauses:" in user_prompt


def test_prompt_builder_selects_generation_subset_for_binary_questions() -> None:
    builder = PromptBuilder()
    chunks = [
        RetrievedChunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            document_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="Noise Article 1",
            url="https://example.com/noise-1",
            source_type="text",
            content=(
                "Title: Noise Article 1\n"
                "Source: Wired\n"
                "Published At: 2023-10-08\n"
                "A noisy article with weak overlap."
            ),
            metadata={},
            similarity_score=0.95,
        ),
        RetrievedChunk(
            chunk_id="22222222-2222-2222-2222-222222222222",
            document_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            title="TechCrunch Flexport Article",
            url="https://example.com/tc",
            source_type="text",
            content=(
                "Title: TechCrunch Flexport Article\n"
                "Source: TechCrunch\n"
                "Published At: 2023-10-07\n"
                "Dave Clark discussed Flexport strategy changes."
            ),
            metadata={},
            similarity_score=0.87,
        ),
        RetrievedChunk(
            chunk_id="33333333-3333-3333-3333-333333333333",
            document_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            title="The Verge Google Article",
            url="https://example.com/verge",
            source_type="text",
            content=(
                "Title: The Verge Google Article\n"
                "Source: The Verge\n"
                "Published At: 2023-11-01\n"
                "Coverage of Google's influence on the internet's appearance."
            ),
            metadata={},
            similarity_score=0.86,
        ),
        RetrievedChunk(
            chunk_id="44444444-4444-4444-4444-444444444444",
            document_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
            title="Noise Article 2",
            url="https://example.com/noise-2",
            source_type="text",
            content=(
                "Title: Noise Article 2\n"
                "Source: Fortune\n"
                "Published At: 2023-09-20\n"
                "Another noisy article."
            ),
            metadata={},
            similarity_score=0.84,
        ),
        RetrievedChunk(
            chunk_id="55555555-5555-5555-5555-555555555555",
            document_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
            title="Noise Article 3",
            url="https://example.com/noise-3",
            source_type="text",
            content=(
                "Title: Noise Article 3\n"
                "Source: Science News For Students\n"
                "Published At: 2023-09-22\n"
                "Background article with weak overlap."
            ),
            metadata={},
            similarity_score=0.83,
        ),
    ]

    selected = builder.select_generation_chunks(
        user_message=(
            "Did the TechCrunch report on October 7, 2023 and the The Verge report on "
            "November 1, 2023 describe different aspects of Google's market influence?"
        ),
        retrieved_chunks=chunks,
    )

    selected_document_ids = {str(chunk.document_id) for chunk in selected}
    assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in selected_document_ids
    assert "cccccccc-cccc-cccc-cccc-cccccccccccc" in selected_document_ids
    assert len(selected_document_ids) == 2


def test_prompt_builder_builds_binary_adjudication_messages() -> None:
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
                "Dave Clark discussed Flexport strategy changes."
            ),
            metadata={},
            similarity_score=0.92,
        )
    ]

    messages = builder.build_binary_adjudication_messages(
        user_message="Did the TechCrunch report on October 7, 2023 describe a strategy change?",
        retrieved_chunks=chunks,
        max_context_chars=4000,
        max_context_tokens=1000,
        max_chunk_chars=500,
    )

    assert messages[0].content == BINARY_ADJUDICATION_SYSTEM_PROMPT
    assert 'Return JSON only: {"answer":"Yes|No|Insufficient","reason":"one short sentence"}' in messages[1].content
    assert "DECISION TASK" in messages[1].content


def test_prompt_builder_adds_relation_guidance_for_difference_questions() -> None:
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
                "The company shifted toward ad revenue."
            ),
            metadata={},
            similarity_score=0.92,
        )
    ]

    messages = builder.build_binary_adjudication_messages(
        user_message=(
            "Does the TechCrunch article indicate a different monetization strategy compared to another report?"
        ),
        retrieved_chunks=chunks,
        max_context_chars=4000,
        max_context_tokens=1000,
        max_chunk_chars=500,
    )

    assert "QUESTION RELATION" in messages[1].content
    assert "difference or change questions" in messages[1].content
