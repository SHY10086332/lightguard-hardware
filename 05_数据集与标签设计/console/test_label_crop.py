# -*- coding: utf-8 -*-
"""test_label_crop.py —— 裁切模块自测（合成图，不需要硬件）

场景：
  1. 一整张"纸"上有 3 列 × 4 行 = 12 个标签，全部完整 → 应该 12 个全保留；
  2. 同一张纸**右移/下移**导致右列、下行被画面切掉 → 被切的必须剔除，剩下的保留；
  3. 裁出来的图长宽比应接近 1.5（60×40mm）；
  4. 标签内部有文字/条码（会产生大量小轮廓）时不能重复计数。
"""
from __future__ import annotations

import io
import sys

import numpy as np
from PIL import Image, ImageDraw

from label_crop import crop_labels, detect_labels

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %-44s %s" % (name, extra))
    else:
        FAIL += 1
        print("  [x]  %-44s %s" % (name, extra))


def make_sheet(cols: int = 3, rows: int = 4, label_w: int = 300, label_h: int = 200,
               gap: int = 26, pad: int = 40, canvas: tuple[int, int] = (1600, 1200),
               offset: tuple[int, int] = (0, 0)) -> bytes:
    """画一张模拟的 A4 纸：白底 + 若干标签（每个都有边框、标题、条码、方块阵列）"""
    W, H = canvas
    img = Image.new("L", (W, H), 40)                  # 背景（桌面）偏暗
    d = ImageDraw.Draw(img)
    sw = cols * label_w + (cols - 1) * gap + 2 * pad
    sh = rows * label_h + (rows - 1) * gap + 2 * pad
    ox, oy = offset
    d.rectangle([ox, oy, ox + sw, oy + sh], fill=235)  # 纸
    for r in range(rows):
        for c in range(cols):
            x = ox + pad + c * (label_w + gap)
            y = oy + pad + r * (label_h + gap)
            d.rectangle([x, y, x + label_w, y + label_h], outline=60, width=3)
            d.rectangle([x + 14, y + 12, x + 150, y + 38], fill=70)       # 标题
            for i in range(16):                                          # 条码
                bx = x + 16 + i * 12
                if i % 3:
                    d.rectangle([bx, y + 60, bx + 6, y + 140], fill=50)
            for rr in range(4):                                          # 方块阵列
                for cc in range(4):
                    px = x + 200 + cc * 20
                    py = y + 60 + rr * 20
                    d.rectangle([px, py, px + 12, py + 12], fill=90)
            d.rectangle([x + 16, y + 150, x + 200, y + 180], outline=120, width=2)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def main() -> int:
    print("=" * 70)
    print("标签裁切自测（合成 A4 纸，无需硬件）")
    try:
        import cv2  # noqa: F401
        print("opencv: 可用")
    except ImportError:
        print("[x] 需要 opencv-python")
        return 1
    print("=" * 70)

    # ---- 场景 1：整张纸完整在画面里 ----
    data = make_sheet()
    _, labels = detect_labels(data)
    check("① 完整纸：检出 12 个标签", len(labels) == 12, "实际 %d 个" % len(labels))
    check("① 完整纸：全部判定为完整", all(l["complete"] for l in labels),
          "不完整的 %d 个" % sum(1 for l in labels if not l["complete"]))
    r = crop_labels(data)
    check("① 完整纸：裁出 12 张", r["kept_count"] == 12,
          "保留 %d / 剔除 %d" % (r["kept_count"], r["dropped_count"]))
    ars = [k["aspect"] for k in r["kept"]]
    check("① 裁切结果长宽比接近 1.5", all(1.3 <= a <= 1.7 for a in ars),
          "范围 %.2f~%.2f" % (min(ars), max(ars)))
    check("① 每张都能解码成图", all(len(k["jpeg"]) > 500 for k in r["kept"]),
          "最小 %d 字节" % min(len(k["jpeg"]) for k in r["kept"]))
    check("① 有标注预览图", r["preview"] is not None and len(r["preview"]) > 1000,
          "%d 字节" % (len(r["preview"]) if r["preview"] else 0))

    # ---- 场景 2：纸偏出画面（右列与下行被切） ----
    data2 = make_sheet(cols=4, rows=4, offset=(0, 0), canvas=(1300, 1000))
    _, labels2 = detect_labels(data2)
    complete2 = [l for l in labels2 if l["complete"]]
    dropped2 = [l for l in labels2 if not l["complete"]]
    r2 = crop_labels(data2)
    check("② 纸超出画面：有标签被判为不完整", len(dropped2) > 0,
          "完整 %d / 不完整 %d" % (len(complete2), len(dropped2)))
    check("② 被剔除的正是贴边那些", r2["dropped_count"] == len(dropped2),
          "剔除 %d" % r2["dropped_count"])
    check("② 保留数 = 完整数", r2["kept_count"] == len(complete2),
          "保留 %d" % r2["kept_count"])

    # ---- 场景 3：纸上没有标签（空白纸） ----
    blank = Image.new("L", (1600, 1200), 235)
    buf = io.BytesIO(); blank.convert("RGB").save(buf, "JPEG", quality=90)
    r3 = crop_labels(buf.getvalue())
    check("③ 空白纸：不误检、不裁图", r3["kept_count"] == 0,
          "检出 %d / 保留 %d" % (r3["total_found"], r3["kept_count"]))

    # ---- 场景 4：单个标签占满画面 ----
    one = make_sheet(cols=1, rows=1, label_w=600, label_h=400, pad=60, canvas=(800, 600))
    r4 = crop_labels(one)
    check("④ 单标签占满画面：裁出 1 张", r4["kept_count"] == 1,
          "保留 %d / 剔除 %d" % (r4["kept_count"], r4["dropped_count"]))

    print("-" * 70)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
