"""官网托管视觉风格包读取埋点：远端/本地 catalog.json、向前兼容、缓存降级、软融合与重建建议。"""

from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.cloud import styles as cloud_styles
from pacxio.concept import service, styles
from pacxio.messages import use_language
from pacxio.mockup.approval import design_fingerprint
from pacxio.model.job import Job
from pacxio.visual.providers import GenRequest, GenResult, ImageProvider

runner = CliRunner()
CJK = re.compile(r"[㐀-鿿＀-￯　-〿]")

SAMPLE_CATALOG: dict[str, Any] = {
    "schema_version": 1,
    "version": "2026.12.01",
    "default_pair": ["editorial_minimal", "modern_flat"],
    # 模拟官网未来新增字段：老版本客户端必须平滑忽略
    "website_banner": "Winter 2026 Edition",
    "groups": [
        {
            "id": "clean_elevated",
            "name": "Clean & Elevated",
            "summary": "Quiet confidence built on restraint.",
            "i18n": {"zh": {"name": "克制高级", "summary": "以留白与秩序营造高级感。"}},
        },
        {
            "id": "graphic_bold",
            "name": "Graphic & Bold",
            "summary": "High shelf punch.",
            "i18n": {"zh": {"name": "图形撞色", "summary": "强货架识别。"}},
        },
    ],
    "categories": [
        {
            "id": "skincare",
            "name": "Skincare & Beauty",
            "default_pair": ["editorial_minimal", "clinical_lab"],
            "recommended": ["editorial_minimal", "clinical_lab", "noir_foil"],
            "keywords": ["skincare", "serum", "cream"],
            "shelf_focus": "Calm authority and clear volume hierarchy.",
            "prompt_note": "Keep skincare hierarchy calm and refined without fake clinical seals.",
            "i18n": {
                "zh": {
                    "name": "护肤与彩妆",
                    "shelf_focus": "克制可信、容量层级清晰。",
                    "keywords": ["护肤", "精华", "面霜"],
                }
            },
        },
        {
            "id": "fragrance",
            "name": "Fragrance & Candles",
            "default_pair": ["noir_foil", "botanical_ink"],
            "recommended": ["noir_foil", "botanical_ink", "editorial_minimal"],
            "keywords": ["fragrance", "perfume", "candle"],
            "shelf_focus": "Atmospheric mood and tactile material restraint.",
            "prompt_note": "Give the scent name generous quiet breathing room.",
            "i18n": {"zh": {"name": "香氛与蜡烛", "keywords": ["香水", "香氛", "蜡烛"]}},
        },
    ],
    "styles": [
        {
            "id": "editorial_minimal",
            "name": "Editorial Minimal",
            "tagline": "Calm negative space and architectural restraint.",
            "group": "clean_elevated",
            "best_for": ["skincare", "fragrance"],
            "pairs_well_with": ["clinical_lab", "modern_flat"],
            "aesthetic_summary": "Gallery-like calm built on proportion.",
            "palette_philosophy": "Quiet neutral ground with one deep ink.",
            "typography_mood": "Refined serif headline with wide-tracked sans details.",
            "visual_language": "Minimal hairlines and generous breathing room.",
            "avoid": "Busy gradients and cluttered badges.",
            "specimen_url": "/styles/specimens/editorial_minimal.webp",  # 未来官网扩展字段
            "featured": True,
            "printability": {
                "restore_mode": "vector_native",
                "vector_friendly": True,
                "notes": "Rebuilds cleanly with engine text and minimal vector shapes.",
                "font_pairing_hint": "SourceHanSerifSC-Bold + SourceHanSansSC-Regular",
                "recommended_papers": ["textured_300", "sbs_350"],
                "recommended_finishes": ["emboss", "matte_lamination"],
            },
            "prompt_direction": "Editorial minimalist aesthetic with generous negative space and crisp flat-ink edges.",
            "prompt_avoid": "Avoid visual clutter or busy gradients.",
            "prompt_scene": "Minimalist studio photography on a matte architectural surface.",
            "i18n": {
                "zh": {
                    "name": "极简画册",
                    "tagline": "高留白与建筑感克制秩序。",
                    "printability": {"notes": "可用引擎排字与极简矢量高保真重建。"},
                }
            },
        },
        {
            "id": "clinical_lab",
            "name": "Clinical Lab",
            "tagline": "Rational precision and structured clarity.",
            "group": "clean_elevated",
            "best_for": ["skincare"],
            "pairs_well_with": ["editorial_minimal"],
            "printability": {
                "restore_mode": "vector_native",
                "vector_friendly": True,
                "notes": "Razor-sharp grids and clean sans typography.",
                "font_pairing_hint": "SourceHanSansSC-Bold + SourceHanSansSC-Regular",
                "recommended_papers": ["silver_card_350", "sbs_350"],
                "recommended_finishes": ["matte_lamination", "spot_uv"],
            },
            "prompt_direction": "Modern clinical laboratory aesthetic with clean hairline rules and rational hierarchy.",
            "prompt_avoid": "Avoid sci-fi glows or fake medical crosses.",
            "prompt_scene": "Crisp high-key laboratory studio photography on pale brushed metal.",
            "i18n": {"zh": {"name": "院线科技"}},
        },
        {
            "id": "noir_foil",
            "name": "Noir & Foil",
            "tagline": "Deep monolithic stock with restrained metallic foil.",
            "group": "clean_elevated",
            "best_for": ["fragrance", "skincare"],
            "pairs_well_with": ["editorial_minimal", "botanical_ink"],
            "printability": {
                "restore_mode": "vector_native",
                "vector_friendly": True,
                "notes": "Built for dyed black or pearl stocks with foil plates.",
                "font_pairing_hint": "SourceHanSerifSC-Bold + SourceHanSansSC-Regular",
                "recommended_papers": ["black_card_350", "pearl_card_300"],
                "recommended_finishes": ["foil_gold", "emboss"],
            },
            "prompt_direction": "Refined nocturnal luxury craft with abundant matte space and understated foil emblem.",
            "prompt_avoid": "Avoid ornate clutter or neon glows.",
            "prompt_scene": "Sculpted studio photography on a dark slate plinth.",
            "i18n": {"zh": {"name": "黑金奢护"}},
        },
        {
            "id": "modern_flat",
            "name": "Modern Flat",
            "tagline": "Clean flat color blocks and contemporary shelf clarity.",
            "group": "graphic_bold",
            "best_for": [],
            "pairs_well_with": ["editorial_minimal"],
            "printability": {
                "restore_mode": "vector_native",
                "vector_friendly": True,
                "recommended_papers": ["sbs_350"],
                "recommended_finishes": ["matte_lamination"],
            },
            "prompt_direction": "Contemporary flat-vector graphic aesthetic with crisp color blocking.",
            "prompt_avoid": "Avoid airbrushed 3D shading.",
            "prompt_scene": "Bright matte studio tabletop photography.",
            "i18n": {"zh": {"name": "现代平涂"}},
        },
        {
            "id": "botanical_ink",
            "name": "Botanical Ink",
            "tagline": "Organic natural illustration on warm tactile stock.",
            "group": "clean_elevated",
            "best_for": ["fragrance"],
            "pairs_well_with": ["noir_foil"],
            "printability": {
                "restore_mode": "illustrated_bitmap",
                "vector_friendly": False,
                "recommended_papers": ["textured_300", "kraft_350"],
                "recommended_finishes": ["deboss"],
            },
            "prompt_direction": "Natural botanical artisan aesthetic with delicate ink linework.",
            "prompt_avoid": "Avoid glossy plastic reflections.",
            "prompt_scene": "Warm daylight photography on natural linen.",
            "i18n": {"zh": {"name": "植物线描"}},
        },
    ],
}


