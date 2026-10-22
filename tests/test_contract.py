# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0

"""JevMcpConnector through the shared task connector contract tests."""

import unittest
from pathlib import Path

from harness2.testing.connector_contract import ConnectorContractTests

from harness2_jev_connector.connector import JevMcpConnector

REPORT = "PBI-007"
VERIFY_RESULTS = [
    {"n": 1, "command": "true", "code": 0},
    {"n": 2, "command": "true", "code": 0},
]


class FakeClient:
    """A jev-mcp client with two queued tasks and a verification that passes twice."""

    def __init__(self):
        self.closed = False

    def call_tool(self, name, arguments):
        if name == "list_tasks":
            return {
                "repo": arguments["repo"],
                "tasks": [
                    {"report": REPORT, "pbi": REPORT, "title": "seven", "state": "queued"},
                    {
                        "report": f"{REPORT}.rework-1",
                        "pbi": REPORT,
                        "title": "seven rework",
                        "state": "queued",
                    },
                ],
            }
        if name == "claim_task":
            report = arguments["report"]
            return {
                "repo": arguments["repo"],
                "report": report,
                "pbi": report.split(".")[0],
                "prompt": f"Implement {report}.",
                "pbi_markdown": f"# {report}: change a.txt",
                "verify_commands": ["true"],
            }
        if name == "verify":
            return {"code": 0, "results": VERIFY_RESULTS}
        return {}

    def close(self):
        self.closed = True


class JevMcpConnectorContractTest(ConnectorContractTests, unittest.TestCase):
    def make_connector(self, tmp: Path):
        repo = tmp / "repo"
        repo.mkdir()
        return JevMcpConnector(FakeClient(), repo)


if __name__ == "__main__":
    unittest.main()
