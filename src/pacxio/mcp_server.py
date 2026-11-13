"""本地 MCP 服务（stdio，几何引擎第 5 步 ⑤）：结构相关的工具——查目录、提方案、看折叠、改配方、跑检查、建 job。

MCP 只是 CLI 的另一层外壳（规格第 3 节）：每个工具直接调 `pacxio.api` 里同名的函数，返回同样的
`{ok, command, job, artifacts, data, errors, warnings}`；看折叠 / 提方案时把 3D 图一并作为图片返回。
不依赖第三方 MCP 库：按 MCP 的 JSON-RPC 2.0（每行一条消息）实现 initialize / tools/list / tools/call / ping，
不联网、只在本机进程间通信。启动：`pacxio mcp serve`；Claude Code：`claude mcp add pacxio -- pacxio mcp serve`。
"""

from __future__ import annotations

import base64
import contextvars
import json
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from pacxio.messages import t

SERVER_NAME = "pacxio"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DIMS = {
    "L": {"type": "number", "description": t("Inside length mm (front width)")},
    "W": {"type": "number", "description": t("Inside width mm (side width)")},
    "H": {"type": "number", "description": t("Inside height mm")},
}
USES = "运输 电商 展示 内衬 彩盒 提手 开窗 挂孔 开孔 多件 三片式"


