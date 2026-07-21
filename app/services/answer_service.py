"""
Minimal answer generator: concatenates retrieved excerpts and formats source citations.
"""


class AnswerService:
    def __init__(self):
        pass

    # Concatenate top retrieved excerpts into an answer string and build a source list.
    def generate(self, *, user_input: str, retrieved: list[dict]) -> tuple[str, list[dict]]:
        # Minimal answer generator: concatenate top excerpts and label sources
        if not retrieved:
            answer = "Sorry, can't find the source of information."
            return answer, []

        parts = []
        sources = []
        for r in retrieved[:5]:
            md = r.get("metadata", {})
            excerpt = r.get("document_text") or md.get("excerpt") or ""
            parts.append(excerpt)
            sources.append(
                {
                    "documentId": md.get("document_id"),
                    "documentTitle": md.get("document_title") or md.get("document_id"),
                    "versionId": md.get("document_version_id"),
                    "sectionPath": md.get("section_path"),
                    "relevance": r.get("relevance"),
                    "excerpt": excerpt,
                }
            )

        answer_text = "\n\n".join([p for p in parts if p])
        if not answer_text:
            answer_text = "Sorry, can find source but not content found."

        return answer_text, sources


# Module-level singleton; imported by the chat pipeline.
answer_service = AnswerService()
