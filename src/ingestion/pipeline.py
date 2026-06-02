from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .chat_ingestor import ChatIngestor
from .git_ingestor import GitIngestor
from .issue_ingestor import IssueIngestor
from .log_ingestor import LogIngestor

logger = logging.getLogger(__name__)

# Standard locations for data files (inside project's .minidevagent/)
DATA_FILE_PATTERNS = {
    "commits": "commits.json",
    "issues": "issues.json",
    "chat_logs": "chat_logs.json",
    "deployment_logs": "deployment_logs.json",
}


class IngestionPipeline:
    """Orchestrates ingestion from all data sources into ColdMemory.

    Called once at session creation time. Discovers data files
    under .minidevagent/ and loads them into memory records.

    Usage:
        pipeline = IngestionPipeline()
        records = pipeline.ingest_all("/path/to/project")
        for r in records:
            cold_memory.records.append(r)
    """

    def __init__(self) -> None:
        self.git = GitIngestor()
        self.issues = IssueIngestor()
        self.chat = ChatIngestor()
        self.logs = LogIngestor()

    def ingest_all(self, project_path: str) -> dict[str, Any]:
        """Discover and ingest all available data sources.

        Returns:
            {
              "total_records": int,
              "by_source": {"commits": N, "issues": N, ...},
              "records": [...]
            }
        """
        data_dir = Path(project_path) / ".minidevagent"
        all_records: list[dict[str, Any]] = []
        by_source: dict[str, int] = {}

        # 1. Git commits (try live first, then static file)
        live_commits = self.git.ingest_live(project_path, max_commits=50)
        if live_commits:
            all_records.extend(live_commits)
            by_source["commits"] = len(live_commits)
            logger.info("Ingested %d live git commits", len(live_commits))
        else:
            commit_file = data_dir / DATA_FILE_PATTERNS["commits"]
            if commit_file.exists():
                records = self.git.ingest_file(str(commit_file))
                all_records.extend(records)
                by_source["commits"] = len(records)
                logger.info("Ingested %d commits from %s", len(records), commit_file)

        # 2. Issues
        issue_file = data_dir / DATA_FILE_PATTERNS["issues"]
        if issue_file.exists():
            records = self.issues.ingest_file(str(issue_file))
            all_records.extend(records)
            by_source["issues"] = len(records)
            logger.info("Ingested %d issues from %s", len(records), issue_file)

        # 3. Chat logs
        chat_file = data_dir / DATA_FILE_PATTERNS["chat_logs"]
        if chat_file.exists():
            records = self.chat.ingest_file(str(chat_file))
            all_records.extend(records)
            by_source["chat_logs"] = len(records)
            logger.info("Ingested %d chat messages from %s", len(records), chat_file)

        # 4. Deployment logs
        log_file = data_dir / DATA_FILE_PATTERNS["deployment_logs"]
        if log_file.exists():
            records = self.logs.ingest_file(str(log_file))
            all_records.extend(records)
            by_source["deployment_logs"] = len(records)
            logger.info("Ingested %d log entries from %s", len(records), log_file)

        return {
            "total_records": len(all_records),
            "by_source": by_source,
            "records": all_records,
        }

    def ingest_data_dir(self, data_dir: str) -> dict[str, Any]:
        """Ingest from a directory containing data JSON files."""
        return self.ingest_all(data_dir)
