"""A WorkBuddy task may end while its local review window must stay available."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pacxio.mcp_server import handle
from pacxio.studio import control


def create_job(job: Path) -> None:
    result = handle(
        {
            "method": "tools/call",
            "params": {
                "name": "job_init_structure",
                "arguments": {"job": str(job), "code": "0427", "L": 240, "W": 170, "H": 70},
            },
        }
    )
    assert result is not None and not result["isError"], result


@pytest.mark.skipif(os.name != "posix", reason="WorkBuddy POSIX process lifecycle")
def test_detached_workbench_survives_its_agent_worker(tmp_path: Path) -> None:
    job = tmp_path / "review job"
    create_job(job)
    script = """
import json, sys
from pathlib import Path
from pacxio.studio.control import studio_start
result = studio_start(Path(sys.argv[1]))
print(json.dumps(result.model_dump(mode='json')), flush=True)
sys.stdin.read()
"""
    worker = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(job)],
        env=os.environ | {"PACXIO_NO_UPDATE": "1", "PACXIO_STUDIO_DETACH": "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None
        result = json.loads(worker.stdout.readline())
        assert result["ok"], result
        pid = result["data"]["pid"]
        parent = subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True)
        assert int(parent.strip()) != worker.pid
        worker.terminate()
        worker.communicate(timeout=5)
        status = control.studio_status(job)
        assert status.ok and status.data["running"] and status.data["pid"] == pid
        reused = control.studio_start(job)
        assert reused.ok and reused.data["reused"] and reused.data["pid"] == pid
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.communicate(timeout=5)
        stopped = control.studio_stop(job)
        assert stopped.ok
    assert not control.studio_status(job).data["running"]


def test_launcher_failure_is_reported_without_claiming_a_running_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    create_job(job)

    def fail(*_: object) -> None:
        raise OSError("cannot launch test runtime")

    monkeypatch.setattr(control, "_spawn", fail)
    result = control.studio_start(job)
    assert not result.ok and result.errors[0].id == "STUDIO"
    assert not (job / "studio/server.json").exists()
