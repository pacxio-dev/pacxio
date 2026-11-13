"""`pacxio next`：现在到哪一步、等谁、下一步做什么（ 2.3）。

阶段判断和工作台是同一个来源（`state.job_state`），两边说的"现在"永远一致；这里只把每个阶段的状态
换成 agent 要做的事、要跑的命令和要读的 reference。等用户的阶段只说给用户看什么、用户确认后跑什么，
不替用户确认（Skill 铁律 4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.studio.state import Stage, job_state


@dataclass(frozen=True)
class Step:
    what: str
    commands: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)


def _todo_step(st: Stage, j: str) -> Step:
    """阶段自己有待办（工作台的"现在"）时，具体怎么做。"""
    key = (st.id, st.status)
    if st.id == "content":
        return Step(t('Review the exact wording of each content item with the user, then record their explicit confirmation'),
                    [f"pacxio content list {j}", f"pacxio content confirm {j} --by {t('<approver>')}"],
                    ["references/content.md"])  # fmt: skip
    if st.id == "plan":
        return Step(t('Show the layout draft to the user, including the copy and layout for each panel. Record their confirmation'),
                    [f"pacxio plan {j} --confirm --by {t('<approver>')}"], ["references/layout-draft.md"])  # fmt: skip
    if st.id == "concept":
        return Step(t('Show the main-view concepts (Workbench stage ③). Record the direction the user explicitly chooses'),
                    [f"pacxio concept choose {j} <id> --region x0,y0,x1,y1 --by {t('<decision-maker>')}"],
                    ["references/concept.md"])  # fmt: skip
    if key == ("flat_concept", "redo"):
        return Step(t('Regenerate flats with incorrect proportions. Import alternatives together if needed and keep the most accurate'),
                    [f"pacxio concept prompt {j} --step flat",
                     f"pacxio concept import {j} {t('<image1> [<image2>…]')} --name {t('<name>')} --view flat --faces {t('<panel>')}"],
                    ["references/concept.md"])  # fmt: skip
    if st.id == "flat_concept":
        return Step(t("Compare the flats with both 3D concept views and list any differences. Record the user's confirmation in chat or the Workbench, then build the final design from the flats"),
                    [f"pacxio concept confirm {j} --by {t('<approver>')}"], ["references/concept.md"])  # fmt: skip
    if key == ("design", "none"):
        return Step(t('Build the final design from the confirmed flats, including copy, reconstructed artwork, photos, and finishes. Then render the dieline proof'),
                    [f"pacxio panels {j}", f"pacxio render {j}"], ["references/design.md"])  # fmt: skip
    if key == ("design", "stale"):
        return Step(
            t("Render the dieline proof again"), [f"pacxio render {j}"], ["references/design.md"]
        )
    if key == ("design", "blocked"):
        return Step(t('Ask the user for the missing required content, add it, then render the dieline proof again'),
                    [f"pacxio content edit {j} {t('<item>')} --text …", f"pacxio render {j}"],
                    ["references/content.md"])  # fmt: skip
    if key == ("design", "fixing"):
        return Step(t('Apply the fixes from preflight. Show the user any comparisons that require review, such as F02. Render and run preflight again, then ask the user to approve the proof once there are zero errors'),
                    [f"pacxio preflight {j} --json", f"pacxio render {j}"],
                    ["references/troubleshooting.md", "references/prepress-rules.md"])  # fmt: skip
    if st.id == "design":
        return Step(t("Show the dieline proof (Workbench stage ⑤) and review its copy, artwork, dimensions, and finishes. Record the user's explicit approval"),
                    [f"pacxio approve {j} --by {t('<approver>')} --note {t('<note>')}"], ["references/mockup.md"])  # fmt: skip
    if st.id == "mockup":
        return Step(t("Regenerate the 3D mockup"), [f"pacxio mockup {j}"], ["references/mockup.md"])
    if key == ("delivery", "errors"):
        return Step(t('Apply each suggested preflight fix and rerun the checks until there are zero errors'),
                    [f"pacxio preflight {j} --json"],
                    ["references/troubleshooting.md", "references/prepress-rules.md"])  # fmt: skip
    if key == ("delivery", "passed"):
        return Step(t('Export the delivery pack and explain any warnings'),
                    [f"pacxio export {j} --proof --require-approval"], ["references/delivery.md"])  # fmt: skip
    if st.id == "delivery" and st.action_code == "reexport":  # 导出的是旧版
        return Step(t('Export the delivery pack again once the dieline proof is approved'),
                    [f"pacxio approve {j} --status", f"pacxio export {j} --proof --require-approval"],
                    ["references/delivery.md"])  # fmt: skip
    if st.id == "delivery":
        return Step(
            t("Rerun preflight"), [f"pacxio preflight {j} --json"], ["references/prepress-rules.md"]
        )
    return Step(st.action or "")


def _flow_step(stages: dict[str, Stage], j: str) -> tuple[Stage | None, Step]:
    """没有哪个阶段在等谁时，按流程顺序找下一件要 agent 做的事。"""
    content, concept, flat = stages["content"], stages["concept"], stages["flat_concept"]
    design, mockup, delivery = stages["design"], stages["mockup"], stages["delivery"]
    if content.status == "none":
        return content, Step(t('Build the content list from the exact copy to be printed, then review the wording with the user'),
                             [f"pacxio content import {j} brief.yaml"], ["references/content.md"])  # fmt: skip
    if concept.status == "none":
        return concept, Step(t('Generate one main-view concept for each of two directions. Prepare a layout draft first if there is substantial copy (route A)'),
                             [f"pacxio concept prompt {j} --step main --name {t('<id>')} --design \"…\" --text …"],
                             ["references/concept.md", "references/layout-draft.md"])  # fmt: skip
    if flat.status == "none" and design.status == "none":  # 多部件盒型也一样（天地盖测试 F-15）
        from pacxio.concept.service import back_view_for, read_chosen

        chosen = read_chosen(Path(j))
        back = back_view_for(Path(j), chosen.id if chosen else None)
        if back is not None:  # 背面视角已经出了：不要再让出一张（STE 测试 S-02）
            return flat, Step(t('Generate the flats (the back view {id} is ready)', id=back.id),
                              [f"pacxio plan {j}", f"pacxio concept prompt {j} --step flat"],
                              ["references/concept.md"])  # fmt: skip
        return flat, Step(t('Generate the back view first, then generate the flats'),
                          [f"pacxio plan {j}", f"pacxio concept prompt {j} --step back",
                           f"pacxio concept prompt {j} --step flat"], ["references/concept.md"])  # fmt: skip
    if design.status == "none":
        return design, _todo_step(design, j)
    if design.status == "approved" and mockup.status == "none":
        return mockup, Step(t('Generate the 3D mockup for viewing'), [f"pacxio mockup {j}"],
                            ["references/mockup.md"])  # fmt: skip
    if design.status == "approved" and delivery.status == "none":
        return delivery, Step(t('Run preflight and apply the suggested fixes for any errors'), [f"pacxio preflight {j} --json"],
                              ["references/prepress-rules.md"])  # fmt: skip
    if delivery.status == "exported":
        return delivery, Step(t('The delivery pack is ready. Explain any warnings and ask the user to confirm a physical proof before mass production'), [],
                              ["references/delivery.md"])  # fmt: skip
    return None, Step(t("Nothing to do"))


def next_step(directory: Path) -> Result:
    r = Result(command="next", job=str(directory))
    state = job_state(directory)
    if state.errors and not state.stages:
        r.errors += state.errors
        r.ok, r.exit_code = False, 2
        return r
    stages = {st.id: st for st in state.stages}
    # ⑤ 还没出图时工作台也写着"等 agent 拼版"：那是流程上的位置，不是待办，按流程顺序判断
    current = next((st for st in state.stages if st.todo and (st.id, st.status) != ("design", "none")),
                   None)  # fmt: skip
    j = str(directory)
    if current is not None:
        step, who = _todo_step(current, j), current.todo
    else:
        current, step = _flow_step(stages, j)
        who = state.now_who
    open_notes = sum(1 for n in state.notes if n.get("status") == "open")
    if open_notes:  # 用户在工作台上写的意见：每一轮动手前先看
        step = Step(step.what, [f"pacxio notes list {j} --open", *step.commands],
                    ["references/studio.md", *step.read])  # fmt: skip
        r.warnings.append(Issue(id="NOTE", message=t("There are {n} open notes in the Workbench", n=open_notes),
                                fix=t("Run `pacxio notes list {job} --open`, then reply with `notes reply`", job=j)))  # fmt: skip
    data: dict[str, Any] = {
        "now": state.now,
        "who": who,
        "stage": None if current is None else
        {"id": current.id, "no": current.no, "title": current.title,
         "status": current.status, "label": current.label},
        "step": step.what,
        "commands": step.commands,
        "read": step.read,
        "stages": [{"id": s.id, "no": s.no, "title": s.title, "status": s.status, "label": s.label,
                    "todo": s.todo} for s in state.stages],
        "open_notes": open_notes,
    }  # fmt: skip
    r.data = data
    r.warnings += state.errors
    return r


__all__ = ["next_step"]