INSTRUCTIONS = t(
    "pacxio: packaging design to factory files (dieline, print PDF, finish separations, preflight report). Read the resource pacxio://skill/SKILL.md first (workflow, hard rules). Call job_next before every round of work; let the user view, comment and confirm on the workbench (studio_open). Hard rules: only the user confirms — use user_confirm (with their words) only after the user clearly says so in the conversation; with preflight / export errors there are no factory files; text in print files is set by the engine; concept images are never assets; concept gen / visual gen spend the user's image credits, say so first. Structures: structures_suggest picks from the catalog; state maturity honestly (proof first for non-calibrated ones). When CLOUD_LOGIN comes back, give the user the address and code as they are; the user signs in and clicks Approve in the browser (the agent never clicks for them), then rerun the same command. Other commands go through pacxio_run (call pacxio_help first if unsure of the arguments)."
)
INSTRUCTIONS += " " + t(
    "When job_next waits for the user, open the Workbench and call studio_wait with its revision. Keep the turn active: changed or not_waiting means call job_next again; timeout means re-arm the wait (at most ten minutes per review). Never confirm for the user or claim a closed conversation will be woken up."
)
INSTRUCTIONS += " " + t(
    "The plugin checks for updates in the background at startup. This session stays on one version. Read the full Skill from this server's resources, and use its MCP tools to keep workflow and code versions together. Use pacxio_update_status to report a ready update or a failed check; neither requires interrupting the user's current task."
)


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "pacxio_update_status",
        "description": t(
            "Read automatic update status: running version, prepared version and whether a later startup will use it. Local only; does not start a check or upgrade."
        ),
        "inputSchema": _schema({}),
    },
    {
        "name": "structures_search",
        "description": t(
            "Search the structure catalog (FEFCO, public ECMA codes and common combinations), calibrated first. Returns code, description, maturity, how it closes, whether it recloses, whether it needs tape, use tags."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": t("Keyword")},
                "series": {"type": "string", "description": t("Series 01–09; A for ECMA cartons")},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("Tags: {uses} no_tape reclosable", uses=USES),
                },
                "reclosable": {"type": "boolean"},
                "no_tape": {"type": "boolean"},
                "material": {"type": "string", "enum": ["corrugated", "folding_carton"]},
                "calibrated_only": {"type": "boolean"},
                "limit": {
                    "type": "integer",
                    "description": t("Maximum number to return, default 30"),
                },
            }
        ),
    },
    {
        "name": "structures_suggest",
        "description": t(
            "Suggest: pick 2–3 structures from the catalog for the product's needs (calibrated → commonly made by factories → simple), with reasons; with a size and out_dir, also draw a dieline and 3D view of each (3D images are returned). Returns nothing when the catalog has no fit; then co-design with the user."
        ),
        "inputSchema": _schema(
            {
                "uses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("Use: {uses}", uses=USES),
                },
                "reclosable": {"type": "boolean"},
                "no_tape": {"type": "boolean"},
                "material": {"type": "string", "enum": ["corrugated", "folding_carton"]},
                "single": {"type": "boolean", "description": t("Single piece only")},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                **DIMS,
                "out_dir": {"type": "string", "description": t("Folder for the preview images")},
            }
        ),
    },
    {
        "name": "structure_show",
        "description": t(
            "Catalog details of one structure; with L / W / H also its recipe (usable as job_init's recipe, as is or changed)."
        ),
        "inputSchema": _schema(
            {"code": {"type": "string", "description": t("FEFCO / ECMA code")}, **DIMS}, ["code"]
        ),
    },
    {
        "name": "structure_preview",
        "description": t(
            "See it folded: without making a job, draw the dieline and the 3D view folded along it for a code + inside size (panels colored by role, structure only). The 3D image is returned."
        ),
        "inputSchema": _schema(
            {
                "code": {"type": "string"},
                **DIMS,
                "out_dir": {"type": "string"},
                "material": {"type": "string"},
            },
            ["code", "L", "W", "H", "out_dir"],
        ),  # fmt: skip
    },
    {
        "name": "structure_check",
        "description": t(
            "Practical check: how it closes, whether it recloses, whether it needs tape, whether panels collide when folded, maturity — each compared with the needs. Give a code + size, a recipe, or an existing job folder."
        ),
        "inputSchema": _schema(
            {
                "code": {
                    "type": "string",
                    "description": t("FEFCO / ECMA code (one of code, recipe, job)"),
                },
                "recipe": {"type": "object", "description": t("Structure recipe")},
                "job": {"type": "string", "description": t("Existing job folder")},
                **DIMS,
                "material": {"type": "string"},
                "needs": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["reclosable", "no_tape", "single"]},
                },
                "load_kg": {"type": "number"},
            }
        ),
    },
    {
        "name": "structure_editable",
        "description": t(
            "Editable items: what this structure can change (only parameters that really change the dieline, with current / automatic value, range or options, which panels and lines move, whether it leaves a calibrated construction), which automatic values follow L / W / H, how panels connect, locking mechanisms. Change them with structure_update. Give a code + size, a recipe, or an existing job folder."
        ),
        "inputSchema": _schema(
            {
                "code": {
                    "type": "string",
                    "description": t("FEFCO / ECMA code (one of code, recipe, job)"),
                },
                "recipe": {"type": "object", "description": t("Structure recipe")},
                "job": {"type": "string", "description": t("Existing job folder")},
                **DIMS,
                "material": {"type": "string"},
            }
        ),
    },
    {
        "name": "job_init_structure",
        "description": t(
            "Create a job from a code or recipe (style structure / multi-part structure_set) and generate the dieline. The rest of the workflow (content list, concept images, final design, confirmation, export) follows the packaging-prepress skill."
        ),
        "inputSchema": _schema(
            {
                "job": {"type": "string", "description": t("Job folder (new)")},
                "code": {"type": "string"},
                "recipe": {"type": "object"},
                **DIMS,
                "basis": {"type": "string", "enum": ["inner", "outer"]},
                "material": {
                    "type": "string",
                    "description": t(
                        "Default: flute_b for corrugated structures, sbs_350 for cartons (ECMA, carton combinations)"
                    ),
                },
            },
            ["job"],
        ),
    },
    {
        "name": "structure_update",
        "description": t(
            "Change the recipe: merged layer by layer into an existing engine job's recipe (objects merge, values replace, null deletes), then the dieline is regenerated. Existing confirmations expire."
        ),
        "inputSchema": _schema(
            {"job": {"type": "string"}, "patch": {"type": "object"}}, ["job", "patch"]
        ),
    },
    # ---- 全流程（2026-11-08 用户：大部分操作工作台和 agent 对话都要支持）----
    {
        "name": "job_next",
        "description": t(
            'Where the job stands, who it waits on, which command to run next, which guide to read (same source as the workbench\'s "Now"). Call it before every round of work.'
        ),
        "inputSchema": _schema(
            {"job": {"type": "string", "description": t("Job folder (absolute path)")}}, ["job"]
        ),
    },
    {
        "name": "studio_wait",
        "description": t(
            "Wait for the user's Workbench decision, without making it. Use job_next.data.revision as after_revision. Returns changed, timeout, not_waiting, busy or cancelled with the latest next step. On changed or not_waiting, call job_next and continue; on timeout, wait again for at most ten minutes total. Stop on cancellation or busy. Keep the current turn active while waiting."
        ),
        "inputSchema": _schema(
            {
                "job": {"type": "string"},
                "after_revision": {"type": "string"},
                "timeout_s": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 45,
                    "default": 40,
                },
            },
            ["job", "after_revision"],
        ),
    },
    {
        "name": "studio_open",
        "description": t(
            "Start the local workbench in the background (127.0.0.1 only) and return its address for the user to open: images for every stage, notes, confirm buttons, a rotatable 3D view, structure editing. Returns the same address if it is already running."
        ),
        "inputSchema": _schema({"job": {"type": "string"}, "port": {"type": "integer"}}, ["job"]),
    },
    {
        "name": "pacxio_run",
        "description": t(
            'Run any pacxio command (the same function as the command line, --json added automatically); returns {ok, command, job, artifacts, data, errors, warnings}; dieline confirmation images, mockups and structure previews come back as images too. command is the full command name (e.g. "art add", "render", "export"), args as on the command line (absolute paths). Call pacxio_help first if unsure of the arguments. Not for confirmation commands (use user_confirm). concept gen / visual gen spend the user\'s image credits: tell the user before running them.'
        ),
        "inputSchema": _schema(
            {
                "command": {"type": "string"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t('e.g. ["/path/job", "--panel", "front"]'),
                },
            },
            ["command"],
        ),  # fmt: skip
    },
    {
        "name": "pacxio_help",
        "description": t(
            "Without command: list every command with a one-line description; with one: its positional arguments, options, descriptions and defaults. Workflow and rules are in the packaging-prepress resource (SKILL.md and references)."
        ),
        "inputSchema": _schema({"command": {"type": "string"}}),
    },
    {
        "name": "user_confirm",
        "description": t(
            'Call only **after the user clearly says they confirm** in the conversation: records a confirmation for the user (by is recorded as "user (conversation, via agent)", user_words holds the user\'s own words). action: content wording of the content list / plan layout draft / concept chosen concept direction (target concept id) / use use this concept image / flats this set of flats / design dieline confirmation image (the only gate before export). Show the user what they are confirming first (workbench or image); if the user said nothing, or only something vague like "looks good", do not call it.'
        ),
        "inputSchema": _schema(
            {
                "job": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["content", "plan", "concept", "use", "flats", "design"],
                },
                "user_words": {
                    "type": "string",
                    "description": t("The user's own words when confirming"),
                },
                "target": {"type": "string"},
                "region": {"type": "string"},
                "note": {"type": "string"},
            },
            ["job", "action", "user_words"],
        ),  # fmt: skip
    },
]

