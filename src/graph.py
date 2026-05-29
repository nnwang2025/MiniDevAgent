from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import langgraph
    from langgraph import Graph
    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover
    Graph = None
    LANGGRAPH_AVAILABLE = False


class AgentGraph:
    def __init__(self) -> None:
        self.graph: Any = None
        if LANGGRAPH_AVAILABLE:
            try:
                self.graph = Graph()
            except Exception as exc:
                logger.warning("LangGraph import succeeded but Graph creation failed: %s", exc)
                self.graph = None

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if self.graph is None:
            logger.info("Running fallback sequential agent flow.")
            return inputs
        logger.info("Running LangGraph flow.")
        return inputs
