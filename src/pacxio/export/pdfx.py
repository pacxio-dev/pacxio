"""PDF/X-4 结构后处理（）：OutputIntent、XMP、Trapped、PDF 版本。

关于 ICC（Q4）：
- 配置档指定了 ICC：嵌入为 OutputIntent 的 DestOutputProfile，XMP 与 Info 声明 PDF/X-4，
  PDF 版本不低于 1.6；
- 未指定：仍写出 OutputIntent / XMP 结构，但**不声明** PDF/X-4 合规（没有输出配置就不能声称），
  由检查项 X02 给出降级警告。

 要求用 pikepdf 写这些结构；开源工具没有完整的 PDF/X 校验器，
合规性仍需用 Acrobat 预检人工确认（ "校验"）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pikepdf
from pikepdf import Dictionary, Name, Pdf, String

from pacxio import __version__
from pacxio.model.profile import Profile

SHOW_TEXT_OPS = {"Tj", "TJ", "'", '"'}
PDFX_VERSION = "PDF/X-4"
PDFX_MIN_PDF_VERSION = "1.6"
SOURCE_DATE_EPOCH = "SOURCE_DATE_EPOCH"
OUTPUT_CONDITION_FALLBACK = "Custom"


def document_date() -> datetime:
    """文档日期。设置了 SOURCE_DATE_EPOCH（可复现构建的通用约定）时用它，否则取当前时间。"""
    epoch = os.environ.get(SOURCE_DATE_EPOCH)
    if epoch is not None:
        return datetime.fromtimestamp(int(epoch), tz=UTC)
    return datetime.now(tz=UTC)


def _pdf_date(d: datetime) -> str:
    return d.strftime("D:%Y%m%d%H%M%S+00'00'")


def _xmp(title: str, d: datetime, claim_pdfx: bool) -> bytes:
    iso = d.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    pdfx = f"<pdfxid:GTS_PDFXVersion>{PDFX_VERSION}</pdfxid:GTS_PDFXVersion>" if claim_pdfx else ""
    xml = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:pdf="http://ns.adobe.com/pdf/1.3/" '
        'xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/">'
        f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{escape(title)}</rdf:li></rdf:Alt></dc:title>'
        f"<xmp:CreatorTool>pacxio {__version__}</xmp:CreatorTool>"
        f"<xmp:CreateDate>{iso}</xmp:CreateDate>"
        f"<xmp:ModifyDate>{iso}</xmp:ModifyDate>"
        f"<xmp:MetadataDate>{iso}</xmp:MetadataDate>"
        "<pdf:Producer>pacxio</pdf:Producer>"
        "<pdf:Trapped>False</pdf:Trapped>"
        f"{pdfx}"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    return xml.encode("utf-8")


def strip_unused_fonts(pdf: Pdf) -> int:
    """删掉页面里没有被任何显示文字操作符用到的字体引用，返回删除的页数。

    ReportLab 总会在每页内容流开头写一个空的 `BT /F1 12 Tf ET` 并登记 /F1 Helvetica，
    印刷文件不该引用任何字体（D7；PDF/X 也要求字体嵌入）。真正在显示文字的页面不动，
    留给检查项 T03 报错。
    """
    changed = 0
    for page in pdf.pages:
        ops = list(pikepdf.parse_content_stream(page))
        if any(str(i.operator) in SHOW_TEXT_OPS for i in ops):
            continue
        kept = [i for i in ops if str(i.operator) not in {"Tf", "BT", "ET", "TL"}]
        if len(kept) != len(ops):
            page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(kept))
        if "/Resources" in page and "/Font" in page.Resources:
            del page.Resources["/Font"]
            changed += 1
    return changed


def clean_pdf(path: Path) -> None:
    """对不需要 PDF/X 结构的 PDF（刀版、工艺分版）只做字体引用清理。"""
    with Pdf.open(path, allow_overwriting_input=True) as pdf:
        strip_unused_fonts(pdf)
        pdf.save(path, deterministic_id=True)


def finalize_pdfx(path: Path, profile: Profile, title: str, base_dir: Path | None = None) -> bool:
    """给 reportlab 写出的 PDF 补上 PDF/X 结构。返回是否声明了 PDF/X-4（即是否有 ICC）。"""
    icc_path = Path(profile.icc.path) if profile.icc.path else None
    if icc_path is not None and base_dir is not None and not icc_path.is_absolute():
        icc_path = base_dir / icc_path
    claim = icc_path is not None
    d = document_date()

    with Pdf.open(path, allow_overwriting_input=True) as pdf:
        strip_unused_fonts(pdf)
        intent = Dictionary(
            Type=Name.OutputIntent,
            S=Name.GTS_PDFX,
            OutputConditionIdentifier=String(profile.icc.name or OUTPUT_CONDITION_FALLBACK),
            Info=String(profile.icc.name or "No output profile specified (degraded)"),
        )
        if icc_path is not None:
            stream = pdf.make_stream(icc_path.read_bytes())
            stream.N = 4
            intent.DestOutputProfile = stream
        pdf.Root.OutputIntents = pikepdf.Array([pdf.make_indirect(intent)])

        meta = pdf.make_stream(_xmp(title, d, claim))
        meta.Type = Name.Metadata
        meta.Subtype = Name.XML
        pdf.Root.Metadata = meta

        info = pdf.docinfo
        info.Title = String(title)
        info.Creator = String(f"pacxio {__version__}")
        info.Producer = String("pacxio")
        info.CreationDate = String(_pdf_date(d))
        info.ModDate = String(_pdf_date(d))
        info.Trapped = Name("/False")  # 注意：Name.False_ 会写成 /False_
        if claim:
            info.GTS_PDFXVersion = String(PDFX_VERSION)

        pdf.save(
            path,
            deterministic_id=True,
            min_version=PDFX_MIN_PDF_VERSION if claim else pdf.pdf_version,
        )
    return claim