def cli(*args: str) -> tuple[int, dict[str, Any]]:
    res = runner.invoke(app, [*args, "--json"])
    try:
        return res.exit_code, json.loads(res.output)
    except ValueError as e:
        raise AssertionError(res.output) from e


class _FakeProvider(ImageProvider):
    name = "fake"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, req: GenRequest) -> GenResult:
        self.prompts.append(req.prompt)
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (220, 210, 195)).save(buf, format="PNG")
        return GenResult(image=buf.getvalue(), model="fake-v1", revised_prompt=None)


@pytest.fixture
def local_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """指向临时本地 catalog.json（模拟官网持续发布的新风格目录，无需改动引擎代码）。"""
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(SAMPLE_CATALOG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "pacxio_home"))
    monkeypatch.setenv(cloud_styles.STYLES_URL_ENV, str(path))
    return path


def make_job(root: Path, material: str = "sbs_350") -> Path:
    job_dir = root / f"job_{material}"
    code, out = cli(
        "job", "init", str(job_dir), "--style", "rte", "--L", "80", "--W", "50", "--H", "140",
        "--material", material,
    )
    assert code == 0, out
    return job_dir


def test_catalog_reads_external_json_forward_compat_and_i18n(local_catalog: Path) -> None:
    with use_language("en"):
        code_en, out_en = cli("concept", "styles", "--full")
        assert code_en == 0 and out_en["ok"]
        assert not CJK.search(json.dumps(out_en, ensure_ascii=False))
        assert len(out_en["data"]["styles"]) == 5
        assert out_en["data"]["version"] == "2026.12.01"

    with use_language("zh"):
        code_zh, out_zh = cli("concept", "styles", "--full")
        assert code_zh == 0 and out_zh["ok"]
        zh_names = {s["id"]: s["name"] for s in out_zh["data"]["styles"]}
        assert zh_names["editorial_minimal"] == "极简画册"
        assert zh_names["clinical_lab"] == "院线科技"
        assert zh_names["noir_foil"] == "黑金奢护"

    # 官网新增一个引擎从未见过的风格：无需更新引擎或翻译表，立即可读可用
    updated = json.loads(local_catalog.read_text(encoding="utf-8"))
    updated["styles"].append(
        {
            "id": "k_beauty_glass",
            "name": "Glass Glow",
            "tagline": "Translucent pastel freshness.",
            "group": "clean_elevated",
            "prompt_direction": "Luminous translucent pastel minimalism.",
            "brand_new_future_field": {"v": 99},
            "i18n": {"zh": {"name": "清透水光"}},
        }
    )
    local_catalog.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
    with use_language("zh"):
        code_new, out_new = cli("concept", "styles", "k_beauty_glass")
    assert code_new == 0 and out_new["data"]["style"]["name"] == "清透水光"


