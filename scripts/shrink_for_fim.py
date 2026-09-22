# -*- coding: utf-8 -*-
"""为超过网关体积限制的报告生成可上传的压缩版本。

输出到 D:\\固收部报告上传\\_shrink\\，优先保持原格式：
  pptx/docx -> 重压缩内嵌图片后重打包（同名）
  压缩后仍超限 -> LibreOffice 转 PDF（改扩展名 .pdf）
  pdf -> PyMuPDF rewrite_images 降采样重写（同名）

网关限制实测：15.7MiB 成功、17.8MiB 失败，目标压到 SAFE_LIMIT 以下。
"""
import io
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image

DEST_ROOT = Path(r"D:\固收部报告上传")
SHRINK_DIR = DEST_ROOT / "_shrink"
CACHE_DIR = Path(r"D:\juyuan_credit_data\internal_knowledge_base\pdf_cache")
SOFFICE = r"D:\LibreOffice\program\soffice.com"
SAFE_LIMIT = 15 * 1024 * 1024          # 目标 15MiB
JPEG_QUALITY = 72
MAX_PIXELS = 1600                       # 图片最长边
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def shrink_image(data: bytes) -> bytes | None:
    """把图片重压为 JPEG（白底），过大则先缩放。失败返回 None 表示保留原图。"""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001
        return None
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    scale = MAX_PIXELS / max(w, h)
    if scale < 1:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def shrink_office(src: Path, dst: Path) -> Path:
    """重压缩 pptx/docx 内的位图并重打包（其余成员原样保留）。"""
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        buffers = {}
        for name in names:
            data = zin.read(name)
            lowered = name.lower()
            if "/media/" in lowered and lowered.rsplit(".", 1)[-1] in MEDIA_EXTS:
                if len(data) > 150 * 1024:
                    small = shrink_image(data)
                    if small is not None and len(small) < len(data):
                        data = small
            buffers[name] = data
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        for name in names:
            zout.writestr(name, buffers[name])
    return dst


def shrink_pdf(src: Path, dst: Path, dpi_target: int = 110, quality: int = 60) -> Path:
    import pymupdf

    doc = pymupdf.open(src)
    try:
        # Document 级 API：重压缩全部图片（1.28 移除了 Page.rewrite_images）
        doc.rewrite_images(dpi_threshold=96, dpi_target=dpi_target, quality=quality)
        doc.subset_fonts()
        doc.save(dst, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    return dst


def convert_pdf(src: Path, dst: Path) -> Path:
    subprocess.run(
        [SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", str(dst.parent), str(src)],
        check=True, capture_output=True, timeout=300,
    )
    out = dst.with_suffix(".pdf")
    if not out.is_file():
        raise RuntimeError(f"LibreOffice 未生成 {out}")
    return out


def cached_pdf(src: Path, sha256: str) -> Path | None:
    p = CACHE_DIR / f"{sha256}.pdf"
    return p if p.is_file() else None


def process(path: Path, sha256: str) -> Path | None:
    """返回压到 SAFE_LIMIT 以下的候选文件路径；失败返回 None。"""
    SHRINK_DIR.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    out = SHRINK_DIR / path.name
    candidates = []
    if ext in (".pptx", ".docx"):
        candidates.append(("原格式压缩", lambda: shrink_office(path, out)))
        cand = cached_pdf(path, sha256)
        if cand:
            candidates.append(("已有PDF缓存", lambda: shutil_copy(cand, out.with_suffix(".pdf"))))
        candidates.append(("转PDF", lambda: convert_pdf(path, out.with_suffix(".pdf"))))
    elif ext == ".pdf":
        candidates.append(("PDF重写", lambda: shrink_pdf(path, out)))

    for label, fn in candidates:
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            print(f"    [{label} 失败] {e}")
            continue
        size = result.stat().st_size
        print(f"    [{label}] {size/1048576:.1f}MB -> {result.name}")
        if size < SAFE_LIMIT:
            return result
    return None


def shutil_copy(src: Path, dst: Path) -> Path:
    import shutil

    shutil.copy2(src, dst)
    return dst


def main() -> int:
    import csv

    SHRINK_DIR.mkdir(exist_ok=True)
    rows = list(csv.DictReader(open(DEST_ROOT / "manifest.csv", encoding="utf-8-sig")))
    todo = [r for r in rows if r["上传状态"] == "失败" and int(r["大小"]) > SAFE_LIMIT]
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        todo = [r for r in rows if int(r["大小"]) > SAFE_LIMIT]
    print(f"待压缩 {len(todo)} 个:")
    results = {}
    for r in todo:
        key = f"{r['目标文件夹']}/{r['上传文件名']}"
        print(f"  {int(r['大小'])/1048576:6.1f}MB {key}")
        src = DEST_ROOT / r["目标文件夹"] / r["上传文件名"]
        fixed = process(src, r["SHA256"])
        results[key] = str(fixed) if fixed else ""
        if fixed:
            print(f"    => 可用: {fixed.name} ({fixed.stat().st_size/1048576:.1f}MB)")
        else:
            print("    => 无法压到限制内")
    out_csv = SHRINK_DIR / "shrink_results.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["目标文件夹/上传文件名", "压缩后路径"])
        for k, v in results.items():
            w.writerow([k, v])
    print(f"结果清单: {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
