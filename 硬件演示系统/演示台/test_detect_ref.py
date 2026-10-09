# -*- coding: utf-8 -*-
"""test_detect_ref.py —— 参考图比对检测器的自测（合成图，不需要硬件）

用法: py -3.11 test_detect_ref.py

构造一张"合格标签"参考图，再生成各种变动，检查判定是否符合预期：
  · 只加噪声 / 只有 JPEG 压缩 / 轻微平移   -> 应判「正常」
  · 污点 / 断线 / 划痕 / 明显偏位          -> 应判「异常」
"""
from __future__ import annotations

import io
import sys

import numpy as np
from PIL import Image, ImageDraw

from detect_ref import ReferenceDetector

BASE_W, BASE_H = 480, 320
PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %-34s %s" % (name, extra))
    else:
        FAIL += 1
        print("  [x]  %-34s %s" % (name, extra))


def make_label() -> Image.Image:
    """画一张模拟的打印标签：白底 + 边框 + 标题块 + 6x6 方块阵列 + 条码"""
    img = Image.new("L", (BASE_W, BASE_H), 238)
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, BASE_W - 7, BASE_H - 7], outline=60, width=3)          # 外框
    d.rectangle([20, 18, 190, 40], fill=70)                                   # 标题块
    for r in range(6):                                                        # 6x6 阵列
        for c in range(6):
            x = 24 + c * 22
            y = 70 + r * 22
            d.rectangle([x, y, x + 14, y + 14], fill=90)
    for i in range(28):                                                       # 条码
        x = 200 + i * 9
        if i % 3:
            d.rectangle([x, 230, x + 4, 290], fill=50)
    d.rectangle([24, 230, 170, 292], outline=120, width=2)                    # 型号框
    d.line([24, 300, 440, 300], fill=100, width=2)                            # 页脚线
    return img


def jpeg(img: Image.Image, q: int = 92) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q)
    return buf.getvalue()


def add_noise(img: Image.Image, sigma: float = 3.0) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    rng = np.random.default_rng(7)
    arr = np.clip(arr + rng.normal(0, sigma, arr.shape), 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


def main() -> int:
    print("=" * 62)
    print("参考图比对检测器自测（合成图，无需硬件）")
    try:
        import cv2  # noqa: F401
        print("opencv: 可用（有对齐与形态学去噪）")
    except ImportError:
        print("opencv: 不可用（退化模式，仅基础比对）")
    print("=" * 62)

    base = make_label()
    det = ReferenceDetector(defect_ratio_threshold=0.25)
    info = det.set_reference(jpeg(base))
    print("参考图: %dx%d" % (info["width"], info["height"]))
    print("-" * 62)

    # 1. 同一张图重新编码
    r = det.compare(jpeg(base))
    check("完全相同的图 -> 正常", r["verdict"] == "normal", "差异 %.3f%%" % r["defect_ratio"])

    # 2. 高压缩重编码
    r = det.compare(jpeg(base, 55))
    check("高强度 JPEG 压缩 -> 正常", r["verdict"] == "normal", "差异 %.3f%%" % r["defect_ratio"])

    # 3. 传感器噪声
    r = det.compare(jpeg(add_noise(base, 4.0)))
    check("叠加噪声 -> 正常", r["verdict"] == "normal", "差异 %.3f%%" % r["defect_ratio"])

    # 4. 轻微平移（相机微动，应被对齐吃掉）
    shifted = Image.new("L", base.size, 238)
    shifted.paste(base, (6, 4))
    r = det.compare(jpeg(shifted))
    check("整体平移 6,4 像素 -> 正常", r["verdict"] == "normal",
          "差异 %.3f%% 对齐 %s" % (r["defect_ratio"], r["shift"]))

    # 5. 污点
    stain = base.copy()
    ImageDraw.Draw(stain).ellipse([150, 120, 186, 152], fill=45)
    r = det.compare(jpeg(stain))
    check("污点（直径约 35px） -> 异常", r["verdict"] == "abnormal", "差异 %.3f%%" % r["defect_ratio"])

    # 6. 断线（擦掉阵列里一行方块）
    broken = base.copy()
    ImageDraw.Draw(broken).rectangle([20, 112, 160, 132], fill=238)
    r = det.compare(jpeg(broken))
    check("断线（擦掉一排方块） -> 异常", r["verdict"] == "abnormal", "差异 %.3f%%" % r["defect_ratio"])

    # 7. 划痕
    scratch = base.copy()
    ImageDraw.Draw(scratch).line([40, 250, 160, 268], fill=70, width=3)
    r = det.compare(jpeg(scratch))
    check("划痕（3px 细线） -> 异常", r["verdict"] == "abnormal", "差异 %.3f%%" % r["defect_ratio"])

    # 8. 明显偏位（超出对齐搜索范围的整体位移）
    offset = Image.new("L", base.size, 238)
    offset.paste(base, (30, 22))
    r = det.compare(jpeg(offset))
    check("偏位 30,22 像素 -> 异常", r["verdict"] == "abnormal",
          "差异 %.3f%% 对齐 %s" % (r["defect_ratio"], r["shift"]))

    # 9. 差异图能生成
    check("差异预览图可生成", r["diff_jpeg"] is not None and len(r["diff_jpeg"]) > 1000,
          "%d 字节" % (len(r["diff_jpeg"]) if r["diff_jpeg"] else 0))

    # 10. 没设参考图时应报错
    det2 = ReferenceDetector()
    try:
        det2.compare(jpeg(base))
        check("未设参考图时报错", False, "竟然没报错")
    except ValueError as exc:
        check("未设参考图时报错", True, str(exc)[:24])

    print("-" * 62)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
