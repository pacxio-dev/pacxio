"""Typer 入口。每个命令支持 --json，输出统一结构；退出码 0 成功 / 1 有 error / 2 参数错误 / 3 环境缺失。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

import typer

from pacxio import api
from pacxio.messages import lang as lang_now
from pacxio.messages import t
from pacxio.model.common import Issue, Result

app = typer.Typer(help=t("Packaging prepress engine"), no_args_is_help=True, add_completion=False)
job_app = typer.Typer(help=t("Jobs"), no_args_is_help=True)
styles_app = typer.Typer(help=t("Box style library"), no_args_is_help=True)
structures_app = typer.Typer(
    help=t(
        "Structure catalog (geometry engine: FEFCO and ECMA): search, show, preview, suggest by need"
    ),
    no_args_is_help=True,
)
env_app = typer.Typer(help=t("Environment"), no_args_is_help=True)
art_app = typer.Typer(help=t("Artwork placements"), no_args_is_help=True)
surface_app = typer.Typer(
    help=t("Surface treatments (lamination / varnish; not print spot colors)"), no_args_is_help=True
)
app.add_typer(surface_app, name="surface")
color_app = typer.Typer(
    help=t(
        "Key colors: Pantone reference + print CMYK for the factory to match (callouts only; print content is unchanged)"
    ),
    no_args_is_help=True,
)
app.add_typer(color_app, name="color")
finish_app = typer.Typer(help=t("Finishes (foil / spot UV / emboss …)"), no_args_is_help=True)
attach_app = typer.Typer(
    help=t(
        "Attachments (metal badges … made separately and fixed on at assembly; not printed, no finish plate)"
    ),
    no_args_is_help=True,
)
window_app = typer.Typer(
    help=t("Windows (clear or open windows on body panels)"), no_args_is_help=True
)
concept_app = typer.Typer(
    help=t(
        "Concept images: what the finished box should look like, drawn by AI to agree the design direction (never in print files)"
    ),
    no_args_is_help=True,
)
app.add_typer(concept_app, name="concept")
font_app = typer.Typer(help=t("Brand fonts (supplied by the user)"), no_args_is_help=True)
app.add_typer(font_app, name="font")
visual_app = typer.Typer(help=t("Visual assets: import / generate / prepare"), no_args_is_help=True)
app.add_typer(visual_app, name="visual")
app.add_typer(art_app, name="art")
app.add_typer(finish_app, name="finish")
app.add_typer(attach_app, name="attach")
app.add_typer(window_app, name="window")
restore_app = typer.Typer(
    help=t("Concept restore: confirmed flat → printable final artwork"),
    no_args_is_help=True,
)
app.add_typer(restore_app, name="restore")
app.add_typer(job_app, name="job")
app.add_typer(styles_app, name="styles")
app.add_typer(structures_app, name="structures")
mcp_app = typer.Typer(
    help=t(
        "Local MCP server (stdio): structures, workflow commands, user confirmation; install adds it to an agent, doctor checks it"
    ),
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")

update_app = typer.Typer(help=t("Automatic update status"), no_args_is_help=True)
app.add_typer(update_app, name="update")


@update_app.command(
    "status",
    help=t("Show the running version and any prepared update without contacting the network."),
)
def update_status_cmd(json_out: JsonOpt = False) -> None:
    from pacxio.launcher import update_message, update_status

    r = Result(command="update status")
    r.data = update_status()
    r.data["message"] = update_message(r.data)
    if not json_out:
        typer.echo(r.data["message"])
    _emit(r, json_out)


@mcp_app.command(
    "serve",
    help=t(
        "Serve MCP tools (structures, workflow commands, confirmations) over stdin / stdout, JSON-RPC 2.0, one message per line. Packaged plugins check for updates in the background."
    ),
)
def mcp_serve() -> None:
    """按 MCP（JSON-RPC 2.0，每行一条）在 stdin / stdout 上提供工具（结构、全流程命令、确认），不联网。"""
    from pacxio.mcp_server import serve

    serve()


@mcp_app.command(
    "install",
    help=t(
        "Add the local MCP server to an agent. Without --yes it only previews; the original file is backed up before writing. Runs with this Python, so PATH does not matter."
    ),
)
def mcp_install_cmd(
    client: Annotated[
        str, typer.Option("--client", help="claude-code | codex | claude-desktop")
    ] = "claude-code",
    scope: Annotated[
        str,
        typer.Option(
            "--scope", help=t("project (this project only, Claude Code) | user (all projects)")
        ),
    ] = "project",
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help=t("Write the change; without it, only show which file would change and what"),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """把本地 MCP 装进 agent：不加 --yes 只预览；写之前备份原文件。用当前这个 Python 跑，不依赖 PATH。"""
    from pacxio.mcp_install import install

    _emit(install(client, scope, yes), json_out)


@mcp_app.command(
    "doctor",
    help=t(
        "Self-check: start the MCP server once, do the handshake, count tools; list which agents have it and whether their registered command runs."
    ),
)
def mcp_doctor_cmd(json_out: JsonOpt = False) -> None:
    """自检：起一次 MCP 服务走握手、数工具；列出装在哪些 agent 里、那边登记的命令能不能跑。"""
    from pacxio.mcp_install import doctor

    _emit(doctor(), json_out)


@app.command(
    "setup",
    help=t(
        "One-step install: add the pacxio plugin (Skill + MCP) to Claude Code / Codex (all found agents when --agent is not given), download fonts if missing, then self-check. Without --yes it only previews."
    ),
)
def setup_cmd(
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help=t(
                "claude-code | codex; leave out to install into every agent found on this machine"
            ),
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help=t("Install for real; without it, only list what would be done")),
    ] = False,
    fonts: Annotated[
        bool,
        typer.Option(
            "--fonts/--no-fonts",
            help=t("Download Source Han Sans if missing (about 33 MB, uses the network)"),
        ),
    ] = True,
    json_out: JsonOpt = False,
) -> None:
    """一句话安装：把 pacxio 插件（Skill + MCP）装进 Claude Code / Codex（不给 --agent 时本机有几个装几个），缺字体时下载，最后自检。不加 --yes 只预览。"""
    from pacxio.plugin_setup import setup

    r = setup(agent, yes, fonts)
    if not json_out and r.data.get("steps"):
        typer.echo((t("To do") if not yes else t("Done")) + ":")
        for s in r.data["steps"]:
            typer.echo(f"  - {s}")
    _emit(r, json_out)


app.add_typer(env_app, name="env")

JsonOpt = Annotated[bool, typer.Option("--json", help=t("Print machine-readable JSON"))]


def _emit(result: Result, as_json: bool) -> None:
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        mark = "✓" if result.ok else "✗"
        typer.echo(f"{mark} {result.command}")
        for a in result.artifacts:
            typer.echo(t("  created {path}", path=a))
        for i in result.errors:
            typer.echo(
                t("  error [{id}] {message}", id=i.id, message=i.message)
                + (t("\n    fix: {fix}", fix=i.fix) if i.fix else "")
            )
        for i in result.warnings:
            typer.echo(
                t("  warning [{id}] {message}", id=i.id, message=i.message)
                + (t("\n    suggestion: {fix}", fix=i.fix) if i.fix else "")
            )
    raise typer.Exit(result.exit_code)


@env_app.command(
    "check",
    help=t(
        "Check dependencies, Ghostscript and profiles; report what is missing and how to install it."
    ),
)
def env_check(json_out: JsonOpt = False) -> None:
    """检查依赖、Ghostscript、配置档，输出缺什么、怎么装。"""
    r = api.check_env()
    if not json_out:
        d: dict[str, Any] = r.data
        typer.echo(
            t(
                "Python {python}  Ghostscript: {gs}  Fonts: {font}",
                python=d["python"],
                gs=d.get("ghostscript") or t("not installed"),
                font=d.get("font") or t("not downloaded"),
            )
        )
    _emit(r, json_out)


@styles_app.command("list", help=t("List box styles with their parameters and defaults."))
def styles_list(json_out: JsonOpt = False) -> None:
    """列出盒型、参数表、默认值。"""
    r = api.list_styles()
    if not json_out:
        for s in r.data["styles"]:
            typer.echo(
                t(
                    "{id:10} {name}  refs {refs}  materials {materials}",
                    id=s["id"],
                    name=s["name"],
                    refs=s["refs"],
                    materials=s["material_types"],
                )
            )
            for name, spec in s["params"].items():
                typer.echo(
                    t(
                        "    {name}: default {default}",
                        name=name,
                        default=spec.get("default", t("required")),
                    )
                )
    _emit(r, json_out)


def _tri(yes: bool, no: bool) -> bool | None:
    return True if yes else False if no else None


@structures_app.command("list", help=t("List the structure catalog (calibrated first)."))
def structures_list(
    query: Annotated[
        str | None,
        typer.Option(
            "--search", help=t("Keyword (searched in code, description, closure and tags)")
        ),
    ] = None,
    series: Annotated[
        str | None, typer.Option("--series", help=t("Series: 01–09; A for ECMA cartons"))
    ] = None,
    tags: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help=t(
                "Tag, repeatable: shipping ecommerce display insert carton handle window hang_hole hole multi_part three_piece no_tape reclosable"
            ),
        ),
    ] = None,
    reclosable: Annotated[
        bool,
        typer.Option("--reclosable", help=t("Only structures that can be opened and closed again")),
    ] = False,
    no_tape: Annotated[
        bool, typer.Option("--no-tape", help=t("Only structures that close without tape or glue"))
    ] = False,
    material: Annotated[
        str | None, typer.Option("--material", help="corrugated | folding_carton")
    ] = None,
    calibrated: Annotated[
        bool, typer.Option("--calibrated", help=t("Only calibrated structures"))
    ] = False,
    lang: Annotated[
        str | None,
        typer.Option(
            "--lang",
            help=t("zh | en (language for human-readable text; default follows PACXIO_LANG)"),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """列结构目录（已校准的排前面）。"""
    r = api.structures_list(query, series, tags, reclosable or None, no_tape or None, material,
                            "calibrated" if calibrated else None, lang or lang_now())  # fmt: skip
    if not json_out and r.ok:
        for e in r.data["structures"]:
            mark = "★" if e["maturity"] == "calibrated" else " "
            series_name = e.get("series_zh") or e.get("series_name", "")
            typer.echo(
                f"{mark} {e['catalog']} {e['code']:13} {series_name[:6]:6}  {e['description'][:60]}"
            )
        typer.echo(
            t(
                "{count} structures (★ calibrated; the rest are read from the catalogue drawing, values are initial)",
                count=r.data["count"],
            )
        )
    _emit(r, json_out)


@structures_app.command(
    "show", help=t("Catalog details of one structure; with --L --W --H also prints its recipe.")
)
def structures_show(
    code: Annotated[str, typer.Argument(help=t("FEFCO / ECMA code"))],
    length: Annotated[float | None, typer.Option("--L")] = None,
    width: Annotated[float | None, typer.Option("--W")] = None,
    height: Annotated[float | None, typer.Option("--H")] = None,
    lang: Annotated[
        str | None,
        typer.Option(
            "--lang",
            help=t("zh | en (language for human-readable text; default follows PACXIO_LANG)"),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """一款结构的目录信息；给了 --L --W --H 再附配方。"""
    _emit(api.structure_show(code, length, width, height, lang or lang_now()), json_out)


@structures_app.command(
    "preview",
    help=t(
        "Draw the dieline and a 3D view without making a job (colored by role, structure only)."
    ),
)
def structures_preview(
    code: Annotated[str, typer.Argument(help=t("FEFCO / ECMA code"))],
    out: Annotated[Path, typer.Option("--out", help=t("Output folder"))],
    length: Annotated[float, typer.Option("--L")],
    width: Annotated[float, typer.Option("--W")],
    height: Annotated[float, typer.Option("--H")],
    material: Annotated[str | None, typer.Option("--material")] = None,
    json_out: JsonOpt = False,
) -> None:
    """不建 job，出刀版图和 3D 图（按角色上色，只看结构）。"""
    _emit(api.structure_preview(code, length, width, height, out, material), json_out)


@structures_app.command(
    "suggest",
    help=t(
        "Pick structures from the catalog for the product's needs (catalog first; co-design only if nothing fits)."
    ),
)
def structures_suggest(
    uses: Annotated[
        list[str] | None,
        typer.Option(
            "--use",
            help=t(
                "Use, repeatable: shipping ecommerce display insert carton handle window hang_hole hole multi_part three_piece"
            ),
        ),
    ] = None,
    reclosable: Annotated[
        bool, typer.Option("--reclosable", help=t("Must open and close again"))
    ] = False,
    no_tape: Annotated[
        bool, typer.Option("--no-tape", help=t("Must close without tape or glue"))
    ] = False,
    material: Annotated[
        str | None, typer.Option("--material", help="corrugated | folding_carton")
    ] = None,
    single: Annotated[
        bool, typer.Option("--single", help=t("Single piece only (no multi-part sets)"))
    ] = False,
    query: Annotated[str | None, typer.Option("--search", help=t("Extra keyword"))] = None,
    limit: Annotated[int, typer.Option("--limit")] = 3,
    length: Annotated[
        float | None, typer.Option("--L", help=t("With sizes and --out, draw a preview of each"))
    ] = None,
    width: Annotated[float | None, typer.Option("--W")] = None,
    height: Annotated[float | None, typer.Option("--H")] = None,
    out: Annotated[Path | None, typer.Option("--out")] = None,
    json_out: JsonOpt = False,
) -> None:
    """按产品需求从目录挑方案（先目录，都不满足再共创）。"""
    dims = (length, width, height) if None not in (length, width, height) else None
    result = api.structures_suggest(uses, reclosable or None, no_tape or None, material, single or None,
                                    query, limit, dims, out)  # type: ignore[arg-type]  # fmt: skip
    if not json_out:
        for row in result.data["suggestions"]:
            structure = row["structure"]
            typer.echo(f"{structure['code']}  {structure['description']}")
            for reason in row["why"]:
                typer.echo(f"  - {reason}")
    _emit(result, json_out)


@structures_app.command(
    "files",
    help=t(
        "Write dieline files (DXF / CF2 / dieline PDF / SVG) without making a job; same functions as the delivery pack (including die corner radii)."
    ),
)
def structures_files(
    code: Annotated[
        str,
        typer.Argument(
            help=t("Structure code: FEFCO (0201), ECMA (A20.20.01.01) or a common combination")
        ),
    ],
    length: Annotated[float, typer.Option("--L", help=t("Inside length mm"))],
    width: Annotated[float, typer.Option("--W", help=t("Inside width mm"))],
    height: Annotated[float, typer.Option("--H", help=t("Inside height mm"))],
    out: Annotated[Path, typer.Option("--out", help=t("Output folder"))],
    material: Annotated[
        str | None,
        typer.Option("--material", help=t("Material (default: the structure's default)")),
    ] = None,
    fmt: Annotated[
        list[str] | None,
        typer.Option("--format", help=t("dxf | cf2 | pdf | svg, repeatable; default all")),
    ] = None,
    basis: Annotated[
        str,
        typer.Option("--basis", help=t("inner (inside dimensions) | outer (outside dimensions)")),
    ] = "inner",
    json_out: JsonOpt = False,
) -> None:
    """不建 job，直接出刀版文件（DXF / CF2 / 刀版 PDF / SVG）；和交付包里的刀版同一套函数（含刀模圆角）。"""
    _emit(api.structure_files(code, length, width, height, out, material, fmt, basis), json_out)


@structures_app.command(
    "check",
    help=t(
        "Practical check: how it closes, whether it recloses, tape-free or not, interference when folded, maturity — each compared with the needs."
    ),
)
def structures_check(
    job: Annotated[
        Path | None,
        typer.Argument(help=t("Existing job (otherwise use --fefco / --ecma / --recipe)")),
    ] = None,
    fefco_code: Annotated[str | None, typer.Option("--fefco")] = None,
    ecma_code: Annotated[str | None, typer.Option("--ecma")] = None,
    recipe_file: Annotated[
        Path | None, typer.Option("--recipe", help=t("Recipe JSON file"))
    ] = None,
    length: Annotated[float | None, typer.Option("--L")] = None,
    width: Annotated[float | None, typer.Option("--W")] = None,
    height: Annotated[float | None, typer.Option("--H")] = None,
    material: Annotated[str | None, typer.Option("--material")] = None,
    needs: Annotated[
        list[str] | None,
        typer.Option(
            "--need", help=t("Need to check against, repeatable: reclosable no_tape single")
        ),
    ] = None,
    load_kg: Annotated[
        float | None,
        typer.Option(
            "--load-kg",
            help=t(
                "Load weight (the engine does not calculate strength; it only reminds the factory to check)"
            ),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """实用性检查：靠什么合上、能否反复开合、免不免胶带、折好后穿插、成熟度，逐条对照需求。"""
    recipe = None
    if recipe_file is not None:
        try:
            recipe = json.loads(recipe_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise typer.BadParameter(t("Cannot read the recipe file: {error}", error=e)) from e
    _emit(api.structure_check(fefco_code, ecma_code, recipe, (length, width, height), material, needs, load_kg,
                              job), json_out)  # fmt: skip


@structures_app.command(
    "update",
    help=t(
        "Change structure parameters (see structures editable), material or die corner radii and regenerate the dieline; existing confirmations expire."
    ),
)
def structures_update(
    job: Annotated[Path, typer.Argument(help=t("Job folder"))],
    sets: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            help=t(
                "path=value, repeatable: top.tuck_len=20, tuning.friction_lock=false, lug_w=24; value null removes it"
            ),
        ),
    ] = None,
    patch: Annotated[
        str | None, typer.Option("--patch", help=t("JSON object, merged layer by layer"))
    ] = None,
    material: Annotated[
        str | None,
        typer.Option(
            "--material",
            help=t("Change material preset (the box switches between carton board and corrugated)"),
        ),
    ] = None,
    corner_outer: Annotated[
        str | None,
        typer.Option(
            "--corner-outer",
            help=t("Die outer-corner radius mm; 0 = none, auto = by material (carton board 2)"),
        ),
    ] = None,
    corner_inner: Annotated[
        str | None,
        typer.Option(
            "--corner-inner",
            help=t("Die inner-corner radius mm; 0 = none, auto = by material (carton board 1)"),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """改结构参数（能改哪些看 structures editable）、换材料或调刀模圆角，重新生成刀版；已有确认会过期。"""
    merged: dict[str, Any] = {}
    if patch:
        try:
            merged = json.loads(patch)
        except ValueError as e:
            raise typer.BadParameter(t("--patch is not JSON: {error}", error=e)) from e
        if not isinstance(merged, dict):
            raise typer.BadParameter(t("--patch must be a JSON object"))
    for item in sets or []:
        path, sep, raw = item.partition("=")
        if not sep or not path:
            raise typer.BadParameter(t("--set must be path=value: {item}", item=item))
        try:
            value: Any = json.loads(raw)
        except ValueError:
            value = raw
        cur = merged
        keys = path.split(".")
        for k in keys[:-1]:
            nxt = cur.setdefault(k, {})
            if not isinstance(nxt, dict):
                raise typer.BadParameter(t("--set path conflict: {path}", path=path))
            cur = nxt
        cur[keys[-1]] = value
    corners: dict[str, Any] = {}
    for key, given in (("outer", corner_outer), ("inner", corner_inner)):
        if given is None:
            continue
        try:
            corners[key] = None if given.strip().lower() == "auto" else float(given)
        except ValueError as e:
            raise typer.BadParameter(
                t("--corner-{key} must be a number or auto: {given}", key=key, given=given)
            ) from e
    if not merged and not material and not corners:
        raise typer.BadParameter(
            t("Give --set / --patch / --material / --corner-outer / --corner-inner")
        )
    _emit(api.structure_update(job, merged, material, corners or None), json_out)


@structures_app.command(
    "editable",
    help=t(
        "Editable items: what can change, what each change moves, which automatic values follow the size, how panels connect, locking mechanisms."
    ),
)
def structures_editable(
    job: Annotated[
        Path | None,
        typer.Argument(help=t("Existing job (otherwise use --fefco / --ecma / --recipe)")),
    ] = None,
    fefco_code: Annotated[str | None, typer.Option("--fefco")] = None,
    ecma_code: Annotated[str | None, typer.Option("--ecma")] = None,
    recipe_file: Annotated[
        Path | None, typer.Option("--recipe", help=t("Recipe JSON file"))
    ] = None,
    length: Annotated[float | None, typer.Option("--L")] = None,
    width: Annotated[float | None, typer.Option("--W")] = None,
    height: Annotated[float | None, typer.Option("--H")] = None,
    material: Annotated[str | None, typer.Option("--material")] = None,
    lang: Annotated[
        str | None,
        typer.Option(
            "--lang",
            help=t("zh | en (language for human-readable text; default follows PACXIO_LANG)"),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """可编辑项：能改什么、改了动到哪儿、尺寸变了哪些自动值跟着变、面怎么连、锁合机构。"""
    recipe = None
    if recipe_file is not None:
        try:
            recipe = json.loads(recipe_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise typer.BadParameter(t("Cannot read the recipe file: {error}", error=e)) from e
    result = api.structure_editable(
        fefco_code, ecma_code, recipe, (length, width, height), material, job, lang or lang_now()
    )
    if result.ok and not json_out:
        _print_editable(result.data)
    _emit(result, json_out)


def _print_editable(data: dict[str, Any]) -> None:
    route = data["route"]
    typer.echo(
        t("Build route: {route}", route=ROUTE_ZH.get(route.split(":")[0], route))
        + (t(" ({variant})", variant=route.split(":")[1]) if ":" in route else "")
    )
    for group, title in (("size", t("Size parameters")), ("option", t("Construction options"))):
        rows = [p for p in data["parameters"] if p["group"] == group]
        if not rows:
            continue
        typer.echo(t("\n{title}:", title=title))
        for p in rows:
            if p["auto"]:
                cur = (
                    t("auto ({value:g}{unit})", value=p["auto_value"], unit=p["unit"])
                    if "auto_value" in p
                    else t("auto")
                )
            else:
                cur = f"{p['value']}{p['unit']}"
            span = ", ".join(map(str, p["options"])) if "options" in p else ""
            n = sum(len(v) for v in p["affects"].values())
            notes = []
            if p.get("both_ends"):
                notes.append(t("both ends change together"))
            if p.get("changes_route"):
                notes.append(
                    t("some values switch the build route: ")
                    + ", ".join(f"{k}→{v}" for k, v in p["changes_route"].items())
                )
            tail = f"  [{'; '.join(notes)}]" if notes else ""
            typer.echo(
                t(
                    "  {path:<28} now {cur:<14} {span}  moves {n} places  {label}{tail}",
                    path=p["path"],
                    cur=cur,
                    span=(t("options ") + span) if span else "",
                    n=n,
                    label=p["label"],
                    tail=tail,
                )
            )
    if data["dependencies"]:
        typer.echo(t("\nAutomatic values that follow the size:"))
        for dim, items in data["dependencies"].items():
            typer.echo(f"  {dim} → {', '.join(items)}")
    st = data["structure"]
    typer.echo(t("\nLines: ") + ", ".join(f"{k} {v}" for k, v in st["line_counts"].items()))
    typer.echo(t("Locking mechanisms:"))
    for m in st["mechanisms"]:
        if m["type"] == "closure":
            typer.echo(
                t("  {end}: {kind}", end=m["end"], kind=m["kind"])
                + (t(" (on {on})", on=m["on"]) if m.get("on") is not None else "")
            )
        elif m["type"] == "tab_slot":
            typer.echo(t("  {tab} goes into the slot on {slot}", tab=m["tab"], slot=m["slot_in"]))
        elif m["type"] == "glue":
            typer.echo(
                t(
                    "  glue: {panels} ({areas} glue areas)",
                    panels=", ".join(m["panels"]) or "—",
                    areas=m["areas"],
                )
            )


ROUTE_ZH = {"style": t("hand-built style"), "calibrated": t("calibrated construction"), "assembled": t("rectangular tube modules"), "tray": t("tray modules"), "sheet": t("flat sheet"),
            "native": t("engine native"), "set": t("multi-part set")}  # fmt: skip


@styles_app.command(
    "presets", help=t("List design uses and reference size presets (they do not add box styles).")
)
def styles_presets(
    style: Annotated[str | None, typer.Option("--style")] = None, json_out: JsonOpt = False
) -> None:
    """列出设计用途和参考尺寸预设（不增加盒型数量）。"""
    result = api.list_style_presets(style)
    if result.ok and not json_out:
        for p in result.data["presets"]:
            dims = p["params"]
            typer.echo(
                t(
                    "{id}  {name}  inside {L}x{W}x{H} mm  {material}  {use}",
                    id=p["id"],
                    name=p["name"],
                    L=dims["L"],
                    W=dims["W"],
                    H=dims["H"],
                    material=p["material"],
                    use=p["use"],
                )
            )
    _emit(result, json_out)


@styles_app.command(
    "calibrate",
    help=t(
        "Compare both ways with a local vector reference; writes a difference report and an overlay. Does not change the structure's verification level."
    ),
)
def styles_calibrate(
    style: Annotated[str, typer.Argument(help=t("Box style ID"))],
    reference: Annotated[
        Path, typer.Option("--ref", help=t("Local single-page vector PDF; nothing is downloaded"))
    ],
    size: Annotated[str, typer.Option("--size", help="LxWxH mm")],
    out: Annotated[Path, typer.Option("--out")] = Path("briefs/calibration"),
    material: Annotated[str, typer.Option("--material")] = "sbs_350",
    profile: Annotated[str, typer.Option("--profile")] = "default",
    tolerance: Annotated[float, typer.Option("--tolerance")] = 0.1,
    part: Annotated[str | None, typer.Option("--part")] = None,
    sets: Annotated[list[str] | None, typer.Option("--set")] = None,
    cut_color: Annotated[str, typer.Option("--cut-color", help=t("RGB 0–1, e.g. 1,0,0"))] = "1,0,0",
    crease_color: Annotated[str, typer.Option("--crease-color")] = "0,1,0",
    json_out: JsonOpt = False,
) -> None:
    """与本地矢量参考双向比对，生成差异报告和叠图；不改变结构验证等级。"""
    from pacxio.styles.calibration import calibrate

    options: dict[str, Any] = {}
    for item in sets or []:
        key, eq, value = item.partition("=")
        if not eq or not key.strip():
            raise typer.BadParameter(t("--set needs key=value"))
        options[key.strip()] = value.strip()

    def color(value: str) -> tuple[float, float, float]:
        try:
            values = [float(v) for v in value.split(",")]
            if len(values) != 3 or not all(0 <= v <= 1 for v in values):
                raise ValueError
            return values[0], values[1], values[2]
        except ValueError as error:
            raise typer.BadParameter(
                t("Line color must be three 0–1 RGB numbers separated by commas")
            ) from error

    _emit(
        calibrate(
            style,
            reference,
            size,
            material,
            profile,
            out,
            tolerance,
            part,
            options,
            color(cut_color),
            color(crease_color),
        ),
        json_out,
    )


@job_app.command(
    "init",
    help=t(
        "Create a job and generate its dieline (job.json + dieline.svg). --fefco / --ecma / --recipe / --combo use the geometry engine (style structure)."
    ),
)
def job_init(
    directory: Annotated[Path, typer.Argument(help=t("Job folder (created if missing)"))],
    style: Annotated[str, typer.Option("--style")] = "rte",
    length: Annotated[float | None, typer.Option("--L", help=t("Length (front width) mm"))] = None,
    width: Annotated[float | None, typer.Option("--W", help=t("Width (side width) mm"))] = None,
    height: Annotated[float | None, typer.Option("--H", help=t("Height mm"))] = None,
    basis: Annotated[str, typer.Option("--basis", help="inner | outer")] = "inner",
    material: Annotated[
        str | None,
        typer.Option(
            "--material", help=t("Material preset; default sbs_350 (flute_b for FEFCO codes)")
        ),
    ] = None,
    fefco_code: Annotated[
        str | None,
        typer.Option(
            "--fefco",
            help=t("Geometry engine: build the structure from a FEFCO code (e.g. 0201, 0427.1)"),
        ),
    ] = None,
    ecma_code: Annotated[
        str | None,
        typer.Option(
            "--ecma",
            help=t("Geometry engine: build a carton from a public ECMA code (e.g. A20.20.03.01)"),
        ),
    ] = None,
    recipe_file: Annotated[
        Path | None,
        typer.Option(
            "--recipe",
            help=t("Geometry engine: structure recipe JSON file (multi-part sets use set / parts)"),
        ),
    ] = None,
    combo_code: Annotated[
        str | None,
        typer.Option(
            "--combo",
            help=t(
                "Geometry engine: common combination (list them with structures list --series C, e.g. tear_display_tray)"
            ),
        ),
    ] = None,
    profile: Annotated[str, typer.Option("--profile")] = "default",
    print_method: Annotated[
        str | None,
        typer.Option(
            "--print-method",
            help=t(
                "offset | flexo (water-based) | digital; default by style: slotted boxes flexo, labels digital, the rest offset"
            ),
        ),
    ] = None,
    sets: Annotated[
        list[str] | None,
        typer.Option(
            "--set", help=t("Other style parameters key=value, repeatable, e.g. --set tongue_w=30")
        ),
    ] = None,
    liner: Annotated[
        str | None,
        typer.Option("--liner", help=t("Corrugated liner: kraft | white (default white)")),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """创建 job 并生成刀版（job.json + dieline.svg）。--fefco / --ecma / --recipe / --combo 走几何引擎（盒型 structure）。"""
    if combo_code:
        if fefco_code or ecma_code or recipe_file:
            raise typer.BadParameter(t("Give only one of --combo and --fefco / --ecma / --recipe"))
        fefco_code = combo_code  # 常用组合按编号走（api.structure_params 认得）
    if fefco_code or ecma_code or recipe_file:
        recipe = None
        if recipe_file is not None:
            try:
                recipe = json.loads(recipe_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise typer.BadParameter(t("Cannot read the recipe file: {error}", error=e)) from e
        found = api.structure_params(fefco_code, ecma_code, recipe, length, width, height, basis)
        if isinstance(found, Issue):
            _emit(Result(command="job init", job=str(directory)).fail(found, exit_code=2), json_out)
            return
        st, engine_params = found
        from pacxio.engine.catalog import default_material

        code = fefco_code or ecma_code
        mat = material or (default_material(code) if code else "sbs_350")
        _emit(
            api.init_job(directory, st, engine_params, mat, profile, print_method, liner), json_out
        )
        return
    if length is None or width is None or height is None:
        raise typer.BadParameter(
            t("Give --L --W --H (or build an engine structure with --fefco / --ecma / --recipe)")
        )
    material = material or "sbs_350"
    params: dict[str, Any] = {"L": length, "W": width, "H": height}
    from pacxio.styles import get_style

    try:
        has_dim_basis = "dim_basis" in get_style(style).param_schema().model_fields
    except KeyError:
        has_dim_basis = False  # api.init_job reports an unknown style
    if basis != "inner" or has_dim_basis:
        params["dim_basis"] = basis
    for item in sets or []:
        key, eq, value = item.partition("=")
        if not eq or not key.strip():
            raise typer.BadParameter(t("--set needs key=value, got {item!r}", item=item))
        key = key.strip()
        if "." in key:  # 部件参数：--set lid.lugs=false → params["lid"]["lugs"]
            part, sub = key.split(".", 1)
            params.setdefault(part, {})[sub] = value.strip()
        else:
            params[key] = value.strip()
    r = api.init_job(directory, style, params, material, profile, print_method, liner)
    _emit(r, json_out)


@job_app.command(
    "init-preset",
    help=t("Create an ordinary job from a design preset; size and material can be overridden."),
)
def job_init_preset(
    directory: Annotated[Path, typer.Argument(help=t("New job folder"))],
    preset_id: Annotated[str, typer.Argument(help=t("ID listed by styles presets"))],
    sets: Annotated[
        list[str] | None,
        typer.Option("--set", help=t("Override parameter key=value, e.g. --set H=45")),
    ] = None,
    material: Annotated[str | None, typer.Option("--material")] = None,
    profile: Annotated[str, typer.Option("--profile")] = "default",
    liner: Annotated[str | None, typer.Option("--liner", help="white | kraft")] = None,
    json_out: JsonOpt = False,
) -> None:
    """按设计预设建立普通 job，可覆盖尺寸和材料。"""
    params: dict[str, Any] = {}
    for item in sets or []:
        key, eq, value = item.partition("=")
        if not eq or not key.strip():
            raise typer.BadParameter(t("--set needs key=value, got {item!r}", item=item))
        params[key.strip()] = value.strip()
    _emit(
        api.init_job_from_preset(directory, preset_id, params, material, profile, liner), json_out
    )


def _pairs(items: list[str] | None, what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        key, sep, val = item.rpartition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(t("{what} must be name=value: {item}", what=what, item=item))
        out[key.strip()] = val.strip()
    return out


@job_app.command(
    "liner",
    help=t(
        "Change the liner of a corrugated job: the confirmation image and 3D show the board color (ink over kraft), and the spec sheet states it."
    ),
)
def job_liner(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    liner: Annotated[str, typer.Argument(help=t("kraft liner | white liner"))],
    json_out: JsonOpt = False,
) -> None:
    """改瓦楞 job 的面纸：刀版确认图、3D 按纸色显示（油墨压在牛皮色上），规格书写明。"""
    _emit(api.set_liner(directory, liner), json_out)


@app.command(
    "next",
    help=t(
        'Where the job stands, who it waits on, what to do next (same source as the workbench\'s "Now"); run it before every round of work.'
    ),
)
def next_cmd(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """现在到哪一步、等谁、下一步做什么（和工作台的"现在"同一个来源）；每一轮动手前先跑。"""
    from pacxio.studio.next import next_step

    r = next_step(directory)
    if r.ok:
        from pacxio.studio.bridge import acknowledge

        acknowledge(directory, r.data["revision"])
    if not json_out and r.ok:
        d: dict[str, Any] = r.data
        typer.echo(t("Now: {now}", now=d["now"]))
        if d["stage"] is not None and d["step"]:
            typer.echo(t("Next: {step}", step=d["step"]))
        for c in d["commands"]:
            typer.echo(f"  $ {c}")
        if d["read"]:
            typer.echo(t("Read: ") + ", ".join(d["read"]))
        typer.echo(
            t("Stages: ") + " · ".join(f"{s['no']} {s['title']} {s['label']}" for s in d["stages"])
        )
    _emit(r, json_out)


@app.command(
    "zoom",
    help=t(
        "Read small text on the confirmation image: crop a region at full resolution (200 DPI) into studio/zoom/.\n\nIn the open-source version the confirmation image comes back as a 100 DPI watermarked preview; crops are requested from the cloud (watermarked, up to 24 per image)."
    ),
)
def zoom_cmd(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    panel: Annotated[
        str | None,
        typer.Option("--panel", help=t("One panel (multi-part: full name, e.g. lid.top)")),
    ] = None,
    box: Annotated[
        str | None,
        typer.Option(
            "--box", help=t("Or a region x0,y0,x1,y1: 0–1 relative, origin at the image's top left")
        ),
    ] = None,
    image: Annotated[
        str, typer.Option("--image", help=t("Which image (path inside the job)"))
    ] = "preview.png",
    json_out: JsonOpt = False,
) -> None:
    """看清确认图上的小字：取一块原分辨率（200 DPI）的局部，存到 studio/zoom/。

    开源版里确认图回传的是 100 DPI 加水印的预览，局部向云端要（带水印，同一张最多 24 次）。"""
    from pacxio.zoom import zoom

    _emit(zoom(directory, panel=panel, box=_floats(box, 4, "--box"), image=image), json_out)


@app.command(
    "panels",
    help=t("Panel map: size, orientation, safe area and suggested 300 ppi pixels for every panel."),
)
def panels(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """输出面映射：每个面的尺寸、方向、安全区、300ppi 建议像素。"""
    r = api.get_panel_map(directory)
    if not json_out and r.ok:
        d: dict[str, Any] = r.data
        maps = d["parts"].items() if "parts" in d else [(None, d)]
        for part, m in maps:
            title = t("[{label} {part}]", label=d["part_labels"][part], part=part) if part else ""
            typer.echo(
                t(
                    "{title}Blank {w} x {h} mm, with bleed {pw} x {ph} px@300ppi; bleed {bleed} mm, safe margin {safe} mm",
                    title=title,
                    w=m["blank_mm"]["w"],
                    h=m["blank_mm"]["h"],
                    pw=m["blank_with_bleed_px"]["w"],
                    ph=m["blank_with_bleed_px"]["h"],
                    bleed=m["bleed_mm"],
                    safe=m["safe_margin_mm"],
                )
            )
            for p in m["panels"]:
                flag = t(" (orientation to be confirmed)") if p["orientation_provisional"] else ""
                name = f"{part}.{p['id']}" if part else p["id"]
                typer.echo(
                    f"  {name:14} {p['role']:10} {p['size_mm']['w']:>7} x {p['size_mm']['h']:<7} mm"
                    f"  {p['px']['w']} x {p['px']['h']} px{flag}"
                )
            body = m.get("body")
            if body:
                faces = " → ".join(f"{f['id']} {f['x0']:g}–{f['x1']:g}" for f in body["faces"])
                typer.echo(
                    t(
                        "Body wrap (--space body, origin at the front panel's bottom left): {circ:g} mm around; {faces}; the seam (glue-flap edge) is at x={seam:g}",
                        circ=body["circumference_mm"],
                        faces=faces,
                        seam=body["seam_x_mm"],
                    )
                )
    _emit(r, json_out)


def _floats(text: str | None, n: int, what: str) -> list[float] | None:
    """解析 "a,b,c" 形式的数字列表；格式不对按参数错误退出（退出码 2）。"""
    if text is None:
        return None
    try:
        vals = [float(x) for x in text.split(",")]
    except ValueError:
        vals = []
    if len(vals) != n:
        raise typer.BadParameter(
            t("{what} needs {n} comma-separated numbers, e.g. ", what=what, n=n)
            + ",".join(["10"] * n)
        )
    return vals


def _hanging(text: str | None) -> str | float | None:
    """--hanging-indent：auto | 数值（mm）；格式不对按参数错误退出。"""
    if text is None:
        return None
    if text.strip().lower() == "auto":
        return "auto"
    try:
        return float(text)
    except ValueError:
        raise typer.BadParameter(
            t(
                "--hanging-indent must be auto or millimetres (e.g. 4.5; 0 = no indent), got {text!r}",
                text=text,
            )
        ) from None


VERTICAL_HELP = t(
    "text: vertical (top to bottom, columns run right to left; punctuation set vertically, Latin and digits lie sideways), see references/fonts.md"
)
SKEW_X_HELP = t(
    "text: slant (degrees, -30–30); positive leans the top to the right, like italics; pivots on the ink center"
)
SKEW_Y_HELP = t(
    "text: rise (degrees, -30–30); positive rises to the right while verticals stay upright; pivots on the ink center"
)
HANGING_HELP = t(
    'text: hanging indent (numbered / bulleted lists). auto = continuation lines align with the text after markers like "1. " "• "; a number = that start in mm from the box\'s left edge; 0 = no indent'
)


def _names(text: str | None) -> list[str] | None:
    """解析 "a,b" 形式的名字列表（空的去掉）。"""
    return [x.strip() for x in text.split(",") if x.strip()] if text else None


ViewOpt = Annotated[
    str | None,
    typer.Option("--view", help=t("main view | back (back + left view) | flat (unfolded flat)")),
]
FacesOpt = Annotated[
    str | None,
    typer.Option(
        "--faces",
        help=t(
            "Panels in the image, comma-separated; for flats from left to right, e.g. front,side_b"
        ),
    ),
]


@art_app.command(
    "add",
    help=t(
        "Add a placement (fill / pattern / image / vector graphic / text) to job.json. Placements stack bottom to top in order."
    ),
)
def art_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    kind: Annotated[
        str,
        typer.Option("--kind", help=t("fill | pattern | image | vector | text | placeholder")),
    ],
    id_: Annotated[str | None, typer.Option("--id")] = None,
    tag: Annotated[
        str | None,
        typer.Option(
            "--tag",
            help=t(
                "Content label, e.g. brand / product name / net quantity (groups items on the confirmation page)"
            ),
        ),
    ] = None,
    color: Annotated[
        str | None,
        typer.Option(
            "--color",
            help=t(
                "CMYK percent c,m,y,k (fill / text; for vector the whole graphic prints in this color)"
            ),
        ),
    ] = None,
    colors: Annotated[
        str | None,
        typer.Option(
            "--colors",
            help=t("vector: SVG color → CMYK, e.g. '#3d4536=75,55,80,40;#c8a464=20,35,70,0'"),
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help=t(
                "Asset file (bitmap for image / pattern, SVG for vector); copied into job/assets"
            ),
        ),
    ] = None,
    space: Annotated[
        str | None,
        typer.Option(
            "--space",
            help=t(
                "Box coordinates for image / vector: panel (panel-local, default) | body (around the box body, continuous across folds) | blank (whole unfolded blank)"
            ),
        ),
    ] = None,
    clip: Annotated[
        str | None,
        typer.Option(
            "--clip",
            help=t("image / placeholder: clip with an SVG shape (e.g. arch, rounded top-right)"),
        ),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option(
            "--before", help=t("Insert before this placement (stacked under it); default on top")
        ),
    ] = None,
    content: Annotated[
        str | None,
        typer.Option(
            "--content",
            help=t(
                "Content list item id: the text (or graphic asset) comes from the list, so --text / --source are not needed"
            ),
        ),
    ] = None,
    style: Annotated[
        str | None,
        typer.Option(
            "--style",
            help=t(
                "text: text style (level) name; font, size etc. come from the style (see style set)"
            ),
        ),
    ] = None,
    label: Annotated[
        str | None,
        typer.Option(
            "--label",
            help=t(
                'placeholder / image / vector: what it is (for people), e.g. "bathroom photo" "code area"'
            ),
        ),
    ] = None,
    label_en: Annotated[
        str | None,
        typer.Option(
            "--label-en",
            help=t(
                'English description, used only in the English layout brief and image prompts; needed when --label is not English, e.g. "bathroom photo"'
            ),
        ),
    ] = None,
    panel: Annotated[
        str | None,
        typer.Option(
            "--panel",
            help=t(
                "Panel id (image / vector / text); multi-part styles use part.panel, e.g. lid.top"
            ),
        ),
    ] = None,
    part: Annotated[
        str | None,
        typer.Option(
            "--part",
            help=t(
                "Multi-part styles: the part (with --panel = part.panel; for fills / tiles / space=blank, which part)"
            ),
        ),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option(
            "--scope", help=t("outside_all or comma-separated panel ids (fill / pattern)")
        ),
    ] = None,
    box: Annotated[
        str | None,
        typer.Option(
            "--box",
            help=t(
                "x,y,w,h (mm): panel-local from the panel's bottom left; for space=body / blank see panels"
            ),
        ),
    ] = None,
    fit: Annotated[
        str | None,
        typer.Option(
            "--fit", help=t("cover | contain | stretch (images default cover, vectors contain)")
        ),
    ] = None,
    tile_mm: Annotated[
        float | None, typer.Option("--tile-mm", help=t("Pattern tile width mm"))
    ] = None,
    text: Annotated[
        str | None, typer.Option("--text", help=t("Text; use \\n for a line break"))
    ] = None,
    font: Annotated[str | None, typer.Option("--font", help=t("e.g. SourceHanSansSC-Bold"))] = None,
    size_pt: Annotated[float | None, typer.Option("--size-pt")] = None,
    align: Annotated[
        str | None,
        typer.Option("--align", help=t("left | center | right; vertical top | center | bottom")),
    ] = None,
    tracking: Annotated[
        float | None,
        typer.Option(
            "--tracking",
            help=t("text: tracking, 1/1000 em (e.g. 100 = 0.1 em wider, negative = tighter)"),
        ),
    ] = None,
    line_height: Annotated[
        float | None,
        typer.Option(
            "--line-height", help=t("text: line spacing, multiple of the font size (default 1.2)")
        ),
    ] = None,
    hanging_indent: Annotated[
        str | None, typer.Option("--hanging-indent", help=HANGING_HELP)
    ] = None,
    vertical: Annotated[bool, typer.Option("--vertical", help=VERTICAL_HELP)] = False,
    skew_x: Annotated[float | None, typer.Option("--skew-x", help=SKEW_X_HELP)] = None,
    skew_y: Annotated[float | None, typer.Option("--skew-y", help=SKEW_Y_HELP)] = None,
    json_out: JsonOpt = False,
) -> None:
    """增加一个图文放置（底色 / 图案 / 图片 / 矢量图形 / 文字），写进 job.json。按顺序自下而上叠放。"""
    c = _floats(color, 4, "--color")
    cmap = _color_map(colors)
    b = _floats(box, 4, "--box")
    sc: str | list[str] | None = scope
    if scope and "," in scope:
        sc = [x.strip() for x in scope.split(",") if x.strip()]
    if (
        part and panel and "." not in panel
    ):  # --part lid --panel top → "lid.top"（job.json 里只有一种写法）
        panel, part = f"{part}.{panel}", None
    fields = {
        "id": id_, "tag": tag, "kind": kind, "source": source, "panel": panel, "scope": sc,
        "box": dict(zip("xywh", b, strict=True)) if b else None,
        "fit": fit, "tile_mm": tile_mm, "text": text.replace("\\n", "\n") if text else None, "font": font, "size_pt": size_pt,
        "align": align, "color": {"cmyk": c} if c else None, "space": space, "clip": clip,
        "colors": cmap, "before": before, "tracking": tracking, "line_height": line_height,
        "content": content, "style": style, "label": label, "label_en": label_en, "part": part,
        "hanging_indent": _hanging(hanging_indent), "vertical": True if vertical else None,
        "skew_x": skew_x or None, "skew_y": skew_y or None,
    }  # fmt: skip
    _emit(api.art_add(directory, fields), json_out)


def _color_map(text: str | None) -> dict[str, dict[str, list[float]]] | None:
    """'#aabbcc=c,m,y,k;#ddeeff=c,m,y,k' → {hex: {"cmyk": [...]}}。"""
    if text is None:
        return None
    out: dict[str, dict[str, list[float]]] = {}
    for item in text.replace("\n", ";").split(";"):
        if not item.strip():
            continue
        if "=" not in item:
            raise typer.BadParameter(
                t("Each --colors item must be #RRGGBB=c,m,y,k, got {item!r}", item=item)
            )
        hexpart, cmyk = item.split("=", 1)
        vals = _floats(cmyk.strip(), 4, t("CMYK in --colors"))
        assert vals is not None
        out[hexpart.strip()] = {"cmyk": vals}
    return out


@art_app.command("move", help=t("Change the stacking order."))
def art_move(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    art_id: Annotated[str, typer.Argument(help=t("Placement id to move"))],
    before: Annotated[
        str | None,
        typer.Option("--before", help=t("Move before this placement (stacked under it)")),
    ] = None,
    top: Annotated[bool, typer.Option("--top", help=t("Move to the top"))] = False,
    json_out: JsonOpt = False,
) -> None:
    """调整叠放次序。"""
    r = api.art_move(directory, art_id, before, top)
    if not json_out and r.ok:
        typer.echo(t("Stacking order (bottom to top): ") + " → ".join(r.data["order"]))
    _emit(r, json_out)


@art_app.command(
    "fill",
    help=t(
        "Replace a placeholder with a real image / graphic: position, coordinate space and stacking order stay the same."
    ),
)
def art_fill(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    placeholder: Annotated[str, typer.Argument(help=t("Placeholder id to replace"))],
    source: Annotated[str, typer.Option("--source", help=t("The real image (or SVG graphic)"))],
    kind: Annotated[
        str | None, typer.Option("--kind", help=t("image | vector (default: by file extension)"))
    ] = None,
    clip: Annotated[
        str | None, typer.Option("--clip", help=t("Clip shape SVG (e.g. rounded top-right)"))
    ] = None,
    fit: Annotated[str | None, typer.Option("--fit", help="cover | contain | stretch")] = None,
    color: Annotated[
        str | None, typer.Option("--color", help=t("vector: single color c,m,y,k"))
    ] = None,
    colors: Annotated[
        str | None, typer.Option("--colors", help=t("vector: '#hex=c,m,y,k;…'"))
    ] = None,
    content: Annotated[
        str | None, typer.Option("--content", help=t("Content list item to use"))
    ] = None,
    id_: Annotated[
        str | None, typer.Option("--id", help=t("New id (default: keep the placeholder's id)"))
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """用真实图片 / 图形换掉占位框：位置、坐标系、叠放次序不变。"""
    c = _floats(color, 4, "--color")
    fields = {"source": source, "kind": kind, "clip": clip, "fit": fit, "color": {"cmyk": c} if c else None,
              "colors": _color_map(colors), "content": content, "id": id_}  # fmt: skip
    _emit(api.art_fill(directory, placeholder, fields), json_out)


@art_app.command(
    "same-face",
    help=t(
        "Same front and back: one panel's layout is the same as another's (one copy of the data; change one and both change)."
    ),
)
def art_same_face(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    target: Annotated[str, typer.Argument(help=t("Panel that should match another, e.g. back"))],
    source: Annotated[
        str | None, typer.Option("--as", help=t("Which panel it matches, e.g. front"))
    ] = None,
    clear: Annotated[bool, typer.Option("--clear", help=t("Undo"))] = False,
    json_out: JsonOpt = False,
) -> None:
    """正反同款：一个面的版面和另一个面相同（只有一份数据，改一处两面一起变）。"""
    _emit(api.art_same_face(directory, target, source, clear), json_out)


@art_app.command(
    "set",
    help=t(
        "Change a placement (position, style, color, shape …): id and stacking order stay; nothing changes if validation fails."
    ),
)
def art_set(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    art_id: Annotated[str, typer.Argument(help=t("Placement id to change"))],
    panel: Annotated[str | None, typer.Option("--panel", help=t("Panel id"))] = None,
    box: Annotated[str | None, typer.Option("--box", help=t("x,y,w,h (mm)"))] = None,
    space: Annotated[str | None, typer.Option("--space", help="panel | body | blank")] = None,
    style: Annotated[str | None, typer.Option("--style", help=t("text: text style name"))] = None,
    text: Annotated[
        str | None, typer.Option("--text", help=t("Text; may use {{item_id}} / {{item_id:N}}"))
    ] = None,
    content: Annotated[
        str | None, typer.Option("--content", help=t("Content list item to use"))
    ] = None,
    font: Annotated[str | None, typer.Option("--font")] = None,
    size_pt: Annotated[float | None, typer.Option("--size-pt")] = None,
    color: Annotated[str | None, typer.Option("--color", help=t("CMYK percent c,m,y,k"))] = None,
    colors: Annotated[
        str | None, typer.Option("--colors", help=t("vector: '#hex=c,m,y,k;…'"))
    ] = None,
    align: Annotated[
        str | None,
        typer.Option("--align", help=t("left | center | right; vertical top | center | bottom")),
    ] = None,
    fit: Annotated[str | None, typer.Option("--fit", help="cover | contain | stretch")] = None,
    clip: Annotated[
        str | None, typer.Option("--clip", help=t("image / placeholder: clip shape SVG"))
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help=t("Replace the asset file"))
    ] = None,
    label: Annotated[
        str | None,
        typer.Option("--label", help=t("placeholder / image / vector: what it is (for people)")),
    ] = None,
    label_en: Annotated[
        str | None,
        typer.Option(
            "--label-en",
            help=t("English description (only in the English layout brief and image prompts)"),
        ),
    ] = None,
    tag: Annotated[str | None, typer.Option("--tag", help=t("Content label"))] = None,
    tracking: Annotated[
        float | None, typer.Option("--tracking", help=t("Tracking, 1/1000 em"))
    ] = None,
    line_height: Annotated[
        float | None, typer.Option("--line-height", help=t("Line spacing multiple"))
    ] = None,
    hanging_indent: Annotated[
        str | None, typer.Option("--hanging-indent", help=HANGING_HELP)
    ] = None,
    vertical: Annotated[
        bool,
        typer.Option(
            "--vertical", help=VERTICAL_HELP + t("; back to horizontal with --clear vertical")
        ),
    ] = False,
    skew_x: Annotated[
        float | None, typer.Option("--skew-x", help=SKEW_X_HELP + t("; remove with --clear skew_x"))
    ] = None,
    skew_y: Annotated[
        float | None, typer.Option("--skew-y", help=SKEW_Y_HELP + t("; remove with --clear skew_y"))
    ] = None,
    clear: Annotated[
        list[str] | None,
        typer.Option(
            "--clear", help=t("Remove a field (repeatable), e.g. --clear clip --clear color")
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """修改一个放置（位置、样式、颜色、形状……）：id 与叠放次序不变；校验不过时不改动。"""
    c = _floats(color, 4, "--color")
    b = _floats(box, 4, "--box")
    fields = {
        "panel": panel, "box": dict(zip("xywh", b, strict=True)) if b else None, "space": space,
        "style": style, "text": text.replace("\\n", "\n") if text else None, "content": content,
        "font": font, "size_pt": size_pt, "color": {"cmyk": c} if c else None,
        "colors": _color_map(colors), "align": align, "fit": fit, "clip": clip, "source": source,
        "label": label, "label_en": label_en, "tag": tag, "tracking": tracking, "line_height": line_height,
        "hanging_indent": _hanging(hanging_indent), "vertical": True if vertical else None,
        "skew_x": skew_x, "skew_y": skew_y,
    }  # fmt: skip
    _emit(api.art_set(directory, art_id, fields, clear), json_out)


@art_app.command("remove", help=t("Delete a placement (the asset file stays in assets/)."))
def art_remove(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    art_id: Annotated[str, typer.Argument(help=t("Placement id to delete"))],
    json_out: JsonOpt = False,
) -> None:
    """删除一个放置（素材文件仍留在 assets/）。"""
    _emit(api.art_remove(directory, art_id), json_out)


NOTE_HELP = t(
    "Note for the factory, written into the spec sheet: the foil's real color (e.g. copper-gold hot foil, match the factory sample), gloss etc."
)


@finish_app.command("add", help=t("Add a finish (named spot color, overprint) to job.json."))
def finish_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    type_: Annotated[
        str,
        typer.Option(
            "--type",
            help="foil_gold | foil_silver | foil_copper | spot_uv | emboss | deboss | white",
        ),
    ],
    panel: Annotated[
        str | None,
        typer.Option(
            "--panel", help=t("Can be left out with --placement (uses that text's panel)")
        ),
    ] = None,
    box: Annotated[
        str | None,
        typer.Option(
            "--box",
            help=t(
                "Panel-local x,y,w,h (mm); can be left out when --content points to a logo on the layout (uses that logo's box)"
            ),
        ),
    ] = None,
    placement: Annotated[
        str | None,
        typer.Option(
            "--placement",
            help=t(
                "Follow a typeset text (text placement id): foil product name, badge lettering; the finish follows text changes"
            ),
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help=t(
                "SVG (preferred) or two-tone PNG; can be left out when --content points to a logo in the list"
            ),
        ),
    ] = None,
    content: Annotated[
        str | None, typer.Option("--content", help=t("Content list item to use (e.g. brand logo)"))
    ] = None,
    id_: Annotated[str | None, typer.Option("--id")] = None,
    tag: Annotated[
        str | None, typer.Option("--tag", help=t("Content label, e.g. brand mark"))
    ] = None,
    threshold: Annotated[
        int | None, typer.Option("--threshold", help=t("Bitmap threshold 1–254"))
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help=NOTE_HELP)] = None,
    process: Annotated[str | None, typer.Option("--process", help=PROCESS_HELP)] = None,
    json_out: JsonOpt = False,
) -> None:
    """增加一个工艺（命名专色、叠印），写进 job.json。"""
    b = _floats(box, 4, "--box")
    fields = {
        "id": id_, "tag": tag, "type": type_, "source": source, "text": placement, "panel": panel,
        "box": dict(zip("xywh", b, strict=True)) if b else None, "threshold": threshold,
        "content": content, "note": note, "process": process,
    }  # fmt: skip
    _emit(api.finish_add(directory, {k: v for k, v in fields.items() if v is not None}), json_out)


PROCESS_HELP = t(
    "Spot UV method: default is conventional (screen print, line width and gap from the profile's spot_uv); digital = digital UV (inkjet, from spot_uv_digital, finer lines and gaps) — only when the user has confirmed the factory can do digital UV"
)
ATTACH_SOURCE_HELP = t("Outline SVG (a round badge is one filled circle)")
ATTACH_RELIEF_HELP = t(
    "Raised pattern SVG (lettering, border, ornament; same artboard as the outline)"
)
PLATING_HELP = t("Plating: champagne_rose_gold | rose_gold | champagne_gold | gold | silver")


def _attach_fields(
    panel: str | None, box: str | None, source: str | None, relief: str | None, material: str | None,
    plating: str | None, thickness: float | None, tag: str | None, note: str | None,
) -> dict[str, Any]:  # fmt: skip
    b = _floats(box, 4, "--box") if box else None
    return {
        "panel": panel, "box": dict(zip("xywh", b, strict=True)) if b else None, "source": source,
        "relief": relief, "material": material, "plating": plating, "thickness_mm": thickness,
        "tag": tag, "note": note,
    }  # fmt: skip


@attach_app.command(
    "add",
    help=t(
        "Add an attachment (first version: metal badge, adhesive-backed, flat): not printed, no finish plate. Drawn in the confirmation image and 3D; the production files get a placement layer, the delivery pack a 1:1 artwork, and the spec sheet material, plating, thickness and position."
    ),
)
def attach_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    panel: Annotated[
        str,
        typer.Option(
            "--panel", help=t("Which panel it goes on (multi-part: full name, e.g. lid.top)")
        ),
    ],
    box: Annotated[
        str,
        typer.Option(
            "--box",
            help=t("Panel-local x,y,w,h (mm); the outline's artboard is fitted into this box"),
        ),
    ],
    source: Annotated[str, typer.Option("--source", help=ATTACH_SOURCE_HELP)],
    relief: Annotated[str | None, typer.Option("--relief", help=ATTACH_RELIEF_HELP)] = None,
    material: Annotated[
        str | None,
        typer.Option(
            "--material",
            help=t("zinc_alloy (die-cast zinc alloy, default) | aluminum (stamped aluminum)"),
        ),
    ] = None,
    plating: Annotated[str | None, typer.Option("--plating", help=PLATING_HELP)] = None,
    thickness: Annotated[
        float | None, typer.Option("--thickness", help=t("Thickness mm (default 3)"))
    ] = None,
    id_: Annotated[str | None, typer.Option("--id")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    note: Annotated[
        str | None,
        typer.Option(
            "--note",
            help=t("Note for the badge maker and box factory, written into the spec sheet"),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """加一个配件（第一版只有金属标，背胶平贴）：不印、不出工艺版；确认图、3D 里画出来，生产文件
    加贴标位置层，交付包附 1:1 图稿，规格书写材质、电镀色、厚度、位置。"""
    from pacxio.attachments import edit_attachments

    fields = _attach_fields(panel, box, source, relief, material, plating, thickness, tag, note)
    _emit(edit_attachments(directory, "add", {**fields, "id": id_}), json_out)


@attach_app.command("set", help=t("Change an attachment."))
def attach_set(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    item_id: Annotated[str, typer.Argument(help=t("Attachment id (see attach list)"))],
    panel: Annotated[str | None, typer.Option("--panel")] = None,
    box: Annotated[str | None, typer.Option("--box", help=t("Panel-local x,y,w,h (mm)"))] = None,
    source: Annotated[str | None, typer.Option("--source", help=ATTACH_SOURCE_HELP)] = None,
    relief: Annotated[
        str | None, typer.Option("--relief", help=ATTACH_RELIEF_HELP + t('; "" removes it'))
    ] = None,
    material: Annotated[str | None, typer.Option("--material")] = None,
    plating: Annotated[str | None, typer.Option("--plating", help=PLATING_HELP)] = None,
    thickness: Annotated[float | None, typer.Option("--thickness")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    note: Annotated[str | None, typer.Option("--note", help=t('--note "" removes it'))] = None,
    json_out: JsonOpt = False,
) -> None:
    """改一个配件。"""
    from pacxio.attachments import edit_attachments

    fields = _attach_fields(panel, box, source, relief, material, plating, thickness, tag, note)
    _emit(edit_attachments(directory, "set", fields, item_id), json_out)


@attach_app.command("remove")
def attach_remove(directory: Path, item_id: str, json_out: JsonOpt = False) -> None:
    from pacxio.attachments import edit_attachments

    _emit(edit_attachments(directory, "remove", item_id=item_id), json_out)


@attach_app.command("list")
def attach_list(directory: Path, json_out: JsonOpt = False) -> None:
    from pacxio.attachments import edit_attachments

    _emit(edit_attachments(directory, "list"), json_out)


@finish_app.command(
    "fit",
    help=t(
        "Make a finish graphic pass F02 (minimum line width and gap): writes a candidate SVG and a before/after image first; --apply replaces it."
    ),
)
def finish_fit(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    item_id: Annotated[str, typer.Argument(help=t("Finish id (see finish list)"))],
    close_mm: Annotated[
        float | None,
        typer.Option(
            "--close",
            help=t("Closing radius mm (fills gaps that are too narrow); default from the profile"),
        ),
    ] = None,
    open_mm: Annotated[
        float | None,
        typer.Option(
            "--open",
            help=t(
                "Opening radius mm (removes points that are too thin); default from the profile"
            ),
        ),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=t(
                "After the user has seen the comparison and agreed: switch to the fitted finish graphic"
            ),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """让工艺图形过 F02（最小线宽、间距）：先写候选 SVG 和前后对比图，--apply 才替换。"""
    from pacxio_pro.finishes.fit import fit_finish

    _emit(fit_finish(directory, item_id, close_mm, open_mm, apply), json_out)


@finish_app.command("list")
def finish_list(directory: Path, json_out: JsonOpt = False) -> None:
    from pacxio_pro.finishes.service import edit

    _emit(edit(directory, "finish", "list"), json_out)


@finish_app.command("remove")
def finish_remove(directory: Path, item_id: str, json_out: JsonOpt = False) -> None:
    from pacxio_pro.finishes.service import edit

    _emit(edit(directory, "finish", "remove", item_id=item_id), json_out)


@finish_app.command("set")
def finish_set(
    directory: Path,
    item_id: str,
    type_: Annotated[str | None, typer.Option("--type")] = None,
    panel: str | None = None,
    box: str | None = None,
    source: str | None = None,
    placement: Annotated[
        str | None,
        typer.Option("--placement", help=t("Follow this text instead (text placement id)")),
    ] = None,
    tag: str | None = None,
    content: str | None = None,
    threshold: int | None = None,
    note: Annotated[
        str | None, typer.Option("--note", help=NOTE_HELP + t('; --note "" removes it'))
    ] = None,
    process: Annotated[
        str | None,
        typer.Option("--process", help=PROCESS_HELP + t("; screen goes back to conventional")),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    from pacxio_pro.finishes.service import edit

    b = _floats(box, 4, "--box") if box else None
    _emit(
        edit(
            directory,
            "finish",
            "set",
            {
                "type": type_,
                "panel": panel,
                "box": dict(zip("xywh", b, strict=True)) if b else None,
                "source": source,
                "text": placement,
                "tag": tag,
                "content": content,
                "threshold": threshold,
                "note": note,
                "process": process,
            },
            item_id,
        ),
        json_out,
    )


def _surface_fields(
    type_: str | None,
    label: str | None,
    panels: str | None,
    part: str | None,
    exclusions: str | None,
    bonding_policy: str | None,
    note: str | None,
) -> dict[str, Any]:
    import json

    coverage = None
    if panels is not None or part is not None:
        coverage = {
            "scope": "panels" if panels and panels != "outside_all" else "outside_all",
            "panels": panels.split(",") if panels and panels != "outside_all" else None,
            "part": part,
        }
    try:
        exc = json.loads(exclusions) if exclusions is not None else None
    except ValueError as e:
        raise typer.BadParameter(t("--exclusions must be a JSON array")) from e
    return {
        "type": type_,
        "label": label,
        "coverage": coverage,
        "exclusions": exc,
        "bonding_policy": bonding_policy,
        "note": note,
    }


@surface_app.command(
    "add",
    help=t(
        "Record a lamination / varnish. --panels: comma-separated panel names; --exclusions: JSON [{panel,box,source?}]."
    ),
)
def surface_add(
    directory: Path,
    type_: Annotated[str, typer.Option("--type")],
    id_: Annotated[str | None, typer.Option("--id")] = None,
    label: str | None = None,
    panels: str = "outside_all",
    part: str | None = None,
    exclusions: str | None = None,
    bonding_policy: Annotated[
        str | None,
        typer.Option(
            help=t(
                "full_sheet covers the whole sheet, no knock-out at glue areas (default) / explicit_exclusions knocks out --exclusions"
            )
        ),
    ] = None,
    note: str | None = None,
    json_out: JsonOpt = False,
) -> None:
    """记录覆膜/光油。--panels 面名逗号分隔；--exclusions 为 [{panel,box,source?}] JSON。"""
    from pacxio_pro.finishes.service import edit

    fields = _surface_fields(type_, label, panels, part, exclusions, bonding_policy, note)
    fields["id"] = id_
    _emit(edit(directory, "surface", "add", fields), json_out)


@surface_app.command("set")
def surface_set(
    directory: Path,
    item_id: str,
    type_: Annotated[str | None, typer.Option("--type")] = None,
    label: str | None = None,
    panels: str | None = None,
    part: str | None = None,
    exclusions: str | None = None,
    bonding_policy: Annotated[
        str | None,
        typer.Option(
            help=t(
                "full_sheet covers the whole sheet, no knock-out at glue areas (default) / explicit_exclusions knocks out --exclusions"
            )
        ),
    ] = None,
    note: str | None = None,
    json_out: JsonOpt = False,
) -> None:
    from pacxio_pro.finishes.service import edit

    _emit(
        edit(
            directory,
            "surface",
            "set",
            _surface_fields(type_, label, panels, part, exclusions, bonding_policy, note),
            item_id,
        ),
        json_out,
    )


@surface_app.command("list")
def surface_list(directory: Path, json_out: JsonOpt = False) -> None:
    from pacxio_pro.finishes.service import edit

    _emit(edit(directory, "surface", "list"), json_out)


@surface_app.command("remove")
def surface_remove(directory: Path, item_id: str, json_out: JsonOpt = False) -> None:
    from pacxio_pro.finishes.service import edit

    _emit(edit(directory, "surface", "remove", item_id=item_id), json_out)


@color_app.command(
    "add",
    help=t(
        "Record a key color (the spec sheet's Colors section and the color swatches on the production files' callout layer). The engine does not convert Pantone numbers to CMYK."
    ),
)
def color_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    name: Annotated[
        str,
        typer.Option(
            "--name",
            help=t("Name, e.g. brand green; adding the same name again changes its values"),
        ),
    ],
    cmyk: Annotated[str, typer.Option("--cmyk", help=t("Print CMYK (0–100), e.g. 75,0,40,0"))],
    pantone: Annotated[
        str | None,
        typer.Option(
            "--pantone", help=t('Pantone number, e.g. "PANTONE 7473 C" (as given by the user)')
        ),
    ] = None,
    where: Annotated[
        str | None, typer.Option("--where", help=t("Where it is used, e.g. logo, title underline"))
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """记一个关键色（规格书「颜色」一节、生产文件标注层的色标）。潘通号对应的 CMYK 不由引擎换算。"""
    from pacxio_pro.color.keys import edit_key_colors

    fields = {"name": name, "cmyk": _floats(cmyk, 4, "--cmyk"), "pantone": pantone, "where": where}
    _emit(edit_key_colors(directory, "add", fields), json_out)


@color_app.command("list")
def color_list(directory: Path, json_out: JsonOpt = False) -> None:
    from pacxio_pro.color.keys import edit_key_colors

    _emit(edit_key_colors(directory, "list"), json_out)


@color_app.command("remove")
def color_remove(directory: Path, name: str, json_out: JsonOpt = False) -> None:
    from pacxio_pro.color.keys import edit_key_colors

    _emit(edit_key_colors(directory, "remove", name=name), json_out)


@window_app.command(
    "add",
    help=t(
        "Add a window: the opening is an inside cut (film is applied later by the factory). Decide it before concept images."
    ),
)
def window_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    panel: Annotated[
        str, typer.Option("--panel", help=t("Body panel: front | back | side_a | side_b"))
    ],
    box: Annotated[str, typer.Option("--box", help=t("Panel-local x,y,w,h (mm)"))],
    shape: Annotated[
        str | None,
        typer.Option("--shape", help=t("rect rounded rectangle (default) | circle | obround")),
    ] = None,
    radius: Annotated[
        float | None,
        typer.Option("--r", help=t("Corner radius mm (rect only; default from the profile)")),
    ] = None,
    film: Annotated[
        str | None,
        typer.Option(
            "--film",
            help=t(
                "Window film: pet (default) | none (open); only affects mockups and the spec sheet"
            ),
        ),
    ] = None,
    id_: Annotated[str | None, typer.Option("--id")] = None,
    json_out: JsonOpt = False,
) -> None:
    """开一个窗：窗口是内部切线（贴窗是工厂的后续工序）。要在出概念图之前定。"""
    b = _floats(box, 4, "--box")
    assert b is not None
    fields = {
        "id": id_, "panel": panel, "box": dict(zip("xywh", b, strict=True)), "shape": shape,
        "corner_r": radius, "film": film,
    }  # fmt: skip
    _emit(api.window_add(directory, fields), json_out)


@window_app.command(
    "list", help=t("List windows (panel-local), window film, and the areas text must avoid.")
)
def window_list(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """列出窗口（面局部坐标）、窗膜、排字要避开的区域。"""
    r = api.window_list(directory)
    if not json_out and r.ok:
        for w in r.data["windows"]:
            b = w["box_local_mm"]
            film = t("open") if w.get("film") == "none" else t("film")
            typer.echo(
                f"  {w['id']:4} {w['panel']:7} {w.get('shape', '')} "
                f"{b['x']:g},{b['y']:g},{b['w']:g},{b['h']:g}  {film}"
            )
    _emit(r, json_out)


@window_app.command("remove", help=t("Delete a window."))
def window_remove(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    window_id: Annotated[str, typer.Argument(help=t("Window id to delete"))],
    json_out: JsonOpt = False,
) -> None:
    """删除一个窗口。"""
    _emit(api.window_remove(directory, window_id), json_out)


def _size(text: str | None) -> tuple[int, int] | None:
    v = _floats(text.replace("x", ",") if text else None, 2, "--size")
    return (int(v[0]), int(v[1])) if v else None


@visual_app.command(
    "import",
    help=t(
        "Import an image into job/assets and record where it came from (supplied by the user / Codex built-in image generation)."
    ),
)
def visual_import(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    source: Annotated[
        Path | None, typer.Argument(help=t("Image path (PNG / JPEG / WebP; logos may be SVG)"))
    ] = None,
    role: Annotated[
        str,
        typer.Option(
            "--role",
            help=t(
                "hero | pattern | texture | logo | icon | graphic (flat-color shapes, to be traced as vectors)"
            ),
        ),
    ] = "hero",
    name: Annotated[
        str | None, typer.Option("--name", help=t("Asset name (default: file name)"))
    ] = None,
    codex_latest: Annotated[
        bool,
        typer.Option(
            "--codex-latest", help=t("Take the newest image in $CODEX_HOME/generated_images")
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """把图片导入 job/assets，并记录来源（用户提供的图片 / Codex 内置出图）。"""
    from pacxio.visual import assets

    _emit(assets.import_asset(directory, source, role, name, codex_latest), json_out)


@visual_app.command(
    "gen",
    help=t(
        'Generate an image with an API key (reads OPENAI_API_KEY). Constraints such as "no text" are always appended to the prompt.'
    ),
)
def visual_gen(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    prompt: Annotated[str, typer.Option("--prompt")],
    name: Annotated[str, typer.Option("--name")],
    role: Annotated[
        str,
        typer.Option(
            "--role",
            help=t(
                "hero | pattern | texture | logo | icon | graphic (flat-color shapes, to be traced as vectors)"
            ),
        ),
    ] = "hero",
    size: Annotated[
        str | None,
        typer.Option(
            "--size", help=t("Wanted pixel size WxH; the closest supported aspect ratio is used")
        ),
    ] = None,
    transparent: Annotated[
        bool,
        typer.Option(
            "--transparent",
            help=t("Ask for a pure green background, keyed out later with --chroma-key"),
        ),
    ] = False,
    provider: Annotated[str, typer.Option("--provider")] = "openai",
    json_out: JsonOpt = False,
) -> None:
    """用 API key 出图（读取环境变量 OPENAI_API_KEY）。提示词会被强制追加"无文字"等约束。"""
    from pacxio.visual import assets

    _emit(
        assets.generate_asset(directory, prompt, role, name, _size(size), transparent, provider),
        json_out,
    )


@visual_app.command(
    "trace",
    help=t(
        "Trace an image as vector SVG, then use art add --kind vector. Full illustrations are traced in full color (converted with ICC); line art, icons and flat graphics are traced as color areas (CMYK set by hand from brand colors). Without --method the engine picks by looking at the image."
    ),
)
def visual_trace(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    asset: Annotated[
        str, typer.Argument(help=t("Image to trace (path relative to the job, or asset name)"))
    ],
    colors: Annotated[
        int | None,
        typer.Option(
            "--colors",
            help=t(
                "shapes: number of colors (background not counted); when given, traces as color areas"
            ),
        ),
    ] = None,
    palette: Annotated[
        str | None,
        typer.Option(
            "--palette",
            help=t(
                "shapes: snap to these brand colors; vtracer: key colors (only very close colors snap), e.g. '#3d4536,#8a9a6e'"
            ),
        ),
    ] = None,
    keep_background: Annotated[
        bool,
        typer.Option(
            "--keep-background", help=t("Trace the background as a shape too (default: drop it)")
        ),
    ] = False,
    name: Annotated[
        str | None, typer.Option("--name", help=t("Output name (assets/<name>.svg)"))
    ] = None,
    trim: Annotated[
        bool,
        typer.Option(
            "--trim",
            help=t(
                "Crop the artboard to the graphic (icons, logos: removes the margin around AI images so they fill their box)"
            ),
        ),
    ] = False,
    lines: Annotated[
        bool,
        typer.Option(
            "--lines",
            help=t(
                "Trace thin lines (contours, outlines) as even-width strokes along their center line instead of merging them into color areas (the color count excludes line colors)"
            ),
        ),
    ] = False,
    wrap: Annotated[
        bool | None,
        typer.Option(
            "--wrap/--no-wrap",
            help=t(
                "Left and right ends meet (around the box body): join the opposite end before tracing so the seam does not break; used automatically for a full wrap made by restore strip"
            ),
        ),
    ] = None,
    background: Annotated[
        str | None,
        typer.Option(
            "--background",
            help=t(
                "Background color #hex (not traced); default: the background recorded by restore strip / face, else the most common color at the image edge; gradient: the background is a gradient, removed as a slowly changing background so only the illustration on top is traced (full-color tracing only)"
            ),
        ),
    ] = None,
    method: Annotated[
        str,
        typer.Option(
            "--method",
            help=t(
                "auto (pick by image) | shapes (line art, icons, flat-color graphics: trace color areas) | vtracer (full illustrations: full-color tracing)"
            ),
        ),
    ] = "auto",
    speckle: Annotated[
        int | None,
        typer.Option(
            "--speckle",
            help=t(
                "vtracer: merge specks smaller than this (pixels, default 4); raise it when there are too many shapes"
            ),
        ),
    ] = None,
    erase: Annotated[
        list[str] | None,
        typer.Option(
            "--erase",
            help=t(
                "Boxes x,y,w,h painted with the background before tracing (pixels, origin top left, repeatable): things drawn in the asset that should stay empty"
            ),
        ),
    ] = None,
    tones: Annotated[
        int | None,
        typer.Option(
            "--tones",
            help=t(
                "vtracer: merge non-background colors into this many levels (for one-color line art; 6 is chosen automatically for line art)"
            ),
        ),
    ] = None,
    strength: Annotated[
        float,
        typer.Option(
            "--strength",
            help=t(
                "vtracer: line strength, 1 = as is; 0.35 = pull colors toward the background, keeping 35% of the difference (for faded side scenery used as a background texture)"
            ),
        ),
    ] = 1.0,
    json_out: JsonOpt = False,
) -> None:
    """把图描成矢量 SVG，之后用 art add --kind vector。整片插画全彩描（颜色按 ICC 换算），
    线框、图标、纯色图形分色描（按品牌色手配 CMYK）；不给 --method 时看图选。"""
    from pacxio_pro.visual.service import trace_asset

    pal = [c.strip() for c in palette.split(",") if c.strip()] if palette else None
    boxes: list[tuple[int, int, int, int]] = []
    for box in erase or []:
        x, y, w, h = (round(v) for v in _floats(box, 4, "--erase") or (0.0, 0.0, 0.0, 0.0))
        boxes.append((x, y, w, h))
    _emit(
        trace_asset(
            directory,
            asset,
            colors,
            pal,
            keep_background,
            name,
            trim,
            lines=lines,
            wrap=wrap,
            background=background,
            method=method,
            speckle=speckle,
            erase=boxes or None,
            tones=tones,
            strength=strength,
        ),  # fmt: skip
        json_out,
    )


@visual_app.command(
    "prepare",
    help=t(
        "Prepare an asset: aspect-ratio check, green key / light background removal, seamless tile check and fix, upscaling, leftover text check. The original is not changed."
    ),
)
def visual_prepare(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    asset: Annotated[str, typer.Argument(help=t("Asset path (relative to the job) or asset name"))],
    role: Annotated[
        str,
        typer.Option(
            "--role",
            help=t(
                "hero | pattern | texture | logo | icon | graphic (flat-color shapes, to be traced as vectors)"
            ),
        ),
    ] = "hero",
    target_mm: Annotated[
        str | None,
        typer.Option(
            "--target-mm",
            help=t("Target area width,height (mm), for aspect-ratio and resolution checks"),
        ),
    ] = None,
    tile_mm: Annotated[
        float | None, typer.Option("--tile-mm", help=t("Tile width mm for pattern / texture"))
    ] = None,
    min_ppi: Annotated[float, typer.Option("--min-ppi")] = 300.0,
    chroma_key: Annotated[
        bool, typer.Option("--chroma-key", help=t("Key out a pure green background"))
    ] = False,
    remove_bg: Annotated[
        bool,
        typer.Option(
            "--remove-bg",
            help=t(
                "Make a light plain background (gradients and vignettes are fine) transparent: product photos and real shots then show the layout's background; only background connected to the image edge is removed"
            ),
        ),
    ] = False,
    background: Annotated[
        str | None,
        typer.Option(
            "--background",
            help=t(
                "Background color #hex for --remove-bg (default: the most common color at the image edge)"
            ),
        ),
    ] = None,
    tolerance: Annotated[
        float, typer.Option("--tolerance", help=t("Green-key tolerance 0–1"))
    ] = 0.35,
    no_repair: Annotated[
        bool, typer.Option("--no-repair", help=t("Only report pattern seams, do not fix them"))
    ] = False,
    upscale: Annotated[str, typer.Option("--upscale", help="auto | lanczos | none")] = "auto",
    check_text: Annotated[
        bool,
        typer.Option(
            "--check-text",
            help=t("Check the image for text with tesseract (optional, English only)"),
        ),
    ] = False,
    out: Annotated[
        str | None, typer.Option("--out", help=t("Output asset name (default <name>.prepared)"))
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """素材预处理：宽高比检查、抠绿 / 去浅色底、无缝平铺检查与修复、超分、文字残留检查。原图不改。"""
    from pacxio_pro.visual import service
    from pacxio_pro.visual.prepare import PrepareOptions

    if chroma_key and remove_bg:
        raise typer.BadParameter(
            t("Choose one of --chroma-key (pure green) and --remove-bg (light plain background)")
        )
    bg = None
    if background is not None:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
            raise typer.BadParameter(t("--background must be #rrggbb"))
        bg = (int(background[1:3], 16), int(background[3:5], 16), int(background[5:7], 16))
    tgt = _floats(target_mm, 2, "--target-mm")
    opts = PrepareOptions(
        role=role, target_mm=(tgt[0], tgt[1]) if tgt else None, tile_mm=tile_mm, min_ppi=min_ppi,
        chroma_key=chroma_key, remove_bg=remove_bg, background=bg, tolerance=tolerance,
        repair_seams=not no_repair, upscale=upscale, check_text=check_text,
    )  # fmt: skip
    _emit(service.prepare_asset(directory, asset, opts, out), json_out)


@concept_app.command(
    "gen",
    help=t(
        "Draw concept images with AI (the finished look; text allowed). The box style and real proportions go into the prompt."
    ),
)
def concept_gen(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    design: Annotated[
        str,
        typer.Option("--design", help=t("Design description: style, colors, graphics, layout …")),
    ],
    text: Annotated[
        list[str] | None,
        typer.Option(
            "--text",
            help=t("Text that appears on the box (repeatable; the AI draws it as written)"),
        ),
    ] = None,
    n: Annotated[
        int, typer.Option("--n", help=t("How many directions (1–4; each image is one generation)"))
    ] = 1,
    scene: Annotated[
        str | None,
        typer.Option(
            "--scene", help=t("Scene description (default: clean commercial product photography)")
        ),
    ] = None,
    provider: Annotated[str, typer.Option("--provider")] = "openai",
    json_out: JsonOpt = False,
) -> None:
    """用 AI 画概念图（成品样子，允许文字）。盒型与真实比例会写进提示词。"""
    from pacxio.concept import service

    _emit(service.generate_concepts(directory, design, text or [], n, scene, provider), json_out)


@concept_app.command(
    "import",
    help=t(
        "Import concept images made elsewhere (a designer, another AI tool, Codex built-in image generation)."
    ),
)
def concept_import(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    sources: Annotated[
        list[Path] | None,
        typer.Argument(
            help=t(
                "Concept image files (PNG / JPEG / WebP); for a flat you can give several candidates of the same image, only the most accurate proportion is imported"
            )
        ),
    ] = None,
    name: Annotated[
        str | None, typer.Option("--name", help=t("Concept id (default c1, c2 …)"))
    ] = None,
    codex_latest: Annotated[
        bool,
        typer.Option(
            "--codex-latest", help=t("Take the newest image in $CODEX_HOME/generated_images")
        ),
    ] = False,
    view: ViewOpt = None,
    faces: FacesOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """导入别处做好的概念图（设计师、其它 AI 工具、Codex 内置出图）。"""
    from pacxio.concept import service

    files = sources or []
    _emit(service.import_concept(directory, files[0] if files else None, name, codex_latest, view,
                                 _names(faces), files),
          json_out)  # fmt: skip


@concept_app.command(
    "tag",
    help=t(
        "Label an existing concept image with its view and panels (only the metadata changes, not the image)."
    ),
)
def concept_tag(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    concept_id: Annotated[str, typer.Argument(help=t("Concept id, e.g. c13-flat-front"))],
    view: Annotated[
        str,
        typer.Option(
            "--view", help=t("main view | back (back + left view) | flat (unfolded flat)")
        ),
    ],
    faces: FacesOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """给已有的概念图补标视角与面（只改 meta，图不动）。"""
    from pacxio.concept import service

    _emit(service.tag_concept(directory, concept_id, view, _names(faces)), json_out)


@concept_app.command(
    "prompt",
    help=t(
        "Write image prompts (no image is generated): size, ratio, layout brief and references filled in, written to concept-prompts/. main is the main view (same skeleton as concept gen, for Codex built-in or manual image generation)."
    ),
)
def concept_prompt(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    step: Annotated[
        str,
        typer.Option(
            "--step",
            help=t(
                "main view (step 3) | back (back + left view, step 4) | flat (unfolded flat, step 5)"
            ),
        ),
    ],
    design: Annotated[
        str | None,
        typer.Option(
            "--design",
            help=t("Design language (English): colors, type style, graphics, photo mood …"),
        ),
    ] = None,
    back_view: Annotated[
        str | None,
        typer.Option(
            "--back-view", help=t("Which back view the flat follows (default: the one in use)")
        ),
    ] = None,
    text: Annotated[
        list[str] | None,
        typer.Option(
            "--text",
            help=t("(main) Text that appears on the box, repeatable; the AI draws it as written"),
        ),
    ] = None,
    scene: Annotated[
        str | None,
        typer.Option(
            "--scene",
            help=t("(main) Scene description (default: clean commercial product photography)"),
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name", help=t("(main) Concept id, e.g. main-A (default: the next free id)")
        ),
    ] = None,
    brief: Annotated[
        bool,
        typer.Option(
            "--brief",
            help=t(
                "(main) Put the parts of the layout brief plan/brief.txt for the panels seen in the main view (front, right side, top) into the prompt (route A, plan first)"
            ),
        ),
    ] = False,
    body: Annotated[
        bool,
        typer.Option(
            "--body",
            help=t(
                "(flat) Draw the body wrap (back | left | front | right) as one image (default for single-part styles; also forced when the wrap is longer than 3:1)"
            ),
        ),
    ] = False,
    pairs: Annotated[
        bool,
        typer.Option(
            "--pairs",
            help=t(
                "(flat) Single-part styles: back to two panels per image (front + right side, back + left side)"
            ),
        ),
    ] = False,
    separate: Annotated[
        bool,
        typer.Option(
            "--separate",
            help=t(
                "(flat) Each panel stands alone: the wrap / two-panel image states that graphics do not cross folds (for designs that are not continuous)"
            ),
        ),
    ] = False,
    ref: Annotated[
        list[str] | None,
        typer.Option(
            "--ref",
            help=t(
                "(flat) Use a panel of the chosen design, straightened, as a composition reference: panel=concept_id:x0,y0,x1,y1,x2,y2,x3,y3 (top-left, top-right, bottom-right, bottom-left pixels of that panel upright in the image), repeatable"
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force", help=t("Overwrite a prompt file of the same name with different content")
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """生成出图提示词（不出图）：尺寸、比例、版式简报、参考图都填好，写到 concept-prompts/。
    main 是主视角（和 concept gen 同一个骨架，给 Codex 内置出图 / 手工出图用）。"""
    from pacxio.concept.prompts import make_prompts

    refs: dict[str, tuple[str, list[tuple[float, float]]]] = {}
    for item in ref or []:
        try:
            face, rest = item.split("=", 1)
            cid, nums = rest.split(":", 1)
            v = [float(x) for x in nums.split(",")]
        except ValueError:
            _emit(Result(command="concept prompt", job=str(directory), ok=False, exit_code=2, errors=[
                Issue(id="ARG", message=t("Wrong --ref format: {item}", item=item),
                      fix=t("panel=concept_id:x0,y0,x1,y1,x2,y2,x3,y3, e.g. lid.top=A:348,37,1432,203,1305,900,97,652"))]),
                  json_out)  # fmt: skip
            return
        refs[face.strip()] = (cid.strip(), list(zip(v[0::2], v[1::2], strict=False)))
    r = make_prompts(
        directory,
        step,
        design,
        back_view,
        force,
        text,
        scene,
        name,
        body,
        refs or None,
        pairs,
        separate,
        brief,
    )
    if not json_out and r.ok:
        typer.echo(t("  {n} images in all; generate them first, then import:", n=r.data["images"]))
        for row in r.data["prompts"]:
            typer.echo(f"    {row['import']}")
    _emit(r, json_out)


@concept_app.command(
    "rectify",
    help=t(
        "Straighten a flat: find the image edges and folds and save each panel at its real size in mm (still a concept image, reference only)."
    ),
)
def concept_rectify(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    concept_id: Annotated[str, typer.Argument(help=t("Flat id, e.g. c13-flat-front"))],
    px_per_mm: Annotated[
        float, typer.Option("--px-per-mm", help=t("Output pixels per millimetre"))
    ] = 4.0,
    grid: Annotated[
        bool,
        typer.Option("--grid", help=t("Overlay a 10 mm grid (origin bottom left) for measuring")),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """把展开平面图拉正：找画面边界与折线，每个面按真实毫米尺寸存一张（仍是概念图，只作参考）。"""
    from pacxio.concept import service

    r = service.rectify_concept(directory, concept_id, px_per_mm, grid)
    if not json_out and r.ok:
        for f in r.data["faces"]:
            typer.echo(
                t(
                    "  {face:8} source pixel box {box}  proportion error {err:+.1%}",
                    face=f["face"],
                    box=f["box_px"],
                    err=f["aspect_error"],
                )
            )
    _emit(r, json_out)


@concept_app.command(
    "list",
    help=t(
        "List the job's concept images (view, panels), which one was chosen, and which version is in use where there are several."
    ),
)
def concept_list(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """列出 job 里的概念图（视角、面），选定的是哪一张，改过几版的在用哪一版。"""
    from pacxio.concept import service

    r = service.list_concepts(directory)
    if not json_out and r.ok:
        for c in r.data["concepts"]:
            mark = t("  ← chosen") if c["chosen"] else ""
            if c["versions"] and c["versions"] > 1:  # 改过几版的背面视角 / 平面图：标出在用哪一版
                mark = t("  ← in use") if c["in_use"] else t("  (old version, not in use)")
            view = service.view_label(c["view"])
            faces = t(" ({faces})", faces=",".join(c["faces"])) if c["faces"] else ""
            typer.echo(
                t(
                    "{id:16} {view}{faces}  {file}  source {source}{mark}",
                    id=c["id"],
                    view=view,
                    faces=faces,
                    file=c["file"],
                    source=c["source"],
                    mark=mark,
                )
            )
        if not r.data["concepts"]:
            typer.echo(t("No concept images yet"))
    _emit(r, json_out)


@concept_app.command(
    "use",
    help=t(
        "Switch to another version of a back view / flat (the newest import is in use by default; run only when the user asks for an older one). Other versions are kept."
    ),
)
def concept_use(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    concept_id: Annotated[str, typer.Argument(help=t("Back view or flat id, e.g. c2-back1"))],
    by: Annotated[str | None, typer.Option("--by", help=t("Who decided"))] = None,
    note: Annotated[str | None, typer.Option("--note", help=t("Note"))] = None,
    json_out: JsonOpt = False,
) -> None:
    """改用某一版背面视角 / 平面图（默认最新导入的在用；用户说要用旧版时才跑）。其他版本留着。"""
    from pacxio.concept import service

    _emit(service.use_concept(directory, concept_id, by, note), json_out)


@concept_app.command(
    "choose",
    help=t(
        "Record the direction chosen by the decision maker and extract its main colors as the starting point for the final design."
    ),
)
def concept_choose(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    concept_id: Annotated[str, typer.Argument(help=t("Concept id, e.g. c2"))],
    region: Annotated[
        str | None,
        typer.Option(
            "--region",
            help=t(
                "Only take main colors from this region: x0,y0,x1,y1 (0–1, origin at the image's top left)"
            ),
        ),
    ] = None,
    by: Annotated[str | None, typer.Option("--by", help=t("Who chose"))] = None,
    note: Annotated[
        str | None,
        typer.Option("--note", help=t("Note (e.g. the decision maker's change requests)")),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """记录决策者选定的方向，并提取主色作为正式设计的起点。"""
    from pacxio.concept import service

    reg = _floats(region, 4, "--region")
    r = service.choose_concept(directory, concept_id, tuple(reg) if reg else None, by, note)  # type: ignore[arg-type]
    if not json_out and r.ok:
        for c in r.data["palette"]:
            typer.echo(
                t(
                    "  {hex}  share {share:.0%}  CMYK≈{cmyk}",
                    hex=c["hex"],
                    share=c["share"],
                    cmyk=c["cmyk"],
                )
            )
    _emit(r, json_out)


@concept_app.command(
    "confirm",
    help=t(
        "Record that the user confirmed the flats in use (the final artwork follows them). Confirm again after changing an image or switching versions."
    ),
)
def concept_confirm(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    by: Annotated[str | None, typer.Option("--by", help=t("Who confirmed (only the user)"))] = None,
    note: Annotated[str | None, typer.Option("--note", help=t("Note"))] = None,
    json_out: JsonOpt = False,
) -> None:
    """记录用户确认了在用的这组展开平面图（正式稿照它做）。换了图、改用别的版本后要重新确认。"""
    from pacxio.concept import service

    _emit(service.confirm_flats(directory, by, note), json_out)


@font_app.command(
    "add",
    help=t(
        "Add a brand font to job/fonts and read its license information. Text is outlined, so the delivery pack contains no font files."
    ),
)
def font_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    source: Annotated[
        Path, typer.Argument(help=t("Font file (OTF / TTF; TTC / WOFF / WOFF2 also work)"))
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help=t("Font name (for --font; default: the font's family name)")),
    ] = None,
    index: Annotated[int, typer.Option("--index", help=t("Which font in a TTC"))] = 0,
    accept_license_risk: Annotated[
        bool,
        typer.Option(
            "--accept-license-risk",
            help=t(
                "When the font's embedding permission is restricted: confirm you have checked the license with the rights holder"
            ),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """把品牌字体收进 job/fonts，读出授权信息。文字转曲后交付包里不含字体文件。"""
    from pacxio import fonts

    _emit(fonts.add_font(directory, source, name, index, accept_license_risk), json_out)


@font_app.command("list", help=t("List the job's brand fonts and their license information."))
def font_list(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """列出 job 里的品牌字体及其授权信息。"""
    from pacxio import fonts

    r = fonts.list_fonts(directory)
    if not json_out and r.ok:
        for f in r.data["job_fonts"]:
            cjk = t("has CJK") if f["has_cjk"] else t("no CJK")
            typer.echo(
                t(
                    "{name:20} {cjk}  embedding {embedding}  {license}",
                    name=f["name"],
                    cjk=cjk,
                    embedding=f["embedding"],
                    license=f["license"],
                )
            )
        if not r.data["job_fonts"]:
            typer.echo(t("No fonts of its own in this job yet"))
    _emit(r, json_out)


@app.command(
    "mockup",
    help=t(
        "Make exact mockups: 3D views, flat views of each panel, and a summary page with the content list (mockup/00_mockup_sheet.png)."
    ),
)
def mockup(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    require: Annotated[
        str | None,
        typer.Option(
            "--require",
            help=t(
                "Content labels that must appear, comma-separated, e.g. brand,product name,net quantity; confirmation is refused if one is missing"
            ),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """生成精确效果图：三维视角、各面平视、带信息清单的汇总页（mockup/00_mockup_sheet.png）。"""
    from pacxio.mockup import service

    req = [t for t in (require or "").split(",") if t.strip()]
    _emit(service.make_mockup(directory, req), json_out)


@app.command(
    "approve",
    help=t(
        "Confirm the dieline confirmation image (the only gate before export; run only when the user says so). Refused if the design changed after render or required content is missing."
    ),
)
def approve(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    by: Annotated[str | None, typer.Option("--by", help=t("Who confirmed"))] = None,
    note: Annotated[str | None, typer.Option("--note", help=t("Note"))] = None,
    status: Annotated[
        bool, typer.Option("--status", help=t("Only show the confirmation status, do not confirm"))
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """确认刀版确认图（导出前唯一的闸门；用户说了才跑）。设计在 render 之后改过、或必须出现的信息没齐，都不能确认。"""
    from pacxio.mockup import service

    _emit(service.approve_design(directory, by, note, status), json_out)


@app.command(
    "preflight",
    help=t(
        "Preflight: build the print files, run every rule, and write the report (out/preflight/). Exit code 1 when there are errors."
    ),
)
def preflight(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """印前检查：生成印刷文件并跑全部规则，输出报告（out/preflight/）。有 error 时退出码为 1。"""
    _emit(api.preflight_job(directory), json_out)


@app.command(
    "render",
    help=t("Quick iteration: only the flat preview preview.png and the dieline SVG, no preflight."),
)
def render(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """快速迭代：只出展开图预览 preview.png 与刀版 SVG，不跑印前检查。"""
    _emit(api.render_job(directory), json_out)


studio_app = typer.Typer(
    help=t(
        "Local workbench: a web page on this machine showing everything the job produced, by workflow stage, refreshing live"
    ),
    no_args_is_help=True,
)
app.add_typer(studio_app, name="studio")


def _emit_studio(result: Result, json_out: bool) -> None:
    if not json_out and result.ok:
        d = result.data
        if d.get("running"):
            reused = d.get("reused")
            how = (
                t("running")
                if reused is None
                else (t("already running, reused") if reused else t("started"))
            )
            typer.echo(
                t("Workbench: {url} ({how}; process {pid})", url=d["url"], how=how, pid=d["pid"])
            )
        elif d.get("stopped"):
            typer.echo(t("Workbench stopped (process {pid})", pid=d["pid"]))
        else:
            typer.echo(t("The workbench is not running"))
    _emit(result, json_out)


@studio_app.command(
    "start",
    help=t(
        "Start the workbench in the background (listens on this machine only) and return its address; reuses it if this job's workbench is already running."
    ),
)
def studio_start(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    port: Annotated[int, typer.Option("--port", help=t("Port; default: any free port"))] = 0,
    open_browser: Annotated[
        bool, typer.Option("--open", help=t("Open in the default browser"))
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """在后台起工作台（只监听本机），返回网址；这个 job 的工作台已经在跑就复用。"""
    from pacxio.studio.control import studio_start as start

    _emit_studio(start(directory, port, open_browser), json_out)


@studio_app.command(
    "wait", help=t("Wait for a Workbench decision without confirming for the user.")
)
def studio_wait(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    after_revision: Annotated[
        str, typer.Option("--after-revision", help=t("Revision returned by job_next or next"))
    ],
    timeout: Annotated[
        float, typer.Option("--timeout", min=0.01, max=45, help=t("Wait seconds (maximum 45)"))
    ] = 40,
    json_out: JsonOpt = False,
) -> None:
    from pacxio.studio.bridge import wait_for_user

    _emit(wait_for_user(directory, after_revision, timeout), json_out)


@studio_app.command("status", help=t("Whether this job's workbench is running, and its address."))
def studio_status(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """这个 job 的工作台在不在跑、网址是什么。"""
    from pacxio.studio.control import studio_status as status

    _emit_studio(status(directory), json_out)


@studio_app.command("stop", help=t("Stop this job's workbench."))
def studio_stop(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """关掉这个 job 的工作台。"""
    from pacxio.studio.control import studio_stop as stop

    _emit_studio(stop(directory), json_out)


@studio_app.command(
    "serve",
    help=t(
        "Run the workbench in the foreground (Ctrl+C to quit); agents usually use `studio start`."
    ),
)
def studio_serve(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    port: Annotated[int, typer.Option("--port", help=t("Port; default: any free port"))] = 0,
) -> None:
    """在前台运行工作台（Ctrl+C 退出）；agent 一般用 `studio start`。"""
    from pacxio import dielines
    from pacxio.studio.server import run

    job, errs = dielines.load_job(directory)
    if job is None:
        _emit(
            Result(ok=False, command="studio serve", job=str(directory), errors=errs, exit_code=2),
            False,
        )
    run(directory, port)


notes_app = typer.Typer(
    help=t(
        "Notes: comments the user writes on the workbench (with part, panel, position in mm and a snapshot); the agent reads and replies"
    ),
    no_args_is_help=True,
)
app.add_typer(notes_app, name="notes")
NOTE_STATUS_ZH = {
    "open": t("waiting for agent"),
    "answered": t("waiting for user"),
    "resolved": t("closed"),
}


@notes_app.command(
    "list",
    help=t(
        "List notes: text, position (part.panel, mm on the panel, elements in the box), snapshot, replies. Open notes by default."
    ),
)
def notes_list(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    open_only: Annotated[
        bool, typer.Option("--open", help=t("Only notes waiting for the agent"))
    ] = False,
    all_notes: Annotated[bool, typer.Option("--all", help=t("Include closed notes"))] = False,
    json_out: JsonOpt = False,
) -> None:
    """列出批注：内容、位置（部件.面、面内毫米、框里的元素）、截图、回复。默认列没关的。"""
    from pacxio.notes import list_notes

    r = list_notes(directory, "open" if open_only else "all" if all_notes else "unresolved")
    if not json_out and r.ok:
        for n in r.data["notes"]:
            typer.echo(
                t(
                    "{id} [{status}] {by}: {text}",
                    id=n["id"],
                    status=NOTE_STATUS_ZH[n["status"]],
                    by=n["by"],
                    text=n["text"],
                )
            )
            typer.echo(t("    where: {where}", where=n["where"]))
            if n["snapshot"]:
                typer.echo(t("    snapshot: {path}", path=directory / n["snapshot"]))
            for rep in n["replies"]:
                typer.echo(t("    {by}: {text}", by=rep["by"], text=rep["text"]))
        if not r.data["notes"]:
            typer.echo(t("No notes to handle"))
    _emit(r, json_out)


@notes_app.command(
    "reply",
    help=t(
        'The agent replies to a note; by default it is marked "waiting for user" (the user closes it on the workbench or replies again).'
    ),
)
def notes_reply(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    note_id: Annotated[str, typer.Argument(help=t("Note id, e.g. n3"))],
    text: Annotated[
        str, typer.Option("--text", help=t("Reply: what changed / what you want to ask"))
    ],
    keep_open: Annotated[
        bool,
        typer.Option(
            "--keep-open", help=t("Not finished: replied, but still waiting for the agent")
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """agent 回复一条批注；默认标成"等用户看"（用户在工作台上关掉或接着回复）。"""
    from pacxio.notes import reply_note

    _emit(reply_note(directory, note_id, text, keep_open=keep_open), json_out)


@notes_app.command(
    "add",
    help=t(
        'The agent leaves the user a message on the workbench (e.g. "tried both here, pick one"); without --panel / --box it is a general comment.'
    ),
)
def notes_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    text: Annotated[str, typer.Option("--text", help=t("Message"))],
    panel: Annotated[
        str | None,
        typer.Option(
            "--panel", help=t("Which panel of the final artwork it is on (multi-part: part.panel)")
        ),
    ] = None,
    box: Annotated[
        str | None,
        typer.Option("--box", help=t("Panel-local x,y,w,h (mm); zero width and height is a pin")),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """agent 在工作台上给用户留言（如"这里两种都试了，你选一个"）；不给 --panel / --box 是整体意见。"""
    from pacxio.notes import AGENT, add_note, add_note_at_panel

    vals = _floats(box, 4, "--box")
    if (panel is None) != (vals is None):
        _emit(Result(ok=False, command="notes add", job=str(directory), exit_code=2,
                     errors=[Issue(id="ARG", message=t("Give --panel and --box together"), fix=t("Give both, or neither (general comment)"))]),
              json_out)  # fmt: skip
    if panel is not None and vals is not None:
        _emit(
            add_note_at_panel(directory, text, panel, (vals[0], vals[1], vals[2], vals[3])),
            json_out,
        )
    _emit(add_note(directory, text, by=AGENT), json_out)


@notes_app.command(
    "resolve",
    help=t(
        "Close a note. The user closes their own notes on the workbench; the agent uses this only when the user says in the conversation that the note can be closed."
    ),
)
def notes_resolve(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    note_id: Annotated[str, typer.Argument(help=t("Note id"))],
    json_out: JsonOpt = False,
) -> None:
    """关掉一条批注。用户的批注由用户在工作台上关；只有用户在对话里明确说"这条可以关"时 agent 才用。"""
    from pacxio.notes import AGENT, resolve_note

    _emit(resolve_note(directory, note_id, by=AGENT), json_out)


@app.command(
    "export",
    help=t(
        'Export the delivery pack. With preflight errors it is refused, and only a preview marked "not passed" and the report are written.'
    ),
)
def export(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    proof: Annotated[bool, typer.Option("--proof", help=t("Also make a 1:1 proof sheet"))] = False,
    formats: Annotated[
        str | None,
        typer.Option(
            "--formats",
            help=t(
                "Comma-separated: print,finish,pdf,dxf,cf2,spec,report,preview,svg (default: the profile's deliverables)"
            ),
        ),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help=t("Output root (default <job>/out)"))
    ] = None,
    require_approval: Annotated[
        bool,
        typer.Option(
            "--require-approval",
            help=t(
                "Refuse to export when the mockup is not confirmed (or the design changed after confirmation)"
            ),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """导出交付包；印前检查有 error 时拒绝，只输出标注"未通过"的预览与检查报告。"""
    fmts = [f.strip() for f in formats.split(",") if f.strip()] if formats else None
    _emit(api.export_job(directory, fmts, proof, out, require_approval), json_out)


def main() -> None:
    from pacxio.cloud.route import main_hook

    main_hook()  # 闭源命令在云端模式下转给合成服务（）
    app()


# ---------------- 云端（、）----------------

cloud_app = typer.Typer(
    help=t(
        "Cloud compose service: sign in, sign out, status (server-side commands go there automatically in cloud mode)"
    ),
    no_args_is_help=True,
)
app.add_typer(cloud_app, name="cloud")


@app.command(
    "login",
    help=t(
        "Sign in to the pacxio cloud service: you get an address and a code; sign in in the browser with an email code (the first time creates the account), check the code and approve.\n\nThe token is stored in ~/.pacxio/cloud.json (readable only by you). Running a cloud command while signed out also starts sign-in."
    ),
)
def login_cmd(
    url: Annotated[
        str | None,
        typer.Option("--url", help=t("Service address (default https://api.pacxio.dev)")),
    ] = None,
    no_wait: Annotated[
        bool,
        typer.Option(
            "--no-wait",
            help=t(
                "Return as soon as the address and code are shown (for agents: rerun the cloud command after the user approves)"
            ),
        ),
    ] = False,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help=t("Do not open the browser"))
    ] = False,
    timeout: Annotated[int, typer.Option("--timeout", help=t("Maximum seconds to wait"))] = 900,
    json_out: JsonOpt = False,
) -> None:
    """登录 pacxio 云端服务：给一个网址和代码，在浏览器里用邮箱验证码登录（第一次就是注册）、核对代码后确认。

    令牌存在 ~/.pacxio/cloud.json（只有自己能读）。没登录时第一次跑云端命令也会自动开始登录。"""
    from pacxio.cloud import auth

    say = (lambda s: typer.echo(s, err=True)) if json_out else typer.echo
    r = auth.login(url, wait=not no_wait, browser=not no_browser, timeout_s=timeout, say=say)
    _emit(r, json_out)


@app.command(
    "logout",
    help=t(
        "Sign out of the pacxio cloud service: revoke this computer's token on the server and delete the token stored here."
    ),
)
def logout_cmd(json_out: JsonOpt = False) -> None:
    """退出 pacxio 云端服务：服务器上撤销这台电脑的令牌，删掉本机保存的令牌。"""
    from pacxio.cloud import auth

    _emit(auth.logout(), json_out)


@cloud_app.command(
    "login",
    help=t(
        "For development: store a token directly (in ~/.pacxio/cloud.json, readable only by you). Real accounts use `pacxio login`."
    ),
)
def cloud_login(
    token: Annotated[
        str,
        typer.Option(
            "--token", help=t("Fixed token for development (real accounts use `pacxio login`)")
        ),
    ],
    url: Annotated[
        str | None,
        typer.Option("--url", help=t("Service address (default https://api.pacxio.dev)")),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """开发用：直接保存一个令牌（存在 ~/.pacxio/cloud.json，只有自己能读）。正式账户用 `pacxio login`。"""
    from pacxio.cloud import config
    from pacxio.cloud.client import Client, CloudError

    target = (url or config.login().url).rstrip("/")
    r = Result(command="cloud login")
    try:
        Client(target, token).get_run("0" * 32)
    except CloudError as e:
        if e.id != "CLOUD_404":  # 404 = 令牌有效（只是没有这个任务）
            _emit(r.fail(Issue(id=e.id, message=e.message, fix=e.fix), 3), json_out)
    path = config.save(target, token)
    r.data = {"url": target, "saved": str(path)}
    _emit(r, json_out)


@cloud_app.command("logout", help=t("Same as `pacxio logout`."))
def cloud_logout(json_out: JsonOpt = False) -> None:
    """同 `pacxio logout`。"""
    from pacxio.cloud import auth

    r = auth.logout()
    r.command = "cloud logout"
    _emit(r, json_out)


@cloud_app.command(
    "status",
    help=t(
        "Service address, signed in or not, whether the service is reachable, and whether server-side commands run here or in the cloud."
    ),
)
def cloud_status(json_out: JsonOpt = False) -> None:
    """服务地址、是否登录、服务是否连得上、闭源命令现在走本机还是云端。"""
    from pacxio.cloud import config
    from pacxio.cloud.client import Client, CloudError

    login = config.login()
    r = Result(command="cloud status")
    email, reachable = None, False
    try:
        reachable = Client(login.url, login.token).health()
        if reachable and login.token:
            email = Client(login.url, login.token).me().get("email")
    except CloudError as e:
        reachable = reachable if e.id == "CLOUD_AUTH" else False
        r.warnings.append(Issue(id=e.id, message=e.message, fix=e.fix))
    signed_in = bool(login.token) and not any(w.id == "CLOUD_AUTH" for w in r.warnings)
    r.data = {"url": login.url, "signed_in": signed_in, "email": email, "reachable": reachable,
              "mode": "cloud" if config.use_cloud() else "local"}  # fmt: skip
    if not json_out:
        who = (
            t("signed in as {email}", email=email)
            if email
            else (t("signed in") if signed_in else t("signed out"))
        )
        typer.echo(t("Service {url}  {who}  {reach}  server-side commands run {where}", url=login.url, who=who, reach=t("reachable") if reachable else t("unreachable"), where=t("in the cloud") if r.data["mode"] == "cloud" else t("here")))  # fmt: skip
    _emit(r, json_out)


if __name__ == "__main__":
    main()


# ---------------- 信息清单（ 2.1）----------------

content_app = typer.Typer(
    help=t(
        "Content list: every piece of information on the pack (exact text, required / optional); the user confirms the wording before concept images"
    ),
    no_args_is_help=True,
)
app.add_typer(content_app, name="content")
conventions_app = typer.Typer(
    help=t("Reference tables: what usually goes on each panel for a product category"),
    no_args_is_help=True,
)
app.add_typer(conventions_app, name="conventions")

CategoryOpt = Annotated[
    str | None,
    typer.Option(
        "--category", help=t("Category: brand / product_name / selling_point / usage / barcode …")
    ),
]


@content_app.command(
    "import",
    help=t(
        "Import the list from a file (the agent writes it in one go after organising the brief)."
    ),
)
def content_import(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    file: Annotated[
        Path, typer.Argument(help=t("YAML / JSON: {conventions: paint, items: [...]}"))
    ],
    replace: Annotated[
        bool,
        typer.Option(
            "--replace", help=t("Replace the whole list with the file's (default: merge)")
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """从文件导入清单（agent 整理好需求文档后一次写入）。"""
    from pacxio import inventory

    _emit(inventory.content_import(directory, file, replace), json_out)


@content_app.command("add", help=t("Add an item."))
def content_add(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    category: CategoryOpt = None,
    text: Annotated[
        str | None,
        typer.Option("--text", help=t("Exact text (verbatim); use \\n for a line break")),
    ] = None,
    id_: Annotated[str | None, typer.Option("--id")] = None,
    asset: Annotated[
        str | None, typer.Option("--asset", help=t("Graphic asset such as a logo / icon / barcode"))
    ] = None,
    optional: Annotated[
        bool, typer.Option("--optional", help=t("Optional (default: must appear)"))
    ] = False,
    pending: Annotated[
        str | None,
        typer.Option(
            "--pending",
            help=t(
                'Not decided yet: say what is missing, e.g. "drying time to come from the brand"'
            ),
        ),
    ] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    json_out: JsonOpt = False,
) -> None:
    """加一条信息。"""
    from pacxio import inventory

    fields = {
        "id": id_, "category": category, "text": text.replace("\\n", "\n") if text else None,
        "asset": asset, "required": not optional, "pending": pending, "note": note,
    }  # fmt: skip
    _emit(inventory.content_add(directory, fields), json_out)


@content_app.command("edit", help=t("Change an item."))
def content_edit(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    item_id: Annotated[str, typer.Argument(help=t("Item id"))],
    category: CategoryOpt = None,
    text: Annotated[
        str | None, typer.Option("--text", help=t("New text; use \\n for a line break"))
    ] = None,
    asset: Annotated[str | None, typer.Option("--asset")] = None,
    required: Annotated[
        bool | None, typer.Option("--required/--optional", help=t("Required / optional"))
    ] = None,
    pending: Annotated[str | None, typer.Option("--pending")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    clear: Annotated[
        str | None,
        typer.Option("--clear", help=t("Clear fields, comma-separated: pending,asset,note,text")),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """改一条信息。"""
    from pacxio import inventory

    fields = {
        "category": category, "text": text.replace("\\n", "\n") if text else None, "asset": asset,
        "required": required, "pending": pending, "note": note,
    }  # fmt: skip
    cleared = [c.strip() for c in (clear or "").split(",") if c.strip()]
    bad = [c for c in cleared if c not in {"pending", "asset", "note", "text"}]
    if bad:
        raise typer.BadParameter(
            t("--clear can only be pending / asset / note / text, got {bad}", bad=bad)
        )
    _emit(inventory.content_edit(directory, item_id, fields, cleared), json_out)


@content_app.command("remove", help=t("Delete an item (refused while a placement uses it)."))
def content_remove(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    item_id: Annotated[str, typer.Argument(help=t("Item id"))],
    json_out: JsonOpt = False,
) -> None:
    """删一条信息（被放置引用时拒绝）。"""
    from pacxio import inventory

    _emit(inventory.content_remove(directory, item_id), json_out)


@content_app.command(
    "conventions", help=t("Choose which category's reference table this job uses.")
)
def content_conventions(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    conv_id: Annotated[str, typer.Argument(help=t("Reference table id, e.g. paint"))],
    json_out: JsonOpt = False,
) -> None:
    """指定这个 job 用哪个品类的常规参考表。"""
    from pacxio import inventory

    _emit(inventory.set_conventions(directory, conv_id), json_out)


@content_app.command(
    "list",
    help=t(
        "List the content by category: text, required / optional, pending, usual panels, placements using it, confirmation status."
    ),
)
def content_list(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """按类别列出清单：原文、必须 / 可选、待定、常规位置、已放到哪些放置、确认状态。"""
    from pacxio import inventory

    r = inventory.content_list(directory)
    if not json_out and r.ok:
        d: dict[str, Any] = r.data
        status = {
            "none": t("not confirmed"),
            "confirmed": t("confirmed"),
            "stale": t("confirmation expired"),
        }[d["confirmation"]["status"]]
        conv = (
            t("Reference: {name}", name=d["conventions_name"])
            if d.get("conventions_name")
            else t("No reference table chosen")
        )
        typer.echo(
            t(
                "Content list: {n} items ({conv}; wording {status})",
                n=len(d["items"]),
                conv=conv,
                status=status,
            )
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in d["items"]:
            groups.setdefault(row["label"], []).append(row)
        for label, rows in groups.items():
            typer.echo(f"  {label}")
            for row in rows:
                flags = [] if row.get("required", True) else [t("optional")]
                if row.get("pending"):
                    flags.append(t("pending: {what}", what=row["pending"]))
                body = (row.get("text") or "").replace("\n", " / ")
                if row.get("asset"):
                    body = (body + " " if body else "") + t("[graphic {asset}]", asset=row["asset"])
                extra = t("  ({flags})", flags="; ".join(flags)) if flags else ""
                where = (
                    t("  usual: {faces}", faces=", ".join(row["usual_faces"]))
                    if row.get("usual_faces")
                    else ""
                )
                used = (
                    t("  placed: {by}", by=", ".join(row["placed_by"])) if row["placed_by"] else ""
                )
                typer.echo(f"    {row['id']:<16} {body}{extra}{where}{used}")
    _emit(r, json_out)


@content_app.command(
    "confirm",
    help=t(
        'Record the user\'s confirmation of the wording (run only after the user explicitly says "confirm").'
    ),
)
def content_confirm(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    by: Annotated[str | None, typer.Option("--by", help=t("Who confirmed"))] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    status: Annotated[
        bool, typer.Option("--status", help=t("Only show the confirmation status"))
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """记录用户对措辞的确认（用户明确说"确认"之后才运行）。"""
    from pacxio import inventory

    _emit(inventory.content_confirm(directory, by, note, status), json_out)


@conventions_app.command("list", help=t("List the available category reference tables."))
def conventions_list(json_out: JsonOpt = False) -> None:
    """列出可用的品类常规参考表。"""
    from pacxio import inventory

    r = inventory.conventions_list()
    if not json_out:
        for c in r.data["conventions"]:
            flag = "" if c["verified"] else t(" (initial values, not confirmed)")
            typer.echo(
                t(
                    "  {id:<10} {name}: {description}{flag}",
                    id=c["id"],
                    name=c["name_zh"],
                    description=c["description"],
                    flag=flag,
                )
            )
    _emit(r, json_out)


@conventions_app.command(
    "show",
    help=t(
        "Show a category's reference: which panels each kind of information usually goes on, common layouts, regulatory reminders."
    ),
)
def conventions_show(
    conv_id: Annotated[str, typer.Argument(help=t("e.g. paint"))],
    json_out: JsonOpt = False,
) -> None:
    """查看一个品类的常规参考：各类信息通常放哪些面、常见版式、法规提醒。"""
    from pacxio import inventory

    r = inventory.conventions_show(conv_id)
    if not json_out and r.ok:
        d: dict[str, Any] = r.data
        typer.echo(t("{name}: {description}", name=d["name_zh"], description=d["description"]))
        for label, faces in d["faces_zh"].items():
            typer.echo(f"  {label:<8} {', '.join(faces)}")
        typer.echo(t("Common layouts:"))
        for lay in d["layouts"]:
            typer.echo(
                t("  {name} ({common_for})", name=lay["name_zh"], common_for=lay["common_for"])
            )
            typer.echo(
                t(
                    "    front: {front}; back: {back}; sides: {sides}",
                    front=lay["front"],
                    back=lay["back"],
                    sides=lay["sides"],
                )
            )
        typer.echo(t("Reminders:"))
        for rem in d["reminders"]:
            typer.echo(f"  - {rem}")
    _emit(r, json_out)


# ---------------- 文字样式（层级）----------------

style_app = typer.Typer(
    help=t("Text styles (levels): title, subtitle, body, small print …"), no_args_is_help=True
)
app.add_typer(style_app, name="style")


@style_app.command(
    "set", help=t("Create or change a text style; text that uses it changes with it.")
)
def style_set(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    name: Annotated[str, typer.Argument(help=t("Style name, e.g. title / heading / body / small"))],
    font: Annotated[
        str | None, typer.Option("--font", help=t("Font (a font chain is allowed)"))
    ] = None,
    size_pt: Annotated[float | None, typer.Option("--size-pt")] = None,
    label: Annotated[
        str | None, typer.Option("--label", help=t("Level name for people, e.g. Title / Body"))
    ] = None,
    line_height: Annotated[
        float | None,
        typer.Option("--line-height", help=t("Line spacing, multiple of the font size")),
    ] = None,
    tracking: Annotated[
        float | None, typer.Option("--tracking", help=t("Tracking, 1/1000 em"))
    ] = None,
    color: Annotated[str | None, typer.Option("--color", help=t("CMYK percent c,m,y,k"))] = None,
    hanging_indent: Annotated[
        str | None, typer.Option("--hanging-indent", help=HANGING_HELP)
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """新建或修改一个文字样式；用它的文字一起变。"""
    from pacxio import textstyles

    c = _floats(color, 4, "--color")
    fields = {"font": font, "size_pt": size_pt, "label": label, "line_height": line_height,
              "tracking": tracking, "color": {"cmyk": c} if c else None,
              "hanging_indent": _hanging(hanging_indent)}  # fmt: skip
    _emit(textstyles.style_set(directory, name, fields), json_out)


@style_app.command("list", help=t("List text styles and the placements that use them."))
def style_list(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """列出文字样式与使用它们的放置。"""
    from pacxio import textstyles

    r = textstyles.style_list(directory)
    if not json_out and r.ok:
        for st in r.data["styles"]:
            lab = t(" ({label})", label=st["label"]) if st.get("label") else ""
            typer.echo(
                t(
                    "  {name:<10}{lab} {font} {size:g}pt  used by: {used}",
                    name=st["name"],
                    lab=lab,
                    font=st["font"],
                    size=st["size_pt"],
                    used=", ".join(st["used_by"]) or "—",
                )
            )
    _emit(r, json_out)


@style_app.command("remove", help=t("Delete a style nothing uses."))
def style_remove(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    name: Annotated[str, typer.Argument(help=t("Style name"))],
    json_out: JsonOpt = False,
) -> None:
    """删除一个没被使用的样式。"""
    from pacxio import textstyles

    _emit(textstyles.style_remove(directory, name), json_out)


@app.command(
    "plan",
    help=t(
        "Copy layout draft: text per panel (plan/plan.md) + layout sketches (plan/wireframe.png, faces/, pairs/)."
    ),
)
def plan(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help=t(
                "Record that the user confirmed this layout draft (only after the user explicitly confirms)"
            ),
        ),
    ] = False,
    by: Annotated[str | None, typer.Option("--by", help=t("Who confirmed"))] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    json_out: JsonOpt = False,
) -> None:
    """文案排版初稿：按面文字稿（plan/plan.md）+ 版式示意图（plan/wireframe.png、faces/、pairs/）。"""
    from pacxio_pro.plan import make_plan

    r = make_plan(directory, confirm, by, note)
    if not json_out and r.ok:
        d: dict[str, Any] = r.data
        status = {
            "none": t("not confirmed"),
            "confirmed": t("confirmed"),
            "stale": t("changed after confirmation"),
        }[d["confirmation"]["status"]]
        typer.echo(t("Layout draft ({status})", status=status))
        for face, rows in d["faces"].items():
            texts = sum(1 for x in rows if x["kind"] == "text")
            boxes = len(rows) - texts
            typer.echo(
                t(
                    "  {face:<8} {texts} text blocks, {boxes} images / graphics / placeholders",
                    face=face,
                    texts=texts,
                    boxes=boxes,
                )
            )
    _emit(r, json_out)


@app.command(
    "flat",
    help=t(
        "Compare panel by panel with the flat (for agents, flat/vs_flat_<panel>.png); --diff records differences, shown next to the dieline confirmation image on the workbench."
    ),
)
def flat(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    diff: Annotated[
        list[str] | None,
        typer.Option(
            "--diff",
            help=t(
                "One difference between the final artwork and the flat / concept (repeatable); default: keep the last ones"
            ),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """和展开平面图逐面对照（agent 用，flat/vs_flat_<面>.png）；--diff 写差别，显示在工作台刀版确认图旁边。"""
    from pacxio.mockup.flat import make_flat

    _emit(make_flat(directory, diff), json_out)


@restore_app.command(
    "score",
    help=t(
        "Fidelity score: compare the final artwork with the flat panel by panel (position, color difference, SSIM, edges, fine lines); writes flat/score.json."
    ),
)
def restore_score(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    json_out: JsonOpt = False,
) -> None:
    """还原度评分：正式稿和展开平面图逐面比（位置、色差、SSIM、边缘、细线），写 flat/score.json。"""
    from pacxio_pro.restore.score import score_job

    _emit(score_job(directory), json_out)


@restore_app.command(
    "photo",
    help=t(
        "Redraw a photo following the flat: cut the reference and write the prompt; bring the result back with --image. Use the user's own photo when there is one."
    ),
)
def restore_photo(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    placement: Annotated[str, typer.Argument(help=t("Image placement id"))],
    image: Annotated[
        Path | None,
        typer.Option(
            "--image",
            help=t(
                "The redrawn image: import, prepare and swap the placement's asset; without it, only cut the reference and write the prompt (no image generated)"
            ),
        ),
    ] = None,
    note: Annotated[
        str | None, typer.Option("--note", help=t("Add a sentence to the prompt (English)"))
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """照片按平面图重画：切参考图、写提示词；出好后用 --image 导回来。有用户实拍就用实拍。"""
    from pacxio_pro.restore.photo import attach_image, write_prompt

    _emit(
        attach_image(directory, placement, image)
        if image
        else write_prompt(directory, placement, note),
        json_out,
    )


@restore_app.command(
    "graphics",
    help=t(
        "Measure icon and logo positions and sizes from the flat; finishes that follow them move too. Run this before restore text."
    ),
)
def restore_graphics(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=t(
                "Write into the job (backed up to history/ first); without it only flat/graphic_fit.json is written"
            ),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """从展开平面图量图标、Logo 的位置和大小，照它做的工艺跟着挪。先跑这个，再跑 restore text。"""
    from pacxio_pro.restore.graphics import graphics_fit

    _emit(graphics_fit(directory, apply), json_out)


@restore_app.command(
    "frames",
    help=t(
        "Measure the visible frame of rectangular photos first, then cut the full reference with restore photo; edges are not guessed from the photo's texture."
    ),
)
def restore_frames(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=t(
                "Update photos, frames and finishes together; roll back if new preflight problems appear"
            ),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """先量矩形照片的可见边框，再 restore photo 切完整参考；不按照片纹理猜边界。"""
    from pacxio_pro.restore.frames import frames_fit

    _emit(frames_fit(directory, apply), json_out)


@restore_app.command(
    "colors",
    help=t(
        "Match text / one-color icon colors under the current ICC and total ink, and report reference colors that still cannot be reached after sampling."
    ),
)
def restore_colors(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=t(
                "Write colors for reliable matches of text / one-color icons; key colors and shared styles are kept"
            ),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """按当前 ICC 和总墨量匹配文字/单色图标颜色，报告采样后仍无法接近的参考色。"""
    from pacxio_pro.restore.colors import colors_fit

    _emit(colors_fit(directory, apply), json_out)


@restore_app.command(
    "rows",
    help=t(
        "Place each row by the reliably matched set of dividers, then match icons and text inside the row."
    ),
)
def restore_rows(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=t(
                "Align repeated selling-point rows together; roll everything back if new preflight problems appear"
            ),
        ),
    ] = False,
    fonts: Annotated[
        bool,
        typer.Option(
            "--fonts",
            help=t("Allow matching fonts already in this job; shared styles are not changed"),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """按可靠匹配的整组分隔线定位每行，再在行内匹配图标和文字。"""
    from pacxio_pro.restore.rows import rows_fit

    _emit(rows_fit(directory, apply, fonts), json_out)


@restore_app.command(
    "text",
    help=t(
        "Measure font size, line spacing and position from the flat; --tracking measures single-line tracking. Fonts are only suggested by default; colors are not changed."
    ),
)
def restore_text(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=t(
                "Write into the job (backed up to history/ first); without it only flat/text_fit.json is written"
            ),
        ),
    ] = False,
    fonts: Annotated[
        bool,
        typer.Option(
            "--fonts",
            help=t("Also change fonts (default: only suggest); give it only after the user agrees"),
        ),
    ] = False,
    tracking: Annotated[
        bool,
        typer.Option(
            "--tracking",
            help=t(
                "Also measure tracking of single-line text; multi-line paragraphs keep their tracking"
            ),
        ),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """从平面图量字号、行距、位置；--tracking 量单行字距。字体默认只报建议，颜色不改。"""
    from pacxio_pro.restore.text import text_fit

    _emit(text_fit(directory, apply, fonts, tracking), json_out)


@restore_app.command(
    "face",
    help=t(
        "One panel (top, bottom, each mailer panel): straighten this panel of the flat at its real size and remove text, ready for visual trace."
    ),
)
def restore_face(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    face: Annotated[
        str,
        typer.Argument(
            help=t(
                "Panel id: top_lid, btm_lid … (top / bottom work as short forms, mapped to the style's panels)"
            )
        ),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help=t("Output name (assets/<name>.png, default <panel>-face)")),
    ] = None,
    px_per_mm: Annotated[
        float | None,
        typer.Option(
            "--px-per-mm",
            help=t("Pixels per millimetre (default: the flat's own resolution, 6–16)"),
        ),
    ] = None,
    erase: Annotated[
        list[str] | None,
        typer.Option(
            "--erase",
            help=t(
                "Extra boxes to fill x,y,w,h (panel-local mm from the bottom left with the panel upright, repeatable): AI text that was not fully removed"
            ),
        ),
    ] = None,
    crop: Annotated[
        str | None,
        typer.Option(
            "--crop",
            help=t(
                "Only take this part of the panel x,y,w,h (panel-local mm): trace an illustration on a gradient background on its own (then visual trace --background gradient)"
            ),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """单个面（顶、底、飞机盒各面）：展开平面图上这个面按真实尺寸拉正、去文字，给 visual trace 描。"""
    from pacxio_pro.restore.face import face_asset

    boxes = []
    for text in erase or []:
        x, y, w, h = _floats(text, 4, "--erase") or (0.0, 0.0, 0.0, 0.0)
        boxes.append((x, y, w, h))
    c = _floats(crop, 4, "--crop") if crop else None
    region = (c[0], c[1], c[2], c[3]) if c else None
    _emit(face_asset(directory, face, name, px_per_mm, boxes, region), json_out)


@restore_app.command(
    "strip",
    help=t(
        "Build the body wrap: join the flat's body panels at real size into one strip (text removed, folds removed at seams, small steps aligned), ready for visual trace."
    ),
)
def restore_strip(
    directory: Annotated[Path, typer.Argument(help=t("Job folder"))],
    name: Annotated[
        str, typer.Option("--name", help=t("Output name (assets/<name>.png)"))
    ] = "body-strip",
    px_per_mm: Annotated[
        float | None,
        typer.Option(
            "--px-per-mm",
            help=t("Pixels per millimetre; default: the flat's own resolution (6–16)"),
        ),
    ] = None,
    erase: Annotated[
        list[str] | None,
        typer.Option(
            "--erase",
            help=t(
                "Extra boxes to fill x,y,w,h (body coordinates mm, repeatable): AI text that was not fully removed"
            ),
        ),
    ] = None,
    keep: Annotated[
        list[str] | None,
        typer.Option(
            "--keep",
            help=t(
                "When the seam is far off, keep this panel fixed (repeatable) and bend the other one to meet it; right / left work as short forms"
            ),
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """拼盒身一圈：展开平面图的盒身各面按真实尺寸接成一条（去文字、接缝去折线、小台阶对齐），给 visual trace 描。"""
    from pacxio.concept.service import FACE_ALIASES
    from pacxio_pro.restore.strip import strip_asset

    boxes = []
    for text in erase or []:
        x, y, w, h = _floats(text, 4, "--erase") or (0.0, 0.0, 0.0, 0.0)
        boxes.append((x, y, w, h))
    faces = [FACE_ALIASES.get(f.strip(), f.strip()) for f in keep or []]
    _emit(strip_asset(directory, name, px_per_mm, boxes, faces), json_out)
