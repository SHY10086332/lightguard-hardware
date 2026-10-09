# -*- coding: utf-8 -*-
"""check_framing_and_fp.py —— 用真实相机帧做两件事：

A. 取景体检：标签在画面里的位置、大小、清晰度、过曝情况
B. 误报检查：把同一张标签的**两帧真实相机照片**互相比对
   （一帧当参考图、另一帧当待检）——理论上应该判"正常"。
   如果这里判异常，说明"参考图比对"在真实相机噪声/自动曝光漂移下会误报，
   阈值必须调，否则采集/演示时会一路红灯。

方法要点：
  · 先设亮度 → **等 7 秒**（照度稳了但相机自动曝光还要收敛，实测必需）
  · ffmpeg 抓 5 帧取最后一帧
  · 抓到的帧只在内存/临时目录处理，脚本结束即删（不留画面）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import numpy as np
import serial
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detect_ref import ReferenceDetector  # noqa: E402

PORT, CAM = "COM5", "video=USB Camera"
TMP = os.path.join(os.environ.get("TEMP", "."), "lg_framing_check")
LEVEL = 25
SETTLE = 7.0


def cmd(ser, text, wait=1.5):
    ser.reset_input_buffer()
    ser.write((text + "\n").encode())
    ser.flush()
    end = time.time() + wait
    while time.time() < end:
        raw = ser.readline()
        if raw and raw.startswith(b"{"):
            try:
                return json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
    return None


def grab(tag: str) -> str | None:
    os.makedirs(TMP, exist_ok=True)
    pattern = os.path.join(TMP, "f_%s_%%02d.jpg" % tag)
    logf = os.path.join(TMP, "ffmpeg.log")
    with open(logf, "wb") as fh:
        subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "dshow", "-i", CAM,
                        "-frames:v", "5", "-q:v", "2", pattern],
                       stdout=fh, stderr=fh, timeout=90)
    files = sorted(f for f in os.listdir(TMP) if f.startswith("f_%s_" % tag))
    return os.path.join(TMP, files[-1]) if files else None


def analyse(path: str) -> dict:
    im = Image.open(path)
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = float(np.var(np.gradient(np.gradient(g, axis=0), axis=0)))  # 粗略清晰度
    return {
        "size": "%dx%d" % (im.width, im.height),
        "mean": round(float(g.mean()), 1),
        "sat_ratio": round(float((g >= 250).mean()), 4),
        "dark_ratio": round(float((g <= 40).mean()), 4),
        "sharpness": round(lap, 1),
    }


def framing(path: str) -> dict:
    """取景体检：找"像标签"的矩形（长宽比 1.2~1.9、面积 1%~40%、不贴边），
    并顺带算左右/上下的光照均匀性。

    为什么不能只取"最大暗块"：实测第一次实现就是那样，结果把画面左侧的背景暗区
    当成了标签（报"贴边、只占 12.8%"），完全是错的。标签是 60×40mm → 长宽比 1.5，
    这个先验比"面积最大"可靠得多。
    """
    try:
        import cv2
    except ImportError:
        return {"found": False, "error": "没有 opencv"}
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"found": False, "error": "读不到图"}
    H, W = img.shape
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cnts, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    labels, clipped = [], []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 80 or h < 60:
            continue
        ar = w / h
        if not (1.2 <= ar <= 1.9):        # 60/40 = 1.5
            continue
        ratio = w * h / (W * H) * 100
        if not (0.5 <= ratio <= 40):
            continue
        touch = bool(x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2)
        item = {"bbox": "x=%d y=%d w=%d h=%d" % (x, y, w, h), "area_pct": round(ratio, 1),
                "aspect": round(ar, 2)}
        (clipped if touch else labels).append(item)

    # 光照均匀性（四象限 + 左右）
    h2, w2 = H // 2, W // 2
    quad = {
        "左上": float(img[:h2, :w2].mean()), "右上": float(img[:h2, w2:].mean()),
        "左下": float(img[h2:, :w2].mean()), "右下": float(img[h2:, w2:].mean()),
    }
    left, right = float(img[:, :w2].mean()), float(img[:, w2:].mean())
    return {
        "found": bool(labels or clipped),
        "complete": len(labels),
        "clipped": len(clipped),
        "biggest": max(labels, key=lambda d: d["area_pct"]) if labels else None,
        "quad": {k: round(v, 1) for k, v in quad.items()},
        "lr_diff": round(right - left, 1),
        "uneven": abs(right - left) > 25,
    }


def main() -> int:
    ser = serial.Serial(PORT, 115200, timeout=0.8)
    time.sleep(0.4)
    ser.reset_input_buffer()
    print("=" * 70)
    print("  取景体检 + 真实帧误报检查（亮度 %d%%，等 %.0f 秒让相机自动曝光收敛）" % (LEVEL, SETTLE))
    print("=" * 70)
    try:
        st = cmd(ser, "LIGHT %d" % LEVEL, 2.0)
        print("  灯带: 回读亮度 %s%%，照度等待中…" % (st or {}).get("brightness"))
        time.sleep(SETTLE)
        cur = cmd(ser, "STATUS")
        print("  稳定后照度: %s lx" % (cur or {}).get("lux"))

        a = grab("A")
        if not a:
            print("  [x] 抓帧失败（相机被占用？）")
            return 1
        time.sleep(3.0)                      # 让自动曝光再稳一会儿
        b = grab("B")
        if not b:
            print("  [x] 第二次抓帧失败")
            return 1

        ia, ib = analyse(a), analyse(b)
        print("-" * 70)
        print("  A 帧：%s  均值 %s  过曝 %.1f%%  清晰度 %s" % (ia["size"], ia["mean"], ia["sat_ratio"] * 100, ia["sharpness"]))
        print("  B 帧：%s  均值 %s  过曝 %.1f%%  清晰度 %s" % (ib["size"], ib["mean"], ib["sat_ratio"] * 100, ib["sharpness"]))
        print("-" * 70)
        fr = framing(a)
        print("  【取景体检】")
        if fr.get("found"):
            print("    画面里可见标签: 完整 %d 个 + 贴边被裁 %d 个" % (fr["complete"], fr["clipped"]))
            if fr.get("biggest"):
                best = fr["biggest"]
                print("    最大完整标签: %s  占画面 %.1f%%  长宽比 %.2f（60×40mm 应为 1.50）"
                      % (best["bbox"], best["area_pct"], best["aspect"]))
                if best["area_pct"] < 25:
                    print("      → 一张标签只占画面 %.1f%%：若想「一图一标签」，应把相机拉近/换镜头，"
                          "让标签占到 40%%~70%%；若想「一图多样品」则当前取景可用" % best["area_pct"])
            print("    四象限均值: " + "  ".join("%s %.1f" % (k, v) for k, v in fr["quad"].items()))
            print("    左右差 %.1f 灰度 → %s" % (fr["lr_diff"],
                  "★ 光照不均匀（>25 灰度）：左右亮度差会让「把标签放到别的位置」被误判成缺陷"
                  if fr["uneven"] else "均匀 ✅"))
        else:
            print("    没找到像标签的矩形：%s" % fr.get("error", "画面里可能没有完整标签"))

        print("-" * 70)
        print("  【误报检查】同一张标签的两帧真实相机照片互相比对（应当判「正常」）")
        det = ReferenceDetector(0.25)
        det.set_reference(open(a, "rb").read())
        r = det.compare(open(b, "rb").read())
        print("    判定: %s   差异占比 %.3f%%（阈值 %.2f%%）  相似度 %.2f%%  对齐偏移 %s"
              % ("异常 ✗（误报！）" if r["anomaly"] else "正常 ✅", r["defect_ratio"], r["threshold"],
                 r["similarity"], r["shift"]))
        if r["anomaly"]:
            print("    → 说明真实相机噪声/自动曝光漂移会让阈值 0.25%% 误报，采集与演示前必须调阈值")
            for t in (0.5, 1.0, 2.0, 4.0):
                rr = det.compare(open(b, "rb").read(), threshold=t)
                print("       阈值 %.2f%% → %s（差异 %.3f%%）" % (t, "正常 ✅" if not rr["anomaly"] else "仍异常", rr["defect_ratio"]))
        else:
            print("    → 真实相机帧下阈值 0.25%% 不误报 ✅（可直接用于采集与演示）")
        print("=" * 70)
    finally:
        off = cmd(ser, "LIGHT 0", 1.5)
        print("  已关灯（回读亮度 %s%%）" % (off or {}).get("brightness"))
        ser.close()
        for f in os.listdir(TMP) if os.path.isdir(TMP) else []:
            if f.startswith("f_"):
                os.remove(os.path.join(TMP, f))
        print("  抓帧已删除")
    return 0


if __name__ == "__main__":
    sys.exit(main())
