"""Official MCP Registry manifest and PyPI ownership marker consistency checks."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pacxio import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_server_json_matches_package_and_readme_marker() -> None:
    server_path = ROOT / "server.json"
    assert server_path.is_file(), "server.json must exist at repository root"
    server = json.loads(server_path.read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pkg_version = pyproject["project"]["version"]

    assert server["$schema"] == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    assert server["name"] == "dev.pacxio/pacxio"
    assert 1 <= len(server["description"]) <= 100
    assert server["version"] == pkg_version == __version__
    assert server["websiteUrl"] == "https://pacxio.dev"
    assert server["repository"]["url"] == "https://github.com/pacxio-dev/pacxio"
    assert server["repository"]["source"] == "github"

    packages = server["packages"]
    assert len(packages) == 1
    pkg = packages[0]
    assert pkg["registryType"] == "pypi"
    assert pkg["registryBaseUrl"] == "https://pypi.org"
    assert pkg["identifier"] == "pacxio"
    assert pkg["version"] == pkg_version
    assert pkg["runtimeHint"] == "uvx"
    assert pkg["transport"] == {"type": "stdio"}
    assert pkg["packageArguments"] == [
        {"type": "positional", "value": "mcp"},
        {"type": "positional", "value": "serve"},
    ]
    for env_var in pkg.get("environmentVariables", []):
        assert env_var.get("isRequired") is False

    marker = f"<!-- mcp-name: {server['name']} -->"
    assert marker in (ROOT / "README.md").read_text(encoding="utf-8")
    open_readme = ROOT / "open" / "README.md"
    if open_readme.is_file():
        assert marker in open_readme.read_text(encoding="utf-8")
