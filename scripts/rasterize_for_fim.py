# -*- coding: utf-8 -*-
"""最后的兜底：把 PDF 逐页光栅化并叠加隐形文字层，保证体积低于网关限制。

- 页面渲染为 JPEG（默认130dpi/质量60），外观与原页面一致
- 原页面文字以隐形模式(render_mode=3)按原坐标写回，保留全文检索能力
- 用于内容以矢量图表为主、常规压缩无效的文件
"""
import sys
from pathlib import Path

import pymupdf

SAFE_LIMIT = 15 * 1024 * 1024


def rasterize(src: Path, dst: Path, dpi: int = 130, quality: int = 60) -> Path:
    doc = pymupdf.open(src)
    out = pymupdf.open()
    try:
        zoom = dpi / 72
        mat = pymupdf.Matrix(zoom, zoom)
        for page in doc:
            rect = page.rect
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = pix.tobytes("jpeg", jpg_quality=quality)
            new_page = out.new_page(width=rect.width, height=rect.height)
            new_page.insert_image(rect, stream=img)
            for w in page.get_text("words"):
                x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
                h = max(y1 - y0, 2.0)
                try:
                    new_page.insert_text(
                        (x0, y1 - h * 0.18), word,
                        fontsize=h * 0.85, fontname="china-s", render_mode=3,
                    )
                except Exception:  # noqa: BLE001 个别异常字符跳过
                    continue
        out.save(dst, garbage=4, deflate=True)
    finally:
        doc.close()
        out.close()
    return dst


def main() -> int:
    jobs = [
        (r"D:\固收部报告上传\外部\公募基金仓位、行业主题透视.pdf", 130, 60),
        (r"D:\固收部报告上传\外部\AI投研数据部署.pdf", 130, 60),
        (r"D:\固收部报告上传\_shrink\美债交流.pdf", 130, 60),
        (r"D:\固收部报告上传\_shrink\易方达固收AI应用经验分享.pdf", 130, 60),
    ]
    for src_str, dpi, q in jobs:
        src = Path(src_str)
        dst = src.with_name(src.stem + "_r.pdf")
        if dst.exists():
            dst.unlink()
        rasterize(src, dst, dpi, q)
        size = dst.stat().st_size
        # 校验文字层
        check = pymupdf.open(dst)
        text_len = sum(len(p.get_text()) for p in check)
        pages = check.page_count
        check.close()
        ok = "OK" if size < SAFE_LIMIT else "仍超限"
        print(f"{src.name}: {src.stat().st_size/1048576:.1f}MB -> {size/1048576:.1f}MB "
          f"[{pages}页, 文字层{text_len}字符] {ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
