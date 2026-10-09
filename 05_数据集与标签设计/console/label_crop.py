# -*- coding: utf-8 -*-
"""label_crop.py —— 从整帧照片里自动找出每个标签并裁成单独图片

用途：数据集要"一图一标签"。相机一帧里能看到十来个标签，本模块负责：
  1. 找出所有**像标签**的矩形（60×40mm → 长宽比 1.5，这是关键先验）；
  2. **只保留完整的**：碰到画面边缘的（被裁掉一半的）一律剔除 —— 按用户要求
     "哪列完整就保留，不完整的直接剔除"；
  3. 去重（标签内部的条码/文字会产生一堆小轮廓，要按位置合并）；
  4. 按"从上到下、从左到右"排序后逐个裁出来（留一点边距）；
  5. 顺便给出一张**标注预览图**：绿框=保留、红框=剔除（边缘被切），方便人工复核。

不做旋转矫正：画面里纸即使有点歪，矩形包围盒仍能完整包住标签；
但**歪得太厉害（>10°）会让包围盒混进旁边的纸**，所以摆纸时尽量摆正。
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

MIN_ASPECT, MAX_ASPECT = 1.15, 1.95      # 60/40 = 1.5，给透视留余量
# 上限 55%：用来排除"整张纸"那种巨大轮廓（纸通常占 60% 以上）；
# 但如果相机凑得很近、一个标签就占了半屏，仍应被认出来（自测场景④）。
MIN_AREA_PCT, MAX_AREA_PCT = 0.4, 55.0
DEDUPE_DIST = 60                         # 中心距小于它视为同一个标签（像素）
EDGE_MARGIN = 8                          # 离画面边缘小于它就算"被裁"，剔除


def _decode(data: bytes) -> np.ndarray:
    if not _HAS_CV2:
        raise RuntimeError("需要 opencv：py -3.11 -m pip install opencv-python")
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("图片解码失败")
    return img


def detect_labels(data: bytes) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """返回 (灰度图, 标签列表)。每项含 box/complete/area_pct/aspect。"""
    img = _decode(data)
    H, W = img.shape
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cnts, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    raw = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 80 or h < 60:
            continue
        ar = w / h
        if not (MIN_ASPECT <= ar <= MAX_ASPECT):
            continue
        area = w * h / (W * H) * 100
        if not (MIN_AREA_PCT <= area <= MAX_AREA_PCT):
            continue
        raw.append((x, y, w, h, ar, area))

    # 去重：中心点距离太近的只留面积大的那个
    raw.sort(key=lambda b: -b[2] * b[3])
    kept: List[Tuple[int, int, int, int, float, float]] = []
    for b in raw:
        cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
        if all(abs(cx - (k[0] + k[2] / 2)) > DEDUPE_DIST or abs(cy - (k[1] + k[3] / 2)) > DEDUPE_DIST
               for k in kept):
            kept.append(b)

    # 按行归类（同一行的 y 相近），行内按 x 排序
    kept.sort(key=lambda b: (b[1] + b[3] / 2, b[0] + b[2] / 2))
    rows: List[List[Tuple]] = []
    for b in kept:
        cy = b[1] + b[3] / 2
        if rows and abs(cy - (rows[-1][0][1] + rows[-1][0][3] / 2)) < 150:
            rows[-1].append(b)
        else:
            rows.append([b])
    out = []
    for r_i, row in enumerate(rows, 1):
        row.sort(key=lambda b: b[0])
        for c_i, (x, y, w, h, ar, area) in enumerate(row, 1):
            complete = not (x <= EDGE_MARGIN or y <= EDGE_MARGIN
                            or x + w >= W - EDGE_MARGIN or y + h >= H - EDGE_MARGIN)
            out.append({"box": (int(x), int(y), int(w), int(h)), "row": r_i, "col": c_i,
                        "aspect": round(ar, 2), "area_pct": round(area, 1),
                        "complete": bool(complete),
                        "reason": "" if complete else "贴住画面边缘（被裁）"})
    return img, out


def crop_labels(data: bytes, margin_pct: float = 0.03) -> Dict[str, Any]:
    """把完整标签逐个裁成 JPEG。

    margin_pct：裁切时向外留一点边（0.03 = 标签尺寸的 3%），避免切到边框。
    """
    img, labels = detect_labels(data)
    H, W = img.shape
    kept, dropped = [], []
    for it in labels:
        x, y, w, h = it["box"]
        if not it["complete"]:
            dropped.append({k: it[k] for k in ("box", "row", "col", "reason")})
            continue
        mx, my = int(w * margin_pct), int(h * margin_pct)
        x0, y0 = max(0, x - mx), max(0, y - my)
        x1, y1 = min(W, x + w + mx), min(H, y + h + my)
        crop = img[y0:y1, x0:x1]
        ok, enc = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            dropped.append({"box": it["box"], "row": it["row"], "col": it["col"], "reason": "编码失败"})
            continue
        kept.append({"box": [x0, y0, x1 - x0, y1 - y0], "row": it["row"], "col": it["col"],
                     "aspect": it["aspect"], "area_pct": it["area_pct"],
                     "size": [int(x1 - x0), int(y1 - y0)],
                     "jpeg": enc.tobytes()})
    return {"kept": kept, "dropped": dropped, "preview": _preview(img, kept, dropped),
            "total_found": len(labels), "kept_count": len(kept), "dropped_count": len(dropped)}


def _preview(img: np.ndarray, kept: List[Dict], dropped: List[Dict]) -> bytes | None:
    """标注预览图：绿框=保留、红框=剔除，并写上编号"""
    try:
        base = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        for i, k in enumerate(kept, 1):
            x, y, w, h = k["box"]
            cv2.rectangle(base, (x, y), (x + w, y + h), (0, 200, 0), 5)
            cv2.putText(base, str(i), (x + 8, y + 46), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 200, 0), 4)
        for d in dropped:
            x, y, w, h = d["box"]
            cv2.rectangle(base, (x, y), (x + w, y + h), (0, 0, 255), 5)
        ok, enc = cv2.imencode(".jpg", base, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return enc.tobytes() if ok else None
    except Exception:
        return None