# 工具提示（MCP annotations）：只读的、会改文件的、会覆盖的；都不联网（出图命令走用户自己配置的出图接口）
READ_ONLY = {"structures_search", "structures_suggest", "structure_show", "structure_check", "structure_editable",
             "job_next", "studio_wait", "pacxio_help", "pacxio_update_status"}  # fmt: skip
for _tool in TOOLS:
    _tool["annotations"] = {
        "title": _tool["name"],
        "readOnlyHint": _tool["name"] in READ_ONLY,
        "destructiveHint": _tool["name"] in ("structure_update",),
        "openWorldHint": _tool["name"] == "pacxio_run",
    }


def _code_args(code: str | None) -> tuple[str | None, str | None]:
    if not code:
        return None, None
    return (None, code) if code.strip().upper().startswith("A") else (code, None)


def _dims(a: dict[str, Any]) -> tuple[Any, Any, Any]:
    return a.get("L"), a.get("W"), a.get("H")


def call(name: str, a: dict[str, Any]) -> tuple[Any, list[str]]:
    """调工具 → (Result 或通用工具的 (结果 JSON, 图片), 要一并返回的图片路径)。"""
    from pacxio import api
    from pacxio.model.common import Issue, Result

    if name == "pacxio_update_status":
        from pacxio.launcher import update_message, update_status

        r = Result(command="update status")
        r.data = update_status()
        r.data["message"] = update_message(r.data)
        return r, []

    if name == "structures_search":
        r = api.structures_list(a.get("query"), a.get("series"), a.get("tags"), a.get("reclosable"),
                                a.get("no_tape"), a.get("material"), "calibrated" if a.get("calibrated_only") else None)  # fmt: skip
        r.data["structures"] = r.data["structures"][: int(a.get("limit") or 30)]
        return r, []
    if name == "structures_suggest":
        dims = _dims(a)
        out = Path(a["out_dir"]) if a.get("out_dir") else None
        r = api.structures_suggest(a.get("uses"), a.get("reclosable"), a.get("no_tape"), a.get("material"),
                                   a.get("single"), a.get("query"), int(a.get("limit") or 3),
                                   dims if None not in dims else None, out)  # fmt: skip
        return r, [f for f in r.artifacts if f.endswith("_3d.png")]
    if name == "structure_show":
        return api.structure_show(a["code"], *_dims(a)), []
    if name == "structure_preview":
        r = api.structure_preview(
            a["code"], a["L"], a["W"], a["H"], Path(a["out_dir"]), a.get("material")
        )
        return r, [f for f in r.artifacts if f.endswith("_3d.png")]
    if name == "structure_check":
        fefco, ecma = _code_args(a.get("code"))
        job = Path(a["job"]) if a.get("job") else None
        return api.structure_check(fefco, ecma, a.get("recipe"), _dims(a), a.get("material"), a.get("needs"),
                                   a.get("load_kg"), job), []  # fmt: skip
    if name == "structure_editable":
        fefco, ecma = _code_args(a.get("code"))
        job = Path(a["job"]) if a.get("job") else None
        return api.structure_editable(
            fefco, ecma, a.get("recipe"), _dims(a), a.get("material"), job
        ), []
    if name == "job_init_structure":
        fefco, ecma = _code_args(a.get("code"))
        found = api.structure_params(
            fefco, ecma, a.get("recipe"), *_dims(a), a.get("basis") or "inner"
        )
        if isinstance(found, Issue):
            return Result(command="job init", job=a["job"]).fail(found, exit_code=2), []
        style, params = found
        from pacxio.engine.catalog import default_material

        material = a.get("material") or (
            default_material(a["code"]) if a.get("code") else "sbs_350"
        )
        return api.init_job(Path(a["job"]), style, params, material), []
    if name == "structure_update":
        return api.structure_update(Path(a["job"]), a["patch"]), []
    if name == "job_next":
        from pacxio.studio.bridge import acknowledge
        from pacxio.studio.next import next_step

        directory = Path(a["job"])
        result = next_step(directory)
        if result.ok:
            acknowledge(directory, result.data["revision"])
        return result, []
    if name == "studio_wait":
        from pacxio.studio.bridge import wait_for_user

        return wait_for_user(Path(a["job"]), a["after_revision"], a.get("timeout_s", 40)), []
    from pacxio import mcp_cli

    if name == "pacxio_run":
        return mcp_cli.run(a["command"], a.get("args")), []
    if name == "pacxio_help":
        try:
            return mcp_cli.help_text(a.get("command")), []
        except KeyError as e:  # 没有这条命令：作为工具结果报错（带建议），不是协议错误
            return ({"ok": False, "command": "pacxio_help",
                     "errors": [{"id": "MCP", "message": str(e).strip("'\""), "fix": t("Without command, list every command")}]}, []), []  # fmt: skip
    if name == "user_confirm":
        return mcp_cli.confirm(
            a["job"], a["action"], a["user_words"], a.get("target"), a.get("note"), a.get("region")
        ), []
    if name == "studio_open":
        return mcp_cli.studio_open(a["job"], a.get("port")), []
    raise KeyError(name)


