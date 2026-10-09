# -*- coding: utf-8 -*-
"""diag_light_source.py —— 判定"左暗右亮"到底是灯带造成的，还是房间光造成的

做法（决定性对比）：
  1. 灯带 25% 抓一帧 → 用这一帧的亮像素确定"纸面"的像素范围（掩膜）；
  2. 灯带 0%（只房间光）抓一帧；
  3. 用**同一套掩膜**分别算两帧里"纸面左半/右半"的平均亮度：
       · 关灯时左右差就很大  → 是**房间光**（窗户/台灯）造成，挪灯带没用；
       · 开灯带来的增量右多左少 → **灯带偏右**，往左挪有效；
       · 两边增量差不多        → 灯带位置基本正，差值是房间光贡献的。

两帧都在"相机自动曝光收敛"后才抓（连续两帧均值一致）。
用法: py -3.11 diag_light_source.py
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

PORT, CAM = "COM5", "video=USB Camera"
TMP = os.path.join(os.environ.get("TEMP", "."), "lg_diag")


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


def shoot(tag: str):
    os.makedirs(TMP, exist_ok=True)
    pattern = os.path.join(TMP, "s_%s_%%02d.jpg" % tag)
    logf = os.path.join(TMP, "ffmpeg.log")
    with open(logf, "wb") as fh:
        rc = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "dshow", "-i", CAM,
                             "-frames:v", "5", "-q:v", "2", pattern],
                            stdout=fh, stderr=fh, timeout=90).returncode
    files = sorted(f for f in os.listdir(TMP) if f.startswith("s_%s_" % tag))
    if not files:
        print("      [!] 抓帧失败（ffmpeg rc=%d）；若相机被占用，关掉 Windows 相机/微信小程序的相机预览" % rc)
        return None
    p = os.path.join(TMP, files[-1])
    g = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
    print("      %s 帧: 均值 %.1f" % (tag, g.mean()))
    return p


def shoot_settled(tag: str, tries: int = 5, tol: float = 4.0):
    prev = None
    for i in range(1, tries + 1):
        p = shoot("%s%d" % (tag, i))
        if not p:
            return None
        g = np.asarray(Image.open(p).convert("L"), dtype=np.float32).mean()
        if prev is not None and abs(g - prev) <= tol:
            return p
        prev = g
        time.sleep(2.5)
    return p


def main() -> int:
    ser = serial.Serial(PORT, 115200, timeout=0.8)
    time.sleep(0.4)
    ser.reset_input_buffer()
    print("=" * 72)
    print("  决定性测试：左暗右亮是「灯带」还是「房间光」造成的？")
    print("=" * 72)
    try:
        # ---- 第一步：开着灯抓帧，用它确定纸面掩膜 ----
        print("  ① 灯带 25%，等自动曝光收敛…")
        cmd(ser, "LIGHT 25", 2.0)
        p_on = shoot_settled("on")
        if not p_on:
            return 1
        lux_on = (cmd(ser, "STATUS") or {}).get("lux")
        g_on = np.asarray(Image.open(p_on).convert("L"), dtype=np.float32)
        thr = float(np.percentile(g_on, 55))
        mask = g_on > max(120.0, thr)                 # 纸面（亮区）
        if mask.mean() < 0.05:
            print("      [!] 纸面只占 %.1f%%，掩膜不可靠" % (mask.mean() * 100))
            return 1
        cols = mask.sum(axis=0)
        xs = np.where(cols > 20)[0]
        x0, x1 = int(xs[0]), int(xs[-1])
        mid = x0 + (x1 - x0) // 2
        left_mask = mask.copy(); left_mask[:, mid:] = False
        right_mask = mask.copy(); right_mask[:, :mid] = False
        print("      纸面占画面 %.1f%%，横向 x=%d..%d" % (mask.mean() * 100, x0, x1))

        # ---- 第二步：关灯抓帧 ----
        print("  ② 灯带 0%（只剩房间光），等自动曝光收敛…")
        cmd(ser, "LIGHT 0", 2.0)
        p_off = shoot_settled("off")
        if not p_off:
            return 1
        g_off = np.asarray(Image.open(p_off).convert("L"), dtype=np.float32)

        # ---- 第三步：同一掩膜下左右对比 ----
        def lr(g, m):
            lm, rm = m.copy(), m.copy()
            lm[:, mid:] = False
            rm[:, :mid] = False
            return float(g[lm].mean()), float(g[rm].mean())

        on_l, on_r = lr(g_on, mask)
        off_l, off_r = lr(g_off, mask)
        d_on, d_off = on_r - on_l, off_r - off_l
        add_l, add_r = on_l - off_l, on_r - off_r

        print("-" * 72)
        print("  %-22s %10s %10s %10s" % ("", "纸面左", "纸面右", "左右差"))
        print("  %-22s %10.1f %10.1f %10.1f" % ("灯带 0%%（只房间光）", off_l, off_r, d_off))
        print("  %-22s %10.1f %10.1f %10.1f" % ("灯带 25%%（+灯带）", on_l, on_r, d_on))
        print("  %-22s %10.1f %10.1f %10.1f" % ("灯带贡献的增量", add_l, add_r, add_r - add_l))
        print("-" * 72)
        print("  照度: 25%% → %s lx" % lux_on)
        print()
        print("  【结论】")
        if abs(d_off) > 25:
            print("    ★ 关着灯带时左右差就已经 %.1f 灰阶 → **主要是房间光造成的**" % d_off)
            print("      挪灯带基本没用；要匀就得挡房间光（拉窗帘/关台灯/把纸箱盖盖上），")
            print("      或者干脆接受它 —— 只要「整张纸固定不动、只换纸」，判定就不会误报。")
        elif abs(add_r - add_l) > 25:
            print("    ★ 房间光贡献 %.1f，灯带贡献的左右差 %.1f → **灯带确实偏右**" % (d_off, add_r - add_l))
            print("      把灯带往左挪到纸的中线正上方，或抬高 15~25 cm。")
        else:
            print("    ★ 房间光贡献 %.1f，灯带贡献 %.1f → 两者都不算大" % (d_off, add_r - add_l))
            print("      当前纸面左右差 %.1f 主要来自叠加；要改善就同时处理：挡房间光 + 灯带居中/抬高。" % d_on)
    finally:
        off = cmd(ser, "LIGHT 0", 1.5) or {}
        print("=" * 72)
        print("  已关灯（回读亮度 %s%%）" % off.get("brightness"))
        ser.close()
        for f in os.listdir(TMP) if os.path.isdir(TMP) else []:
            try:
                os.remove(os.path.join(TMP, f))
            except Exception:
                pass
        print("  抓帧已删除")
    return 0


if __name__ == "__main__":
    sys.exit(main())
