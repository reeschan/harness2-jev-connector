# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0
"""A task connector that plugs jev-mcp into Harness²'s intake pipeline.

It receives PBI tasks from a jev-mcp server and runs them as connector tasks.
For each task it sends back the verified diff and the worker's report.
"""

from .connector import JevMcpConnector, JevVerifyJudge, connector_from_options

__all__ = ["JevMcpConnector", "JevVerifyJudge", "connector_from_options"]
__version__ = "0.1.0"
