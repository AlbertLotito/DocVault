import re
from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Send messages to the LLM, return the response text."""
        ...

    def _extract_thinking(self, text: str) -> tuple[str, str | None]:
        """Extract <think>...</think> block. Returns (answer, thinking_or_None)."""
        match = re.search(r'<think>(.*?)</think>', text, re.DOTALL)
        if match:
            thinking = match.group(1).strip()
            answer = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            return answer, thinking
        return text.strip(), None

    def rag_query(self, question: str, chunks: list[str],
                  history: list[dict] | None = None) -> dict:
        """Build a RAG prompt from retrieved chunks and query the LLM."""
        history = history or []

        if chunks:
            context = "\n\n---\n\n".join(chunks)
            system_content = (
                "You are a precise document assistant having a conversation with the user. "
                "Answer using ONLY the document excerpts provided below and the conversation history. "
                "Do not use any outside knowledge. "
                "If the excerpts do not contain enough information to answer, "
                "say exactly: \"I don't have enough information in the indexed documents to answer that.\"\n\n"
                "Document excerpts:\n"
                "==================\n"
                + context +
                "\n==================\n"
                "Answer based solely on the excerpts and conversation history above."
            )
        else:
            system_content = (
                "You are a precise document assistant having a conversation with the user. "
                "No relevant document excerpts were found for the latest question. "
                "Answer based on the conversation history only. "
                "If you cannot answer, say so clearly."
            )

        messages = [{"role": "system", "content": system_content}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})

        raw = self.chat(messages)
        answer, thinking = self._extract_thinking(raw)
        return {'answer': answer, 'thinking': thinking}
