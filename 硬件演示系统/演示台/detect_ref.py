# -*- coding: utf-8 -*-
"""detect_ref.py —— 固定机位下的"参考图比对"检测（演示台内置，独立可用）

定位要说清楚
------------
这是**演示台自带**的一套轻量检测，用来把"拍照 → 判定 → 红绿指示"这条硬件闭环跑通，
**不是**替换比赛作品里的模型（那边是 PatchCore 异常检测，属另一套系统）。
它的原理很朴素，但对"固定机位 + 固定光照"的打印标签足够有效：

    参考图（合格品） ──► 灰度化 ──► 高斯模糊 ──► 对齐 ──► 逐像素差
                                                      │
                                          阈值化 ──► 去噪（形态学开运算）
                                                      │
                                     差异像素占比 > 阈值 ? 异常 : 正常

为什么这几步都不能省（都是实测总结）：
  · 灰度 + 模糊：吸收 JPEG 压缩噪声与传感器噪声，否则"正常"也会被判成异常；
  · **对齐**：相机稍动一点整幅图就全差，所以先在缩小图上做模板匹配找最佳平移，再比；
  · **形态学开运算**：去掉孤立噪点，只留下成片的真实差异（污点/断线/缺墨/划痕）；
  · 用**差异占比**（%）而不是平均差：小面积缺陷在平均差里会被淹没。

输出里额外给一张"差异图"（红色标出异常区域），既方便现场判断，也方便截图进材料。
"""
from __future__ import annotations

import io

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:                     # 没有 opencv 也能跑，只是少了形态学去噪与对齐
    _HAS_CV2 = False

from PIL import Image

ALIGN_SEARCH = 16        # 对齐搜索范围（像素，原图尺度）
BLUR_KERNEL = 5
PIXEL_THRESHOLD = 30     # 灰度差超过多少算"不一样"
MIN_BLOB_AREA = 9        # 小于这个面积的差异当噪声丢掉（像素）
EDGE_MARGIN = 12         # 画面边缘这么多像素不参与判定


def _to_gray(data: bytes) -> np.ndarray:
    if _HAS_CV2:
        buf = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("图片解码失败")
        return img
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("L"))


def _blur(img: np.ndarray) -> np.ndarray:
    if _HAS_CV2:
        return cv2.GaussianBlur(img, (BLUR_KERNEL, BLUR_KERNEL), 0)
    return img.astype(np.float32)          # 退化情况：不模糊


