"""Local Workbench/stdio notification protocol, with synthetic cloud-choice responses.

HTTP handling, confirmation validation, revision tracking and agent waits run normally.
The cloud boundary only writes a synthetic Chosen record: these tests do not exercise
the live cloud service, palette extraction or closed-source design production.
"""

from __future__ import annotations

import http.client
import json
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pacxio import api, mcp_server
from pacxio.cloud import route
from pacxio.concept.service import Chosen, import_concept, read_chosen
from pacxio.inventory import content_add, content_confirm, read_confirmation
from pacxio.messages import use_language
from pacxio.model.common import Issue, Result
from pacxio.studio import bridge, server
from pacxio.studio import next as next_module
from pacxio.studio.next import next_step
from pacxio.studio.server import BY_STUDIO, Studio
from pacxio.visual.meta import now_iso, sha256_of


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(bridge, "POLL_S", 0.01)
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "account"))
    monkeypatch.setenv("PACXIO_CLOUD_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("PACXIO_CLOUD_TOKEN", raising=False)
    directory = tmp_path / "review"
    assert api.init_job(directory, "rte", {"L": 80, "W": 50, "H": 180}, "sbs_350").ok
    assert content_add(directory, {"id": "brand", "category": "brand", "text": "pacxio"}).ok
    assert next_step(directory).data["who"] == "user"
    return directory


@pytest.fixture
def synthetic_cloud_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only the cloud result; never sample colours or call a live service."""
    monkeypatch.setenv("PACXIO_CLOUD", "1")

    def forward(argv: list[str], cwd: Path) -> Result:
        del cwd
        assert argv[:2] == ["concept", "choose"], (
            "Unexpected cloud command in a local protocol test"
        )
        directory, cid = Path(argv[2]), argv[3]
        image = directory / "concepts" / f"{cid}.png"
        chosen = Chosen(
            id=cid,
            file=image.relative_to(directory).as_posix(),
            sha256=sha256_of(image),
            chosen_at=now_iso(),
            by=argv[argv.index("--by") + 1],
            note=argv[argv.index("--note") + 1] if "--note" in argv else None,
        )
        record = directory / "concepts/chosen.json"
        record.write_text(chosen.model_dump_json(indent=2) + "\n")
        return Result(
            command="concept choose",
            job=str(directory),
            artifacts=[str(record)],
            data={"id": cid, "palette": []},
        )

    monkeypatch.setattr(route, "forward", forward)


@pytest.fixture
def studio(
    job: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_cloud_choice: None,
) -> Iterator[Studio]:
    monkeypatch.setattr(server, "POLL_S", 0.05)
    instance = Studio(job)
    worker = threading.Thread(target=instance.serve_forever, daemon=True)
    worker.start()
    yield instance
    instance.shutdown()
    worker.join(timeout=3)


def request(
    studio: Studio,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    connection = http.client.HTTPConnection("127.0.0.1", studio.port, timeout=5)
    request_headers = dict(headers or {})
    if body is not None:
        request_headers = {
            "Content-Type": "application/json",
            "X-Studio-Token": studio.token,
            **request_headers,
        }
    try:
        connection.request(
            "GET" if body is None else "POST",
            path,
            body=None if body is None else json.dumps(body),
            headers=request_headers,
        )
        response = connection.getresponse()
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            json.loads(response.read() or b"{}"),
        )
    finally:
        connection.close()


@contextmanager
def waiting(job: Path, timeout_s: float = 3) -> Iterator[tuple[Future[Result], threading.Event]]:
    cancel = threading.Event()
    revision = next_step(job).data["revision"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(bridge.wait_for_user, job, revision, timeout_s, cancel)
        try:
            deadline = time.monotonic() + 2
            while bridge.agent_status(job)["status"] != "waiting":
                if pending.done():
                    pytest.fail(f"Wait ended before listening: {pending.result()}")
                assert time.monotonic() < deadline, "Agent never started listening"
                cancel.wait(0.005)
            yield pending, cancel
        finally:
            cancel.set()
            pending.result(timeout=3)


def add_concept(job: Path, tmp_path: Path, name: str = "direction-a") -> None:
    source = tmp_path / f"{name}.png"
    Image.new("RGB", (300, 200), (210, 180, 140)).save(source)
    assert import_concept(job, source, name=name).ok


def test_content_confirmation_wakes_waiter_and_requires_agent_receipt(
    job: Path,
    studio: Studio,
) -> None:
    before = next_step(job).data["revision"]
    with waiting(job) as (pending, _):
        code, _, result = request(studio, "/api/confirm", {"action": "content"})
        assert code == 200 and result["ok"]
        received = pending.result(timeout=2)
    assert received.ok and received.data["status"] == "changed"
    assert received.data["revision"] != before
    assert received.data["next"] == next_step(job).data
    assert received.data["next"]["who"] == "agent"
    assert read_confirmation(job).by == BY_STUDIO
    assert bridge.agent_status(job)["status"] == "notified"
    # A page refresh or internal state read does not claim the agent has acted.
    assert request(studio, "/api/agent")[0] == 200
    assert bridge.agent_status(job)["status"] == "notified"
    result, _ = mcp_server.call("job_next", {"job": str(job)})
    assert result.ok
    assert bridge.agent_status(job)["status"] == "received"


def test_old_snapshot_cannot_acknowledge_a_newer_revision(job: Path, studio: Studio) -> None:
    with waiting(job) as (pending, _):
        code, _, confirmation = request(studio, "/api/confirm", {"action": "content"})
        assert code == 200 and confirmation["ok"]
        notification = pending.result(timeout=2)
    assert notification.data["status"] == "changed"
    old_revision = notification.data["revision"]
    assert confirmation["data"]["revision"] == old_revision
    assert content_add(
        job, {"id": "name", "category": "product_name", "text": "Updated package"}
    ).ok

    bridge.acknowledge(job, old_revision)
    _, _, status = request(studio, "/api/agent")
    assert status["status"] == "notified" and status["revision"] == old_revision

    latest = next_step(job)
    assert latest.ok and latest.data["revision"] != old_revision
    bridge.acknowledge(job, latest.data["revision"])
    _, _, status = request(studio, "/api/agent")
    assert status["status"] == "received"
    assert status["revision"] == latest.data["revision"]
    record = json.loads((job / "studio/agent.json").read_text())
    assert record["revision"] == latest.data["revision"]


def test_concept_choice_resumes_at_back_view_without_approving_flats(
    job: Path,
    studio: Studio,
    tmp_path: Path,
) -> None:
    assert content_confirm(job, by="Test reviewer").ok
    add_concept(job, tmp_path)
    assert next_step(job).data["stage"]["id"] == "concept"
    with waiting(job) as (pending, _):
        code, _, result = request(
            studio, "/api/confirm", {"action": "concept", "target": "direction-a"}
        )
        assert code == 200 and result["ok"]
        received = pending.result(timeout=2)
    assert received.data["status"] == "changed"
    assert received.data["next"]["who"] == "agent"
    assert any("--step back" in command for command in received.data["next"]["commands"])
    assert read_chosen(job).id == "direction-a"
    assert read_chosen(job).by == BY_STUDIO
    assert not (job / "concepts/flats_confirmed.json").exists()
    assert not (job / "mockup/approval.json").exists()


def test_confirmation_before_wait_is_not_lost(job: Path, studio: Studio) -> None:
    revision = next_step(job).data["revision"]
    assert request(studio, "/api/confirm", {"action": "content"})[0] == 200
    received = bridge.wait_for_user(job, revision, timeout_s=0.1)
    assert received.ok and received.data["status"] == "changed"
    assert received.data["next"]["who"] == "agent"
    assert received.data["revision"] == next_step(job).data["revision"]


def test_next_step_retries_when_confirmation_arrives_during_state_read(
    job: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_revision = bridge.revision_of(job)
    original_state = next_module.job_state
    reads = 0

    def state_with_confirmation(directory: Path) -> Any:
        nonlocal reads
        state = original_state(directory)
        reads += 1
        if reads == 1:
            assert content_confirm(directory, by="Test reviewer").ok
        return state

    monkeypatch.setattr(next_module, "job_state", state_with_confirmation)
    result = next_step(job)
    assert result.ok and reads >= 2
    assert result.data["who"] == "agent"
    assert result.data["revision"] != original_revision
    assert result.data["revision"] == bridge.revision_of(job)


def test_revision_does_not_change_with_language_or_agent_heartbeat(job: Path) -> None:
    with use_language("en"):
        english = next_step(job).data
    with use_language("zh"):
        chinese = next_step(job).data
    assert english["step"] != chinese["step"]
    assert english["revision"] == chinese["revision"] == bridge.revision_of(job)
    with waiting(job):
        assert bridge.revision_of(job) == english["revision"]
    assert bridge.revision_of(job) == english["revision"]


def test_unrelated_files_do_not_wake_or_confirm(job: Path) -> None:
    revision = bridge.revision_of(job)
    with waiting(job, timeout_s=0.15) as (pending, _):
        (job / "debug.log").write_text("Preview refreshed\n")
        assert bridge.revision_of(job) == revision
        received = pending.result(timeout=2)
    assert received.ok and received.data["status"] == "timeout"
    assert received.data["next"]["who"] == "user"
    assert read_confirmation(job) is None
    assert bridge.agent_status(job)["status"] == "disconnected"


def test_content_change_wakes_without_fabricating_confirmation(job: Path) -> None:
    with waiting(job) as (pending, _):
        assert content_add(
            job, {"id": "name", "category": "product_name", "text": "Test package"}
        ).ok
        received = pending.result(timeout=2)
    assert received.ok and received.data["status"] == "changed"
    assert received.data["next"]["who"] == "user"
    assert read_confirmation(job) is None


def test_cancel_releases_wait_without_confirming(job: Path) -> None:
    revision = bridge.revision_of(job)
    with waiting(job) as (pending, cancel):
        cancel.set()
        received = pending.result(timeout=2)
    assert received.ok and received.data["status"] == "cancelled"
    assert bridge.agent_status(job)["status"] == "disconnected"
    assert read_confirmation(job) is None
    assert bridge.revision_of(job) == revision
    assert bridge.wait_for_user(job, revision, timeout_s=0.02).data["status"] == "timeout"


def test_second_waiter_is_busy_and_cannot_take_over(job: Path, studio: Studio) -> None:
    with waiting(job) as (first, _):
        second = bridge.wait_for_user(job, bridge.revision_of(job), timeout_s=0.1)
        assert second.ok and second.data["status"] == "busy"
        assert not first.done()
        assert bridge.agent_status(job)["status"] == "waiting"
        assert request(studio, "/api/confirm", {"action": "content"})[0] == 200
        assert first.result(timeout=2).data["status"] == "changed"


def test_wait_is_immediate_when_no_user_decision_is_pending(job: Path) -> None:
    assert content_confirm(job, by="Test reviewer").ok
    received = bridge.wait_for_user(job, bridge.revision_of(job), timeout_s=0.1)
    assert received.ok and received.data["status"] == "not_waiting"
    assert received.data["next"]["who"] == "agent"


def test_agent_endpoint_reports_active_wait_and_rejects_other_sites(
    job: Path,
    studio: Studio,
) -> None:
    with waiting(job):
        code, headers, body = request(studio, "/api/agent")
        assert code == 200 and headers["cache-control"] == "no-store"
        assert body["status"] == "waiting" and body["stage"] == "content"
        assert body["expires_at"] > body["updated_at"]
        assert request(studio, "/api/agent", headers={"Host": "evil.example"})[0] == 403
        assert request(studio, "/api/agent", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert (
            request(
                studio,
                "/api/confirm",
                {"action": "content"},
                headers={"Origin": "https://evil.example"},
            )[0]
            == 403
        )
        assert read_confirmation(job) is None


def test_repeated_concept_choice_is_idempotent(
    job: Path,
    studio: Studio,
    tmp_path: Path,
) -> None:
    assert content_confirm(job, by="Test reviewer").ok
    add_concept(job, tmp_path)
    body = {"action": "concept", "target": "direction-a", "note": "Reviewed"}
    assert request(studio, "/api/confirm", body)[0] == 200
    original = (job / "concepts/chosen.json").read_bytes()
    revision = bridge.revision_of(job)
    code, _, result = request(studio, "/api/confirm", body)
    assert code == 200 and result["ok"]
    assert (job / "concepts/chosen.json").read_bytes() == original
    assert bridge.revision_of(job) == revision


def test_cloud_failure_does_not_wake_waiter_or_create_a_choice(
    job: Path,
    studio: Studio,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert content_confirm(job, by="Test reviewer").ok
    add_concept(job, tmp_path)
    revision = bridge.revision_of(job)

    def refuse(argv: list[str], cwd: Path) -> Result:
        del cwd
        assert argv[:2] == ["concept", "choose"]
        return Result(command="concept choose", job=str(job)).fail(
            Issue(id="CLOUD_TEST", message="Synthetic cloud refusal"), 3
        )

    monkeypatch.setattr(route, "forward", refuse)
    with waiting(job, timeout_s=0.3) as (pending, _):
        code, _, body = request(
            studio, "/api/confirm", {"action": "concept", "target": "direction-a"}
        )
        assert code == 400 and not body["ok"]
        assert body["errors"][0]["id"] == "CLOUD_TEST"
        assert body["errors"][0]["message"] == "Synthetic cloud refusal"
        assert bridge.agent_status(job)["status"] == "waiting"
        assert not pending.done()
        outcome = pending.result(timeout=2)
    assert outcome.ok and outcome.data["status"] == "timeout"
    assert outcome.data["next"]["who"] == "user"
    assert read_chosen(job) is None
    assert bridge.revision_of(job) == revision


@pytest.mark.parametrize("during_wait", [False, True])
def test_invalid_state_never_reports_successful_change(job: Path, during_wait: bool) -> None:
    revision = bridge.revision_of(job)
    if during_wait:
        with waiting(job) as (pending, _):
            (job / "job.json").write_text("{invalid")
            received = pending.result(timeout=2)
    else:
        (job / "job.json").write_text("{invalid")
        received = bridge.wait_for_user(job, revision, timeout_s=0.1)
    assert not received.ok and received.errors
    assert received.data.get("status") != "changed"
    assert read_confirmation(job) is None


def test_unreadable_review_notes_do_not_look_like_a_successful_change(job: Path) -> None:
    with waiting(job) as (pending, _):
        folder = job / "review"
        folder.mkdir(exist_ok=True)
        (folder / "notes.json").write_text(json.dumps({"notes": "invalid"}))
        received = pending.result(timeout=2)
    assert not received.ok and received.errors
    assert received.data.get("status") != "changed"
    assert read_confirmation(job) is None


def test_expired_wait_and_receipt_do_not_stay_connected(job: Path) -> None:
    with waiting(job):
        record = json.loads((job / "studio/agent.json").read_text())
    record["expires_at"] = 0
    for saved, expected in (
        ("waiting", "disconnected"),
        ("notified", "disconnected"),
        ("received", "idle"),
    ):
        record["status"] = saved
        (job / "studio/agent.json").write_text(json.dumps(record))
        assert bridge.agent_status(job)["status"] == expected


def send_rpc(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    process.stdin.flush()


def receive_rpc(messages: queue.Queue[str], timeout: float = 3) -> dict[str, Any]:
    try:
        return json.loads(messages.get(timeout=timeout))
    except queue.Empty:
        pytest.fail("The MCP subprocess did not respond in time")


@pytest.fixture
def stdio_mcp() -> Iterator[tuple[subprocess.Popen[str], queue.Queue[str]]]:
    process = subprocess.Popen(
        [sys.executable, "-m", "pacxio", "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    messages: queue.Queue[str] = queue.Queue()

    def collect() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            messages.put(line)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    try:
        send_rpc(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pacxio-wait-test", "version": "1"},
                },
            },
        )
        initialized = receive_rpc(messages, timeout=10)
        assert initialized["id"] == 1 and "result" in initialized
        send_rpc(process, {"method": "notifications/initialized"})
        yield process, messages
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        reader.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def start_stdio_wait(process: subprocess.Popen[str], job: Path) -> None:
    send_rpc(
        process,
        {
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "studio_wait",
                "arguments": {
                    "job": str(job),
                    "after_revision": bridge.revision_of(job),
                    "timeout_s": 40,
                },
            },
        },
    )
    deadline = time.monotonic() + 3
    while bridge.agent_status(job)["status"] != "waiting":
        assert process.poll() is None, "MCP process exited while starting its wait"
        assert time.monotonic() < deadline, "MCP wait never started listening"
        threading.Event().wait(0.01)


def rpc_data(message: dict[str, Any]) -> dict[str, Any]:
    result = message["result"]
    assert not result.get("isError")
    return json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))


def test_stdio_wait_accepts_ping_and_cancellation_without_blocking(
    job: Path,
    stdio_mcp: tuple[subprocess.Popen[str], queue.Queue[str]],
) -> None:
    process, messages = stdio_mcp
    start_stdio_wait(process, job)
    # The on-disk lock protects the job even when the other caller is a separate process.
    assert (
        bridge.wait_for_user(job, bridge.revision_of(job), timeout_s=0.1).data["status"] == "busy"
    )
    send_rpc(process, {"id": 3, "method": "ping"})
    send_rpc(process, {"id": 4, "method": "tools/list"})
    ping = receive_rpc(messages)
    available = receive_rpc(messages)
    assert ping == {"jsonrpc": "2.0", "id": 3, "result": {}}
    assert available["id"] == 4
    assert any(tool["name"] == "studio_wait" for tool in available["result"]["tools"])
    assert bridge.agent_status(job)["status"] == "waiting"
    send_rpc(
        process,
        {
            "method": "notifications/cancelled",
            "params": {"requestId": 2, "reason": "The user stopped waiting"},
        },
    )
    cancelled = receive_rpc(messages)
    assert cancelled["id"] == 2 and rpc_data(cancelled)["data"]["status"] == "cancelled"
    assert bridge.agent_status(job)["status"] == "disconnected"
    assert read_confirmation(job) is None
    send_rpc(process, {"id": 5, "method": "ping"})
    assert receive_rpc(messages)["id"] == 5


def test_stdio_eof_cancels_wait_and_releases_job_lock(
    job: Path,
    stdio_mcp: tuple[subprocess.Popen[str], queue.Queue[str]],
) -> None:
    process, messages = stdio_mcp
    start_stdio_wait(process, job)
    assert process.stdin is not None
    process.stdin.close()
    assert process.wait(timeout=3) == 0
    cancelled = receive_rpc(messages)
    assert cancelled["id"] == 2 and rpc_data(cancelled)["data"]["status"] == "cancelled"
    assert bridge.agent_status(job)["status"] == "disconnected"
    assert read_confirmation(job) is None
    assert (
        bridge.wait_for_user(job, bridge.revision_of(job), timeout_s=0.02).data["status"]
        == "timeout"
    )


def test_stdio_receives_http_choice_then_acknowledges_on_next_call(
    job: Path,
    studio: Studio,
    tmp_path: Path,
    stdio_mcp: tuple[subprocess.Popen[str], queue.Queue[str]],
) -> None:
    assert content_confirm(job, by="Test reviewer").ok
    add_concept(job, tmp_path)
    process, messages = stdio_mcp
    start_stdio_wait(process, job)
    assert request(studio, "/api/confirm", {"action": "concept", "target": "direction-a"})[0] == 200
    response = receive_rpc(messages)
    assert response["id"] == 2
    assert rpc_data(response)["data"]["status"] == "changed"
    assert bridge.agent_status(job)["status"] == "notified"
    send_rpc(
        process,
        {
            "id": 3,
            "method": "tools/call",
            "params": {"name": "job_next", "arguments": {"job": str(job)}},
        },
    )
    response = receive_rpc(messages)
    assert response["id"] == 3
    assert rpc_data(response)["data"]["who"] == "agent"
    assert bridge.agent_status(job)["status"] == "received"
    assert not (job / "concepts/flats_confirmed.json").exists()
