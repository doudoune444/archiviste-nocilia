"""Unit tests for the AC-14 import-firewall regex — MED-1 bypass coverage."""

from __future__ import annotations

import pytest

from tests.conftest import _DRIVE_API_PATTERN


class TestImportFirewallRegex:
    """AC-14: regex must catch all forbidden import forms, not just dotted access."""

    _PAT = _DRIVE_API_PATTERN

    @pytest.mark.parametrize(
        "line",
        [
            # bare module import
            "import requests",
            "import httpx",
            "import urllib3",
            "import urllib",
            "import aiohttp",
            "import googleapiclient",
            "import httplib2",
            # aliased import — previously bypassed the old pattern
            "import httpx as h",
            # from-module import — previously bypassed the old pattern
            "from requests import get",
            "from requests import Session",
            "from httpx import AsyncClient",
            "from urllib3 import PoolManager",
            "from aiohttp import ClientSession",
            "from google.auth import credentials",
            # indented (inside a function/class body)
            "  import requests",
            "  from httpx import get",
        ],
    )
    def test_forbidden_import_detected(self, line: str) -> None:
        assert self._PAT.search(line), f"Should detect forbidden import: {line!r}"

    @pytest.mark.parametrize(
        "line",
        [
            "# import requests",
            "x = 'import requests'",
            "import os",
            "from pathlib import Path",
            "import re",
        ],
    )
    def test_allowed_line_not_flagged(self, line: str) -> None:
        assert not self._PAT.search(line), f"Should not flag allowed line: {line!r}"
