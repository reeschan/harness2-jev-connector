# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0
"""Tests for the package skeleton: version, entry point, and harness2 dependency."""

import importlib
import tomllib
import unittest
from pathlib import Path

import harness2_jev_connector

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def read_pyproject():
    with PYPROJECT.open("rb") as fp:
        return tomllib.load(fp)


class PackageMetadataTest(unittest.TestCase):
    def test_version_matches_pyproject(self):
        project = read_pyproject()["project"]
        self.assertEqual(harness2_jev_connector.__version__, project["version"])

    def test_entry_point_registers_jev_mcp(self):
        group = read_pyproject()["project"]["entry-points"]["harness2.task_connectors"]
        self.assertEqual(
            group["jev-mcp"], "harness2_jev_connector.connector:connector_from_options"
        )

    def test_harness2_modules_are_importable(self):
        modules = (
            "harness2.core.intake",
            "harness2.plugins.mcp",
            "harness2.testing.connector_contract",
        )
        for module in modules:
            with self.subTest(module=module):
                importlib.import_module(module)


if __name__ == "__main__":
    unittest.main()
