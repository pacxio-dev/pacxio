"""Select one verified runtime before importing the CLI; updates never replace a live runtime."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pacxio import __version__

PIN_ENV = "PACXIO_RUNTIME_PIN"
BOOTSTRAP_ENV = "PACXIO_UPDATE_BOOTSTRAP"
FALLBACK_ENV = "PACXIO_UPDATE_FALLBACK"


def _bootstrap_context() -> dict[str, Any] | None:
    try:
        value = json.loads(os.environ.get(BOOTSTRAP_ENV, "null"))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def bootstrap_python() -> str:
    """Plugin registration must keep its original entry point, even from a newer runtime."""
    value = (_bootstrap_context() or {}).get("bootstrap_python")
    return value if isinstance(value, str) and Path(value).is_file() else sys.executable


def choose_launch(argv: list[str]) -> dict[str, Any]:
    """Snapshot the runtime for this launch; the background check happens afterwards."""
    del argv
    plan: dict[str, Any] = {
        "python": sys.executable,
        "version": __version__,
        "path": sys.prefix,
        "switch": False,
        "context": None,
    }
    if os.environ.get("PACXIO_NO_UPDATE") == "1" or os.environ.get(PIN_ENV):
        return plan
    try:
        from pacxio import updates

        context = updates.eligibility()
        plan["context"] = context
        if not context.get("enabled"):
            return plan
        runtime = updates.select_runtime(context)
        if runtime and updates.health_check(runtime["python"], runtime["version"], timeout_s=5):
            plan.update(runtime, switch=True)
        elif runtime:
            plan["fallback"] = "runtime_health_check_failed"
    except Exception:  # noqa: BLE001 — a broken updater must not prevent the installed CLI starting
        plan["fallback"] = "update_state_unavailable"
    return plan


def _run_local(argv: list[str]) -> None:
    # Do not import cli/api until the version is fixed for this entire process.
    from pacxio.cli import main as cli_main

    sys.argv = [sys.argv[0], *argv]
    cli_main()


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    plan = choose_launch(args)
    context = plan.get("context")
    if context and context.get("enabled") and args[:2] == ["mcp", "serve"]:
        try:
            from pacxio.updates import spawn_check

            spawn_check(context)
        except Exception:  # noqa: BLE001 — background maintenance cannot break the protocol
            pass
    environment = os.environ.copy()
    environment[PIN_ENV] = str(plan["path"])
    if context:
        environment[BOOTSTRAP_ENV] = json.dumps(context)
    if plan.get("fallback"):
        environment[FALLBACK_ENV] = plan["fallback"]
    if plan["switch"]:
        # Installed runtimes resolve their own modules, never a caller's source checkout.
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        try:
            os.execve(plan["python"], [plan["python"], "-I", "-m", "pacxio", *args], environment)
        except OSError:
            # exec did not consume any MCP input, so the installed version can still answer.
            environment[PIN_ENV] = sys.prefix
            environment[FALLBACK_ENV] = "runtime_start_failed"
    os.environ.update({PIN_ENV: environment[PIN_ENV]})
    if BOOTSTRAP_ENV in environment:
        os.environ[BOOTSTRAP_ENV] = environment[BOOTSTRAP_ENV]
    if FALLBACK_ENV in environment:
        os.environ[FALLBACK_ENV] = environment[FALLBACK_ENV]
    _run_local(args)


def update_status() -> dict[str, Any]:
    """Local-only diagnostics; querying status never contacts PyPI or starts installation."""
    from pacxio import updates

    context = _bootstrap_context() or updates.eligibility()

    def read(name: str) -> dict[str, Any]:
        try:
            value = json.loads((updates.state_dir() / name).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    state = read("status.json")
    if state.get("bootstrap_prefix") != context.get("bootstrap_prefix"):
        state = {}
    fallback = os.environ.get(FALLBACK_ENV)
    ready = updates.select_runtime(context)
    if not ready and state.get("status") == "ready":
        fallback = fallback or "prepared_runtime_unavailable"
    ready_version = ready.get("version") if ready and not fallback else None
    from packaging.version import Version

    pending_restart = bool(ready_version and Version(ready_version) > Version(__version__))
    return {
        "enabled": bool(context.get("enabled")),
        "reason": context.get("reason"),
        "running_version": __version__,
        "bootstrap_version": context.get("current_version") or __version__,
        "ready_version": ready_version,
        "pending_restart": pending_restart,
        "status": ("failed" if fallback else state.get("status", "not_checked"))
        if context.get("enabled")
        else "disabled",
        "checked_at": state.get("checked_at"),
        "last_error": fallback
        or (state.get("reason") if state.get("status") == "failed" else None),
    }


def update_message(status: dict[str, Any]) -> str:
    from pacxio.messages import t

    if not status["enabled"]:
        return t(
            "Automatic updates are off for this installation; the installed version remains available."
        )
    if status["status"] == "failed":
        return t(
            "The update check or installation failed. Continue using the working version; another check will run at the next plugin startup."
        )
    if status["pending_restart"]:
        return t(
            "Version {version} is ready and will be used on a later startup. This session keeps version {current}.",
            version=status["ready_version"],
            current=status["running_version"],
        )
    if status["status"] in ("checking", "installing"):
        return t(
            "Checking or preparing an update in the background. This session keeps version {version}.",
            version=status["running_version"],
        )
    return t(
        "Running version {version}. Automatic checks run in the background when the plugin starts.",
        version=status["running_version"],
    )


if __name__ == "__main__":
    main()
