# -*- coding: utf-8 -*-
"""label_crop.py —— 从整帧照片里自动找出每个标签并裁成单独图片

用途：数据集要"一图一标签"。相机一帧里能看到十来个标签，本模块负责：
  1. 找出所有**像标签**的矩形（60×40mm → 长宽比 1.5，这是关键先验）；
  2. **只保留完整的**：碰到画面边缘的（被裁掉一半的）一律剔除；
  3. **可选只保留指定列**（columns=…）：因为不同列的亮度可以差 40 多灰阶，
     混进同一个类别会让模型学到"暗 = 某类缺陷"这种假特征；
  4. 按列聚类 → 列内按行排序，编号成"第几列第几行"；
  5. 逐个裁出来（留 3% 边距），并给一张**标注预览图**：
     顶部标出每列的编号与亮度、绿框=已保存、红框=已剔除。

不做旋转矫正：纸稍微歪一点，矩形包围盒仍能完整包住标签；
但**歪得太厉害（>10°）会让包围盒混进旁边的纸**，所以摆纸时尽量摆正。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

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
COL_TOL = 0.45                           # 同一列的判据：|列中心差| < 该值 × 标签宽度


def _decode(data: bytes) -> np.ndarray:
    if not _HAS_CV2:
        raise RuntimeError("需要 opencv：py -3.11 -m pip install opencv-python")
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("图片解码失败")
    return img


def _group_columns(boxes: List[Tuple[int, int, int, int]]) -> List[List[Tuple[int, int, int, int]]]:
    """按 x 中心把标签聚成"列"（从左到右）。列容差取标签宽度的 COL_TOL 倍。"""
    cols: List[List[Tuple[int, int, int, int]]] = []
    for b in sorted(boxes, key=lambda t: t[0] + t[2] / 2):
        cx, w = b[0] + b[2] / 2, b[2]
        placed = False
        for col in cols:
            ref = col[0]
            if abs(cx - (ref[0] + ref[2] / 2)) < COL_TOL * max(w, ref[2]):
                col.append(b)
                placed = True
                break
        if not placed:
            cols.append([b])
    return cols


def detect_labels(data: bytes) -> Tuple[np.ndarray, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """返回 (灰度图, 标签列表, 列信息)。

    标签：box / row / col / aspect / area_pct / complete / reason / brightness
    列信息：col（从 1 开始，从左到右）/ x0 / x1 / count / avg_brightness
    """
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

    cols = _group_columns([(b[0], b[1], b[2], b[3]) for b in kept])
    items: List[Dict[str, Any]] = []
    col_info: List[Dict[str, Any]] = []
    for c_i, col in enumerate(cols, 1):
        col.sort(key=lambda b: b[1])                       # 列内从上到下
        vals = []
        for r_i, (x, y, w, h) in enumerate(col, 1):
            complete = not (x <= EDGE_MARGIN or y <= EDGE_MARGIN
                            or x + w >= W - EDGE_MARGIN or y + h >= H - EDGE_MARGIN)
            # 用标签内部（去掉外框那一圈）算亮度，避免边框把均值拉低
            inner = img[y + 6:y + h - 6, x + 6:x + w - 6]
            bright = float(inner.mean()) if inner.size else float(img[y:y + h, x:x + w].mean())
            vals.append(bright)
            items.append({"box": (int(x), int(y), int(w), int(h)), "row": r_i, "col": c_i,
                          "aspect": round(w / h, 2), "area_pct": round(w * h / (W * H) * 100, 1),
                          "brightness": round(bright, 1),
                          "complete": bool(complete),
                          "reason": "" if complete else "贴住画面边缘（被裁）"})
        col_info.append({
            "col": c_i,
            "x0": int(min(b[0] for b in col)), "x1": int(max(b[0] + b[2] for b in col)),
            "count": len(col),
            "avg_brightness": round(sum(vals) / len(vals), 1) if vals else None,
        })
    return img, items, col_info


def crop_labels(data: bytes, margin_pct: float = 0.03,
                columns: Optional[List[int]] = None) -> Dict[str, Any]:
    """把完整标签逐个裁成 JPEG。

    margin_pct：裁切时向外留一点边（0.03 = 标签尺寸的 3%），避免切到边框。
    columns   ：只保留这些列（1 起，从左到右）；None = 全部列。
    """
    img, labels, col_info = detect_labels(data)
    H, W = img.shape
    keep_set = set(int(c) for c in columns) if columns else None
    kept, dropped = [], []
    for it in labels:
        x, y, w, h = it["box"]
        if not it["complete"]:
            dropped.append({k: it[k] for k in ("box", "row", "col", "reason", "brightness")})
            continue
        if keep_set is not None and it["col"] not in keep_set:
            dropped.append({"box": it["box"], "row": it["row"], "col": it["col"],
                            "brightness": it["brightness"],
                            "reason": "不在保留的列里（列筛选）"})
            continue
        mx, my = int(w * margin_pct), int(h * margin_pct)
        x0, y0 = max(0, x - mx), max(0, y - my)
        x1, y1 = min(W, x + w + mx), min(H, y + h + my)
        crop = img[y0:y1, x0:x1]
        ok, enc = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            dropped.append({"box": it["box"], "row": it["row"], "col": it["col"],
                            "reason": "编码失败"})
            continue
        kept.append({"box": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)],
                     "row": it["row"], "col": it["col"], "aspect": it["aspect"],
                     "brightness": it["brightness"], "size": [int(x1 - x0), int(y1 - y0)],
                     "jpeg": enc.tobytes()})
    return {"kept": kept, "dropped": dropped, "columns": col_info,
            "preview": _preview(img, kept, dropped, col_info, keep_set),
            "total_found": len(labels), "kept_count": len(kept), "dropped_count": len(dropped)}


def _preview(img: np.ndarray, kept: List[Dict], dropped: List[Dict],
             col_info: List[Dict], keep_set: Optional[set]) -> Optional[bytes]:
    """标注预览图：顶部标出每列编号与亮度（保留的用绿色、被筛掉的用灰色）、
    绿框=已保存、红框=已剔除"""
    try:
        base = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        H, W = img.shape[:2]
        for c in col_info:
            keep = keep_set is None or c["col"] in keep_set
            color = (0, 190, 0) if keep else (128, 128, 128)
            cv2.rectangle(base, (c["x0"], 0), (c["x1"], 54), (0, 0, 0), -1)
            cv2.putText(base, "col%d  %.0f" % (c["col"], c["avg_brightness"] or 0),
                        (c["x0"] + 8, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.3, color, 3)
            cv2.line(base, (c["x0"], 54), (c["x0"], H), color, 2)
        for i, k in enumerate(kept, 1):
            x, y, w, h = k["box"]
            cv2.rectangle(base, (x, y), (x + w, y + h), (0, 200, 0), 5)
            cv2.putText(base, "%d" % i, (x + 8, y + 46), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 200, 0), 4)
        for d in dropped:
            x, y, w, h = d["box"]
            cv2.rectangle(base, (x, y), (x + w, y + h), (0, 0, 255), 5)
        ok, enc = cv2.imencode(".jpg", base, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return enc.tobytes() if ok else None
    except Exception:
        return None