def test_category_matching_and_paper_aware_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_catalog: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    cat, _, _ = styles.load_catalog()
    assert styles.resolve_category("skincare", cat) is not None
    assert styles.resolve_category("高端护肤精华", cat) == cat.categories["skincare"]
    assert styles.resolve_category("香氛蜡烛", cat) == cat.categories["fragrance"]

    black_job = make_job(tmp_path, "black_card_350")
    code, out = cli("concept", "styles", "--category", "skincare", "--job", str(black_job))
    assert code == 0, out
    assert out["data"]["styles"][0]["id"] == "noir_foil"
    assert out["data"]["recommended_pair"][0] == "noir_foil"
    assert out["data"]["recommended_pair"][0] != out["data"]["recommended_pair"][1]

    assert cli("concept", "styles", "missing_style")[0] == 2
    assert cli("concept", "styles", "--category", "spaceships")[0] == 2


def test_main_prompt_soft_composition_and_offline_plain_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_catalog: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    job_dir = make_job(tmp_path, "sbs_350")
    fp_before = design_fingerprint(job_dir, Job.load(job_dir))

    with use_language("en"):
        code, out = cli(
            "concept", "prompt", str(job_dir), "--step", "main",
            "--style", "editorial_minimal", "--category", "skincare",
            "--name", "main-A", "--text", "VELA", "--text", "Barrier Serum",
        )
    assert code == 0, out
    row = out["data"]["prompts"][0]
    prompt_a = Path(row["file"]).read_text(encoding="utf-8")
    assert "Editorial Minimal" in prompt_a
    assert "rather than a rigid template" in prompt_a
    assert "# Visual style: editorial_minimal (category: skincare)" in prompt_a
    assert "--style editorial_minimal --category skincare" in row["import"]

    # 用户 --design 置顶主导，--scene 覆盖风格默认场景
    code, out_b = cli(
        "concept", "prompt", str(job_dir), "--step", "main",
        "--style", "clinical_lab", "--category", "skincare",
        "--design", "Deep cobalt #1E3A5F vertical accent bar",
        "--scene", "Brushed steel lab bench",
        "--name", "main-B", "--text", "VELA",
    )
    assert code == 0, out_b
    prompt_b = Path(out_b["data"]["prompts"][0]["file"]).read_text(encoding="utf-8")
    assert prompt_b.index("Deep cobalt #1E3A5F") < prompt_b.index("Visual style direction (Clinical Lab")
    assert "Brushed steel lab bench" in prompt_b
    assert "pale brushed metal" not in prompt_b

    # 不传 --style 时完全不碰风格目录（即使 URL 坏掉也 100% 离线可用）
    monkeypatch.setenv(cloud_styles.STYLES_URL_ENV, str(tmp_path / "does_not_exist.json"))
    code_plain, _ = cli(
        "concept", "prompt", str(job_dir), "--step", "main",
        "--design", "Warm cream and forest green typography",
        "--name", "main-plain", "--text", "VELA",
    )
    assert code_plain == 0
    assert design_fingerprint(job_dir, Job.load(job_dir)) == fp_before
    assert "visual_style" not in (job_dir / "job.json").read_text(encoding="utf-8")