def _best_shift(ref: np.ndarray, cur: np.ndarray, search: int = ALIGN_SEARCH) -> tuple[int, int]:
    """找出 cur 相对 ref 的最佳平移（原图尺度像素）。

    两段式：先在 1/4 缩小图上粗搜（快），再在**原图中心裁剪区**上做 ±5 像素精修。
    只粗搜是不够的 —— 精度只有 4 像素，残差 2 像素就会让整幅图被判成"全都不一样"
    （自测里"整体平移 6,4 像素"因此误判成差异 12%，加上精修后降到正确结果）。
    """
    if not _HAS_CV2:
        return 0, 0
    scale = 4
    h, w = ref.shape
    if h // scale < search or w // scale < search:
        return 0, 0
    small_ref = cv2.resize(ref, (w // scale, h // scale), interpolation=cv2.INTER_AREA)
    small_cur = cv2.resize(cur, (w // scale, h // scale), interpolation=cv2.INTER_AREA)
    pad = search // scale + 1
    th, tw = small_cur.shape[0] - 2 * pad, small_cur.shape[1] - 2 * pad
    if th < 8 or tw < 8:
        return 0, 0
    tmpl = small_cur[pad:pad + th, pad:pad + tw]
    region = small_ref[0:th + 2 * pad, 0:tw + 2 * pad]
    if region.shape[0] < tmpl.shape[0] or region.shape[1] < tmpl.shape[1]:
        return 0, 0
    res = cv2.matchTemplate(region.astype(np.float32), tmpl.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(res)
    dx = (max_loc[0] - pad) * scale
    dy = (max_loc[1] - pad) * scale

    # ---- 原图精修（只看中心裁剪区，够快）----
    crop = 400
    ch = min(crop, h - 2 * 8)
    cw = min(crop, w - 2 * 8)
    y0, x0 = max(0, (h - ch) // 2), max(0, (w - cw) // 2)
    ref_c = ref[y0:y0 + ch, x0:x0 + cw].astype(np.int16)
    best, best_score = (dx, dy), None
    for oy in range(-5, 6):
        for ox in range(-5, 6):
            cand = (dx + ox, dy + oy)
            sh = _shift(cur, cand[0], cand[1])
            cur_c = sh[y0:y0 + ch, x0:x0 + cw].astype(np.int16)
            score = -float(np.mean(np.abs(ref_c - cur_c)))     # 越接近 0 越好
            if best_score is None or score > best_score:
                best_score, best = score, cand
    dx, dy = best
    dx = max(-search, min(search, dx))
    dy = max(-search, min(search, dy))
    return int(dx), int(dy)


def _shift(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    if dx == 0 and dy == 0:
        return img
    if _HAS_CV2:
        m = np.float32([[1, 0, dx], [0, 1, dy]])
        return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]),
                              flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return np.roll(np.roll(img, dy, axis=0), dx, axis=1)


def _cleanup(mask: np.ndarray) -> np.ndarray:
    if not _HAS_CV2:
        return mask
    kernel = np.ones((3, 3), np.uint8)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    out = np.zeros_like(opened)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= MIN_BLOB_AREA:
            out[labels == i] = 255
    return out


class ReferenceDetector:
    """参考图比对检测器（线程安全：页面与闭环是不同线程）"""

    def __init__(self, defect_ratio_threshold: float = 0.25) -> None:
        """defect_ratio_threshold 单位是"差异像素占整幅图的百分比"。

        默认 0.25%：自测里"污点"占 0.69%、"划痕"占 0.33%，用 0.8% 会漏检；
        而噪声/JPEG 压缩/轻微平移经过模糊+阈值+形态学去噪后都是 0.000%，
        所以阈值可以压到 0.25% 而不误报。
        """
        self.ref_gray: np.ndarray | None = None
        self.ref_size: tuple[int, int] | None = None
        self.threshold = float(defect_ratio_threshold)

    # ---------------- 参考图 ----------------
    def has_reference(self) -> bool:
        return self.ref_gray is not None

    def set_reference(self, data: bytes) -> dict:
        gray = _to_gray(data)
        self.ref_gray = _blur(gray)
        self.ref_size = (gray.shape[1], gray.shape[0])
        return {"ok": True, "width": gray.shape[1], "height": gray.shape[0]}

    def clear_reference(self) -> dict:
        self.ref_gray = None
        self.ref_size = None
        return {"ok": True}

    # ---------------- 比对 ----------------
    def compare(self, data: bytes, threshold: float | None = None) -> dict:
        if self.ref_gray is None:
            raise ValueError("还没有设置参考图（先用一张合格品照片设为参考图）")
        thr = float(self.threshold if threshold is None else threshold)

        gray = _to_gray(data)
        ref = self.ref_gray
        if gray.shape != ref.shape:
            if _HAS_CV2:
                gray = cv2.resize(gray, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
            else:
                gray = np.asarray(Image.fromarray(gray).resize((ref.shape[1], ref.shape[0])))
        cur = _blur(gray)

        dx, dy = _best_shift(ref, cur)
        cur_aligned = _shift(cur, dx, dy)

        # 亮度归一化：相机自动曝光会让整幅图整体变亮/变暗，
        # 直接相减会"整幅图都算差异"→ 假报警。先扣掉中位差值再比。
        delta = float(np.median(ref.astype(np.int16) - cur_aligned.astype(np.int16)))
        if abs(delta) >= 1.0:
            cur_aligned = np.clip(cur_aligned.astype(np.int16) + int(round(delta)), 0, 255).astype(np.uint8)

        diff = np.abs(ref.astype(np.int16) - cur_aligned.astype(np.int16)).astype(np.uint8)
        mask = (diff > PIXEL_THRESHOLD).astype(np.uint8) * 255
        mask = _cleanup(mask)

        # 边缘一圈不参与判定：相机轻微移动时，画面边缘会有内容"移进/移出"，
        # 那部分差异不是产品缺陷（自测里"整体平移 6,4 像素"因此残留 1.4% 假差异）。
        m = EDGE_MARGIN
        h, w = mask.shape
        if h > 2 * m + 8 and w > 2 * m + 8:
            mask[:m, :] = 0
            mask[-m:, :] = 0
            mask[:, :m] = 0
            mask[:, -m:] = 0
            valid_total = (h - 2 * m) * (w - 2 * m)
        else:
            valid_total = mask.size

        total = valid_total
        defect_px = int(np.count_nonzero(mask))
        ratio = defect_px / total * 100.0
        similarity = 100.0 - float(np.mean(diff)) / 255.0 * 100.0
        anomaly = ratio > thr

        return {
            "ok": True,
            "anomaly": bool(anomaly),
            "verdict": "abnormal" if anomaly else "normal",
            "defect_ratio": round(ratio, 3),
            "threshold": thr,
            "similarity": round(similarity, 2),
            "defect_pixels": defect_px,
            "shift": [dx, dy],
            "message": ("发现差异 %.3f%%（阈值 %.2f%%）→ 判为异常" % (ratio, thr)) if anomaly
                       else ("差异 %.3f%% ≤ 阈值 %.2f%% → 判为正常" % (ratio, thr)),
            "diff_jpeg": self._diff_preview(cur_aligned, mask),
        }

    # ---------------- 差异预览图 ----------------
    def _diff_preview(self, cur: np.ndarray, mask: np.ndarray) -> bytes | None:
        try:
            if _HAS_CV2:
                base = cv2.cvtColor(cur, cv2.COLOR_GRAY2BGR)
                overlay = base.copy()
                overlay[mask > 0] = (0, 0, 255)          # 红=BGR 的 (0,0,255)
                out = cv2.addWeighted(base, 0.55, overlay, 0.45, 0)
                ok, enc = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                return enc.tobytes() if ok else None
            rgb = np.stack([cur] * 3, axis=-1).astype(np.uint8)
            rgb[mask > 0] = (255, 0, 0)
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, format="JPEG", quality=80)
            return buf.getvalue()
        except Exception:
            return None
