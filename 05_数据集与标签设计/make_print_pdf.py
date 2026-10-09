# -*- coding: utf-8 -*-
"""make_print_pdf.py —— 把打印页 PNG 转成可直接打印的 PDF

为什么用 PDF 而不是 Word：
  PDF 能把"页面尺寸"钉死成 A4（210×297mm），打印时选"实际大小/100%"就和标签设计完全一致；
  Word 会受页面设置、页边距、图片缩放影响，60×40mm 的标签和 100mm 校验尺很容易被缩掉。

用法: py -3.11 make_print_pdf.py
输出: print/标签打印_全6页_A4.pdf  +  每类单页 PDF（标签打印_1_正常_normal.pdf …）
"""
from __future__ import annotations

import io
import os
import re
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PRINT_DIR = os.path.join(HERE, "print")
A4_W, A4_H = 2480, 3508          # A4 @ 300 dpi
DPI = 300.0
A4_PT = (595.28, 841.89)         # A4 的 PDF 尺寸（点）

# 打印顺序 = 采集顺序：先合格品，再 5 种缺陷
ORDER = [
    ("normal",     "正常（合格品）"),
    ("brokenline", "断线"),
    ("faint",      "缺墨"),
    ("stain",      "污点"),
    ("scratch",    "划痕"),
    ("offset",     "偏位重影"),
]


def flatten(path: str) -> Image.Image:
    """RGBA → RGB（铺白底）。PDF 不支持透明通道，直接存会把底变黑。"""
    im = Image.open(path)
    if im.size != (A4_W, A4_H):
        print("  [!] %s 尺寸不是 A4@300dpi：%s" % (os.path.basename(path), im.size))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def save_pdf(images: list[Image.Image], out_path: str) -> int:
    first, rest = images[0], images[1:]
    first.save(out_path, "PDF", resolution=DPI, save_all=True, append_images=rest)
    return os.path.getsize(out_path)


def verify(path: str) -> str:
    """核对页数与页面尺寸。页面尺寸从 MediaBox 换算成毫米，必须是 A4。"""
    raw = open(path, "rb").read()
    # 注意：必须排除 "/Type /Pages"（页树节点），否则页数会多算一个
    pages = len(re.findall(rb"/Type\s*/Page(?![s])", raw))
    boxes = re.findall(rb"/MediaBox\s*\[([^\]]+)\]", raw)
    if not boxes:
        return "读不到 MediaBox"
    nums = [float(x) for x in boxes[0].split()]
    w_mm = (nums[2] - nums[0]) / 72 * 25.4
    h_mm = (nums[3] - nums[1]) / 72 * 25.4
    return "%d 页，页面 %.1f×%.1f mm（A4=210.0×297.0）" % (pages, w_mm, h_mm)


def main() -> int:
    if not os.path.isdir(PRINT_DIR):
        print("[x] 找不到 print 目录：%s" % PRINT_DIR)
        return 1

    todo = []
    for key, label in ORDER:
        src = os.path.join(PRINT_DIR, "A4_%s.png" % key)
        if not os.path.isfile(src):
            print("  [!] 缺少 %s" % src)
            continue
        todo.append((key, label, src))
    if not todo:
        print("[x] 没有可用的打印页")
        return 1

    print("=" * 64)

    # ★ 每个 PDF 都**重新打开**源图再保存。
    #   踩过的坑：Pillow 保存 PDF 时会把 append_images 记在图像对象自己身上，
    #   如果拿同一批对象先存合并版、再存单页版，单页 PDF 会变成 6 页（实测确认）。
    print("单页 PDF（每类一张，适合先试打一张）：")
    for i, (key, label, src) in enumerate(todo, 1):
        img = flatten(src)
        out = os.path.join(PRINT_DIR, "标签打印_%d_%s_%s.pdf" % (i, label.replace("（合格品）", ""), key))
        size = save_pdf([img], out)
        print("  %-42s %s  %.2f MB" % (os.path.basename(out), verify(out), size / 1048576))

    print("-" * 64)
    pages = [flatten(src) for _, _, src in todo]
    combined = os.path.join(PRINT_DIR, "标签打印_全6页_A4.pdf")
    size = save_pdf(pages, combined)
    print("合并 %d 页 -> %s" % (len(pages), os.path.basename(combined)))
    print("      %s   %.2f MB" % (verify(combined), size / 1048576))

    print("=" * 64)
    print("打印要点：")
    print("  1) 纸张 A4、单面、实际大小（100%），不要选「适应页面 / 缩放」")
    print("  2) 打完用尺子量页脚的 100 mm 校验尺：是 100 mm 才算对")
    print("  3) 标签 60×40 mm，每页 18 个；沿裁切标记剪开")
    print("  4) 同一批数据用同一台打印机，别中途换")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