def test_remote_cache_etag_and_offline_stale_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PACXIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(cloud_styles.STYLES_URL_ENV, "https://pacxio.dev/styles/catalog.json")
    monkeypatch.setenv(cloud_styles.STYLES_TTL_ENV, "3600")

    calls: list[dict[str, str]] = []
    payload_bytes = json.dumps(SAMPLE_CATALOG).encode("utf-8")

    class _Resp:
        def __init__(self, data: bytes, etag: str) -> None:
            self._data = data
            self.headers = {"ETag": etag}

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    mode = {"state": "ok"}

    def fake_urlopen(req: Any, timeout: float = 8.0) -> Any:
        calls.append(dict(req.header_items()))
        if mode["state"] == "ok":
            return _Resp(payload_bytes, '"etag-v1"')
        if mode["state"] == "304":
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)  # type: ignore[arg-type]
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(cloud_styles.urllib.request, "urlopen", fake_urlopen)

    # 1) 首次拉取并写缓存
    code, out = cli("concept", "styles")
    assert code == 0 and len(calls) == 1 and cloud_styles.cache_path().is_file()
    assert len(out["data"]["styles"]) == 5

    # 2) TTL 内命中缓存，不发网络请求
    code, _ = cli("concept", "styles")
    assert code == 0 and len(calls) == 1

    # 3) --refresh 带 If-None-Match 收到 304 正常复用
    mode["state"] = "304"
    code, _ = cli("concept", "styles", "--refresh")
    assert code == 0 and len(calls) == 2
    assert calls[-1].get("If-none-match") == '"etag-v1"'

    # 4) 断网时优雅降级旧缓存并附 STYLES_CACHE 提醒
    mode["state"] = "offline"
    code, out_stale = cli("concept", "styles", "--refresh")
    assert code == 0 and any(w["id"] == "STYLES_CACHE" for w in out_stale["warnings"])

    # 5) 既无缓存又断网：明确返回 exit 3 STYLES_NET
    cloud_styles.cache_path().unlink()
    code_err, out_err = cli("concept", "styles")
    assert code_err == 3 and out_err["errors"][0]["id"] == "STYLES_NET"


def test_data_driven_paper_fit_and_choose_rebuild_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_catalog: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    holo_job = make_job(tmp_path, "holo_card_350")
    code, out = cli(
        "concept", "prompt", str(holo_job), "--step", "main",
        "--style", "botanical_ink", "--name", "main-holo", "--text", "TEST",
    )
    assert code == 0 and any(w["id"] == "STYLE_FIT" for w in out["warnings"]), out

    # concept gen + offline import --style
    black_job = make_job(tmp_path, "black_card_350")
    fake = _FakeProvider()
    res = service.generate_concepts(
        black_job, design=None, texts=["NOCTIS"], n=1, provider=fake,
        visual_style="noir_foil", category="fragrance",
    )
    assert res.ok and res.data["visual_style"] == "noir_foil"

    # 即使完全离线无缓存，import --style 依然顺畅记录标签而不报错
    monkeypatch.setenv(cloud_styles.STYLES_URL_ENV, "https://pacxio.dev/styles/catalog.json")
    img = tmp_path / "ext.png"
    Image.new("RGB", (80, 80), (30, 30, 35)).save(img)
    code_imp, out_imp = cli(
        "concept", "import", str(black_job), str(img),
        "--name", "web_new", "--view", "main", "--style", "future-web-style",
    )
    assert code_imp == 0 and out_imp["data"]["visual_style"] == "future_web_style"


@pytest.mark.pro
def test_choose_concept_emits_style_rebuild_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_catalog: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    black_job = make_job(tmp_path, "black_card_350")
    fake = _FakeProvider()
    res = service.generate_concepts(
        black_job, design=None, texts=["NOCTIS"], n=1, provider=fake,
        visual_style="noir_foil", category="fragrance",
    )
    assert res.ok and res.data["visual_style"] == "noir_foil"
    cid = res.data["concepts"][0]
    chosen = service.choose_concept(black_job, cid, region=(0.1, 0.1, 0.9, 0.9), by="tester")
    assert chosen.ok and chosen.data["style_hints"]["style"] == "noir_foil"


def test_mcp_help_and_run_discover_concept_styles(local_catalog: Path) -> None:
    from pacxio import mcp_cli

    info = mcp_cli.help_text("concept styles")
    assert info["command"] == "concept styles" and not info["confirm_only"]
    res, imgs = mcp_cli.run("concept styles", ["--category", "fragrance"])
    assert res["ok"] and imgs == []
    assert res["data"]["recommended_pair"] == ["noir_foil", "botanical_ink"]
