# -*- coding: utf-8 -*-
"""mark_columns.py —— 标出画面里有哪几列标签、每列亮度多少，并生成标注图。

用途：让"只用中间两列"这句话变得没有歧义 —— 直接在图上编号 + 标亮度。
生成：桌面\\标签列_标注.png
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import numpy as np
import serial
from PIL import Image, ImageDraw, ImageFont

PORT, CAM = "COM5", "video=USB Camera"
TMP = os.path.join(os.environ.get("TEMP", "."), "lg_cols")
DESK = os.path.join(os.path.expanduser("~"), "Desktop")


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


def shoot_settled(ser, tries: int = 6, tol: float = 4.0):
    prev, last = None, None
    for i in range(1, tries + 1):
        os.makedirs(TMP, exist_ok=True)
        pattern = os.path.join(TMP, "c_%02d.jpg")
        with open(os.path.join(TMP, "ff.log"), "wb") as fh:
            subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "dshow", "-i", CAM,
                            "-frames:v", "5", "-q:v", "2", pattern],
                           stdout=fh, stderr=fh, timeout=90)
        fs = sorted(f for f in os.listdir(TMP) if f.startswith("c_"))
        if not fs:
            print("  抓帧失败（相机被占用？）")
            return None
        last = os.path.join(TMP, fs[-1])
        m = float(np.asarray(Image.open(last).convert("L"), dtype=np.float32).mean())
        print("     第 %d 帧均值 %.1f%s" % (i, m, "" if prev is None or abs(m - prev) > tol else "  ← 已收敛"))
        if prev is not None and abs(m - prev) <= tol:
            return last
        prev = m
        time.sleep(2.5)
    return last


def main() -> int:
    import cv2
    ser = serial.Serial(PORT, 115200, timeout=0.8)
    time.sleep(0.4)
    ser.reset_input_buffer()
    print("=" * 70)
    print("  标出画面里有哪几列标签（亮度 25%）")
    print("=" * 70)
    try:
        cmd(ser, "LIGHT 25", 2.0)
        path = shoot_settled(ser)
        if not path:
            return 1
        lux = (cmd(ser, "STATUS") or {}).get("lux")
        print("  照度 %s lx" % lux)

        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        H, W = img.shape
        blur = cv2.GaussianBlur(img, (5, 5), 0)
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        cnts, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w < 120 or h < 80:
                continue
            ar = w / h
            if not (1.2 <= ar <= 1.9):
                continue
            if not (0.5 <= w * h / (W * H) * 100 <= 40):
                continue
            if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:
                continue                       # 贴边的不要（不完整）
            boxes.append((x, y, w, h))
        # 去掉重复（同一标签被多个轮廓框住）
        boxes.sort(key=lambda b: (b[0], b[1]))
        uniq = []
        for b in boxes:
            if all(abs(b[0] - u[0]) > 40 or abs(b[1] - u[1]) > 40 for u in uniq):
                uniq.append(b)
        print("  找到完整标签 %d 个" % len(uniq))
        if not uniq:
            print("  [x] 没找到完整标签")
            return 1

        # 按 x 聚成列
        uniq.sort(key=lambda b: b[0] + b[2] / 2)
        cols, cur = [], [uniq[0]]
        for b in uniq[1:]:
            if abs((b[0] + b[2] / 2) - (cur[-1][0] + cur[-1][2] / 2)) < 200:
                cur.append(b)
            else:
                cols.append(cur)
                cur = [b]
        cols.append(cur)

        im = Image.open(path).convert("RGB")
        d = ImageDraw.Draw(im)
        f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 44)
        f2 = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 36)
        print()
        print("  %-6s %-8s %-10s %s" % ("列", "标签数", "平均亮度", "横向范围"))
        for i, col in enumerate(cols, 1):
            x0 = min(b[0] for b in col)
            x1 = max(b[0] + b[2] for b in col)
            vals = [float(img[b[1]:b[1] + b[3], b[0]:b[0] + b[2]].mean()) for b in col]
            avg = sum(vals) / len(vals)
            print("  第%d列   %-8d %-10.1f x=%d..%d" % (i, len(col), avg, x0, x1))
            # 画框 + 列号
            color = (0, 200, 0) if i in (2, 3) else (255, 120, 0)
            for b in col:
                d.rectangle([b[0], b[1], b[0] + b[2], b[1] + b[3]], outline=color, width=6)
            d.rectangle([x0, 0, x1, 60], fill=(0, 0, 0))
            d.text((x0 + 10, 6), "第%d列 亮度%.0f" % (i, avg), font=f2, fill=(255, 255, 0))
        out = os.path.join(DESK, "标签列_标注.png")
        im.save(out)
        print()
        print("  标注图已存: %s" % out)
    finally:
        off = cmd(ser, "LIGHT 0", 1.5) or {}
        print("  已关灯（回读亮度 %s%%）" % off.get("brightness"))
        ser.close()
        for f3 in os.listdir(TMP) if os.path.isdir(TMP) else []:
            try:
                os.remove(os.path.join(TMP, f3))
            except Exception:
                pass
        print("  原始抓帧已删除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
