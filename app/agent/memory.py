"""Conversation memory for the AI agent."""

from typing import Any


def create_agent_memory(k: int = 5) -> Any:
    """Create a sliding-window conversation memory."""
    from langchain.memory import ConversationBufferWindowMemory

    return ConversationBufferWindowMemory(k=k, memory_key="chat_history", return_messages=True)
