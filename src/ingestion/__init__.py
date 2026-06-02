# Ingestion layer for multi-source data
#
# MiniDevAgent ingests heterogeneous data sources to build
# a team's long-term memory:
#
#   GitIngestor    → commits, authors, timestamps, changed files
#   IssueIngestor  → bug reports, resolutions, participants
#   ChatIngestor   → team discussions, decisions, context
#   LogIngestor    → deployment logs, error traces, runtime events
#
# All ingestors produce normalized memory records consumable
# by ColdMemory, enabling cross-source hybrid retrieval.

from .git_ingestor import GitIngestor
from .issue_ingestor import IssueIngestor
from .chat_ingestor import ChatIngestor
from .log_ingestor import LogIngestor
from .pipeline import IngestionPipeline

__all__ = [
    "GitIngestor",
    "IssueIngestor",
    "ChatIngestor",
    "LogIngestor",
    "IngestionPipeline",
]
