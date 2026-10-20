"""LangChain ReAct agent with local HuggingFace model."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agent.memory import create_agent_memory
from app.agent.tools import analyze_text, compare_texts, search_similar_documents, search_web_sources
from app.config import settings

logger = logging.getLogger(__name__)

REACT_PROMPT = """You are a plagiarism analysis assistant. Answer concisely using tools when needed.

Tools: {tools}
Tool names: {tool_names}

Question: {input}
{agent_scratchpad}"""


class PlagiarismAgent:
    """Wraps LangChain agent with session-aware tools."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.memory = None
        self._executor = None

    def _build_tools(self) -> list[Any]:
        from langchain.tools import Tool

        session = self.session

        return [
            Tool(
                name="analyze_text",
                func=lambda text: str(analyze_text(text)),
                description="Analyze text: word count, language, key sentences.",
            ),
            Tool(
                name="search_similar_documents",
                func=lambda text: str(search_similar_documents(session, text)),
                description="Find similar documents in the database.",
            ),
            Tool(
                name="compare_texts",
                func=lambda payload: str(
                    compare_texts(*[p.strip() for p in payload.split("|||", 1)])
                ),
                description="Compare two texts separated by |||",
            ),
            Tool(
                name="search_web_sources",
                func=lambda query: str(search_web_sources(query)),
                description="Search the web for potential sources.",
            ),
        ]

    def _build_executor(self):
        try:
            from langchain.agents import AgentExecutor, create_react_agent
            from langchain_community.llms import HuggingFacePipeline
            from langchain_core.prompts import PromptTemplate
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError(
                "LangChain agent requires: pip install langchain langchain-community transformers torch"
            ) from exc

        logger.info("Loading local LLM: %s", settings.llm_model)
        llm_pipeline = pipeline(
            "text2text-generation",
            model=settings.llm_model,
            max_new_tokens=256,
        )
        llm = HuggingFacePipeline(pipeline=llm_pipeline)
        tools = self._build_tools()
        prompt = PromptTemplate.from_template(REACT_PROMPT)
        agent = create_react_agent(llm, tools, prompt)
        if self.memory is None:
            self.memory = create_agent_memory()
        return AgentExecutor(
            agent=agent,
            tools=tools,
            memory=self.memory,
            verbose=False,
            handle_parsing_errors=True,
            max_iterations=5,
        )

    @property
    def executor(self):
        if self._executor is None:
            self._executor = self._build_executor()
        return self._executor

    def chat(self, message: str, document_text: str | None = None) -> dict[str, Any]:
        """Send a message to the agent and return the reply."""
        enriched = message
        if document_text:
            enriched = f"{message}\n\nDocument excerpt:\n{document_text[:2000]}"

        try:
            result = self.executor.invoke({"input": enriched})
            return {"reply": result.get("output", ""), "tools_used": []}
        except Exception as exc:
            logger.error("Agent error: %s", exc)
            fallback = self._fallback_response(message, document_text)
            return {"reply": fallback, "tools_used": ["fallback"]}

    def _fallback_response(self, message: str, document_text: str | None) -> str:
        """Deterministic response when LLM agent is unavailable."""
        if document_text:
            analysis = analyze_text(document_text)
            try:
                matches = search_similar_documents(self.session, document_text, top_k=3)
            except RuntimeError:
                matches = []
            return (
                f"Analysis: {analysis['word_count']} words, language={analysis['language']}. "
                f"Similar documents found: {len(matches)}. "
                f"Your question: {message}"
            )
        return f"I can help analyze documents for plagiarism. You asked: {message}"


_agent_cache: dict[int, PlagiarismAgent] = {}


def get_agent(session: Session) -> PlagiarismAgent:
    """Return session-scoped agent instance."""
    key = id(session)
    if key not in _agent_cache:
        _agent_cache[key] = PlagiarismAgent(session)
    return _agent_cache[key]
