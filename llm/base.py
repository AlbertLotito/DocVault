from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Send messages to the LLM, return the response text."""
        ...

    def rag_query(self, question: str, chunks: list[str]) -> str:
        """Build a RAG prompt from retrieved chunks and query the LLM."""
        context = "\n\n---\n\n".join(chunks)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. Answer the user's question "
                    "based only on the provided context. If the answer is not "
                    "in the context, say so.\n\nContext:\n" + context
                )
            },
            {"role": "user", "content": question}
        ]
        return self.chat(messages)