def skill_dir() -> Path | None:
    """packaging-prepress Skill（流程和规矩）：源码仓库里的 skill/，或随包安装的副本 _skill/。"""
    here = Path(__file__).resolve()
    for d in (
        here.parents[2] / "skill" / "packaging-prepress",
        here.parent / "_skill" / "packaging-prepress",
    ):
        if (d / "SKILL.md").is_file():
            return d
    return None


def _resources() -> dict[str, tuple[str, str, Path]]:
    """Skill 的说明当 MCP 资源给没装 Skill 的 agent 读：SKILL.md 和 references/*.md。"""
    d = skill_dir()
    if d is None:
        return {}
    out = {
        "pacxio://skill/SKILL.md": (
            "packaging-prepress",
            t("Packaging design to print files, the whole workflow: stages, hard rules, commands"),
            d / "SKILL.md",
        )
    }
    for f in sorted((d / "references").glob("*.md")):
        first = next(
            (
                ln.strip("# ").strip()
                for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ),
            f.stem,
        )
        out[f"pacxio://skill/references/{f.name}"] = (f.stem, first[:120], f)
    return out


class UnknownMethod(Exception):
    """协议里没有的方法（回 -32601）；工具缺参数 / 未知工具是 KeyError（回 -32602）。"""


def _respond(
    out: TextIO, msg_id: Any, result: Any = None, error: dict[str, Any] | None = None
) -> None:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    out.write(json.dumps(body, ensure_ascii=False) + "\n")
    out.flush()


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """一条请求 → 结果（通知返回 None）。出错抛 ValueError / KeyError（参数、未知工具）或 UnknownMethod。"""
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        from pacxio import __version__

        return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": INSTRUCTIONS}  # fmt: skip
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        from pacxio.mcp_cli import images_of, truncate

        name, args = params["name"], params.get("arguments") or {}
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            raise KeyError(t("No tool {name}", name=name))
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
        if (
            missing
        ):  # 参数问题回协议错误（-32602）；参数齐了以后工具里面出的错按工具结果回（带原因）
            raise KeyError(t("Missing arguments: ") + ", ".join(missing))
        try:
            result, images = call(name, args)
        except Exception as e:  # noqa: BLE001
            return {"content": [{"type": "text", "text": json.dumps(
                {"ok": False, "command": name, "errors": [{"id": "MCP", "message": f"{type(e).__name__}: {e}"[:2000],
                 "fix": t("Read the message; if an argument is wrong, fix it per tools/list; if job.json is broken, repair it first")}]},
                ensure_ascii=False)}], "isError": True}  # fmt: skip
        pngs: list[bytes] = [Path(p).read_bytes() for p in images]
        if isinstance(result, tuple):  # 通用工具：(结果 JSON, 图片)
            result, pngs = result
        if isinstance(result, dict):
            body = result
            if not pngs and params["name"] not in ("pacxio_help",):
                pngs = images_of(body)
        else:
            body = json.loads(result.model_dump_json())
        content: list[dict[str, Any]] = [
            {"type": "text", "text": truncate(json.dumps(body, ensure_ascii=False, indent=2))}
        ]
        for png in pngs:
            content.append(
                {
                    "type": "image",
                    "data": base64.b64encode(png).decode("ascii"),
                    "mimeType": "image/png",
                }
            )
        ok = body.get("ok", True) if isinstance(body, dict) else True
        return {"content": content, "isError": not ok}
    if method == "resources/list":
        return {"resources": [{"uri": uri, "name": name, "mimeType": "text/markdown", "description": desc}
                              for uri, (name, desc, _) in _resources().items()]}  # fmt: skip
    if method == "resources/read":
        uri = params["uri"]
        found = _resources().get(uri)
        if found is None:
            raise KeyError(t("No resource {uri}", uri=uri))
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": found[2].read_text(encoding="utf-8"),
                }
            ]
        }
    if method is None or method.startswith("notifications/"):
        return None
    raise UnknownMethod(method)


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Serve requests while bounded waits remain cancellable; stdout is JSON-RPC only."""
    from pacxio.studio.bridge import WAIT_CANCEL

    output_lock = threading.Lock()
    pending: dict[Any, tuple[threading.Event, threading.Thread]] = {}

    def respond(msg_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> None:
        with output_lock:
            _respond(stdout, msg_id, result, error)

    def process(msg: dict[str, Any], cancel: threading.Event | None = None) -> None:
        msg_id = msg.get("id")
        token = WAIT_CANCEL.set(cancel)
        try:
            result = handle(msg)
        except UnknownMethod as e:
            if msg_id is not None:
                respond(msg_id, error={"code": -32601, "message": f"unknown method {e}"})
        except (KeyError, ValueError, TypeError) as e:
            if msg_id is not None:
                respond(msg_id, error={"code": -32602, "message": f"invalid params: {e}"})
        except Exception as e:  # noqa: BLE001 — 工具内部错误按 MCP 的约定回成工具结果，不让服务退出
            if msg_id is not None:
                respond(msg_id, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                                 "isError": True})  # fmt: skip
        else:
            if msg_id is not None and result is not None:
                respond(msg_id, result)
        finally:
            WAIT_CANCEL.reset(token)

    try:
        for line in stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                respond(None, error={"code": -32700, "message": "parse error"})
                continue
            if not isinstance(msg, dict):
                respond(None, error={"code": -32600, "message": "invalid request"})
                continue
            pending = {key: value for key, value in pending.items() if value[1].is_alive()}
            params = msg.get("params") or {}
            if msg.get("method") == "notifications/cancelled":
                entry = pending.get(params.get("requestId")) if isinstance(params, dict) else None
                if entry:
                    entry[0].set()
                continue
            msg_id = msg.get("id")
            if (
                msg.get("method") == "tools/call"
                and isinstance(params, dict)
                and params.get("name") == "studio_wait"
                and msg_id is not None
            ):
                if msg_id in pending:
                    respond(msg_id, error={"code": -32600, "message": "request already pending"})
                    continue
                cancel = threading.Event()
                context = contextvars.copy_context()
                worker = threading.Thread(
                    target=context.run, args=(process, msg, cancel), daemon=True
                )
                pending[msg_id] = cancel, worker
                worker.start()
            else:
                process(msg)
    finally:
        for cancel, _ in pending.values():
            cancel.set()
        for _, worker in pending.values():
            worker.join(timeout=2)


__all__ = ["INSTRUCTIONS", "TOOLS", "call", "handle", "serve", "skill_dir"]
