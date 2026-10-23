# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0

"""JevModelConnector through the shared model connector contract tests."""

import unittest
from pathlib import Path

from harness2.core.models import ModelChoice, SessionRequest
from harness2.testing.model_contract import ModelConnectorContractTests

from harness2_jev_connector.model_config import Step, Template
from harness2_jev_connector.models import JevModelConnector


class FakeClient:
    """A jev-mcp client whose route gives two steps and whose record succeeds."""

    def __init__(self):
        self.closed = 0

    def call_tool(self, name, arguments):
        if name == "route":
            return {
                "tier": arguments.get("tier"),
                "route": [
                    {"runner": "opencode", "model": "fake/model", "effort": ""},
                    {"runner": "claude", "model": "sonnet", "effort": "low"},
                ],
            }
        if name == "record":
            return {"code": 0, "stdout": "", "stderr": ""}
        if name == "run":
            return {"code": 0, "last": "done", "unusable": False, "unusable_reason": None}
        return {}

    def close(self):
        self.closed += 1


TEMPLATE = Template(
    {
        "executor": (Step(jev="haiku"),),
        "improver": (
            Step(runner="claude", model="sonnet", setting="edit-only"),
            Step(jev="sonnet", runners=("claude",), setting="edit-only"),
        ),
    }
)


class JevModelConnectorContractTest(ModelConnectorContractTests, unittest.TestCase):
    def make_connector(self):
        return JevModelConnector(FakeClient(), "/repo", TEMPLATE, timeout=5)

    def make_session_request(self, tmp: Path, choice: ModelChoice) -> SessionRequest:
        return SessionRequest(
            cwd=tmp,
            mission="say hello",
            system="be brief",
            wall=30,
            label="contract",
            choice=choice,
        )


if __name__ == "__main__":
    unittest.main()
