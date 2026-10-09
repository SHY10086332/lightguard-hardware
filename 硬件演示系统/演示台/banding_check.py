# -*- coding: utf-8 -*-
"""banding_check.py —— 相机画面"频闪条纹"复测（关闭验证记录里的待复测项）

背景：1 kHz 的 PWM 调光会在相机画面上留下固定横向条纹（三帧行均值标准差 8.85/8.78/8.80，
≈满量程 3.5%）。固件已把 PWM 提高到 8 kHz，但**当时没有按同样方法复测**。
这个脚本就是用来补那次复测的。

方法（与 1 kHz 那次一致，保证可比）：
  1. 串口设灯带亮度 → **等 2.5 秒**（这是个坑：改完立刻读会得到乱数）；
  2. ffmpeg 从 `USB Camera` 抓 5 帧取最后一帧（避开自动曝光收敛的第一帧）；
  3. 在画面里**自动挑一块最平坦的区域**（梯度最小），对这块区域逐行求灰度均值再求标准差 σ；
     —— 必须挑平坦区：如果 ROI 落在文字上，σ 反映的是"字的明暗"，不是条纹；
  4. σ 越小条纹越弱。判据：σ < 3 认为条纹已经很弱，σ > 6 认为仍明显。
  5. 同时检查画面是否过曝（均值 >250 或极差极小 = 一片死白），过曝时该档数据作废。

★★ 运行前务必确认相机对着的是**空白纸 / 标签**：
   镜头里绝不能出现证件、表格、聊天窗口等个人内容 —— 本工具会把这些帧存到磁盘。

用法:
    py -3.11 banding_check.py                 # 默认测 0% / 25% / 50%
    py -3.11 banding_check.py --levels 0,15,25,35,50
    py -3.11 banding_check.py --keep          # 保留抓帧（默认测完就删，避免留下个人画面）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime

import numpy as np
from PIL import Image

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("[x] 没装 pyserial：py -3.11 -m pip install pyserial")
    sys.exit(1)

BASELINE_1KHZ = 8.8          # 1 kHz 实测（同一 ROI 方法）
ROI_W, ROI_H = 80, 240       # 平坦区搜索用的窗口大小


def pick_port(explicit=None):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    for p in ports:
        if (p.vid, p.pid) in ((0x10C4, 0xEA60), (0x1A86, 0x7523)):
            return p.device
    return ports[0].device if ports else None


def cmd(ser, text, wait=1.2):
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


def grab(cam: str, out_dir: str, tag: str, frames: int = 5):
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "f_%s_%%02d.jpg" % tag)
    logf = os.path.join(out_dir, "ffmpeg.log")
    cmdline = ["ffmpeg", "-hide_banner", "-y", "-f", "dshow", "-i", cam,
               "-frames:v", str(frames), "-q:v", "2", pattern]
    with open(logf, "wb") as fh:
        subprocess.run(cmdline, stdout=fh, stderr=fh, timeout=90)
    files = sorted(f for f in os.listdir(out_dir) if f.startswith("f_%s_" % tag))
    return os.path.join(out_dir, files[-1]) if files else None


def flattest_roi(a: np.ndarray) -> tuple[int, int]:
    """找最平坦的 ROI 左上角：用积分图快速算每个窗口的梯度能量"""
    gx = np.abs(np.diff(a.astype(np.float32), axis=1))
    gy = np.abs(np.diff(a.astype(np.float32), axis=0))
    gy = np.vstack([gy, np.zeros((1, gy.shape[1]))])
    gx = np.hstack([gx, np.zeros((gx.shape[0], 1))])
    energy = gx + gy
    # 盒子滤波（用累积和实现）
    ii = energy.cumsum(0).cumsum(1)
    ii = np.pad(ii, ((1, 0), (1, 0)))
    H, W = a.shape
    bh, bw = ROI_H, ROI_W
    if H < bh or W < bw:
        return 0, 0
    sums = (ii[bh:, bw:] - ii[:-bh, bw:] - ii[bh:, :-bw] + ii[:-bh, :-bw])
    idx = int(np.argmin(sums))
    y, x = divmod(idx, sums.shape[1])
    return int(y), int(x)


def analyse(path: str) -> dict:
    im = Image.open(path).convert("L")
    a = np.asarray(im, dtype=np.float32)
    y, x = flattest_roi(a)
    roi = a[y:y + ROI_H, x:x + ROI_W]
    row_means = roi.mean(axis=1)
    col_means = roi.mean(axis=0)
    # 过曝比例：整幅里接近白（>=250）的像素占比。
    # 用"比例"而不是"均值"判过曝 —— 画面里有一块标签是黑的时，均值会被拉低，看不出过曝。
    sat = float((a >= 250).mean())
    return {
        "file": os.path.basename(path),
        "frame": "%dx%d" % (im.width, im.height),
        "roi": "x=%d,y=%d %dx%d" % (x, y, ROI_W, ROI_H),
        "overall_mean": round(float(a.mean()), 1),
        "roi_mean": round(float(roi.mean()), 1),
        "sat_ratio": round(sat, 4),
        "row_sigma": round(float(row_means.std()), 2),
        "col_sigma": round(float(col_means.std()), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--cam", default="video=USB Camera")
    ap.add_argument("--levels", default="0,25,50")
    ap.add_argument("--settle", type=float, default=2.5,
                    help="每档升亮后等多久再抓帧（默认 2.5 秒）；相机自动曝光收敛慢时要调大")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "banding_out"))
    ap.add_argument("--keep", action="store_true", help="保留抓帧（默认测完删除）")
    args = ap.parse_args()
    levels = [int(x) for x in args.levels.split(",") if x.strip()]

    print("=" * 78)
    print("  相机频闪条纹复测（PWM 频率 与 卷帘快门）")
    print("=" * 78)
    print("  ⚠ 运行前请确认：相机对着的是**空白纸或标签**，画面里没有证件/表格等个人内容。")
    print("  对照基线：1 kHz 时代 σ ≈ %.1f（同一 ROI 方法）" % BASELINE_1KHZ)
    print("-" * 78)

    dev = pick_port(args.port)
    if not dev:
        print("[x] 没找到串口")
        return 1
    ser = serial.Serial(dev, 115200, timeout=0.6)
    time.sleep(0.4)
    ser.reset_input_buffer()
    print("  串口: %s (%s)   相机: %s" % (dev, (cmd(ser, "PING", 1.5) or {}).get("device", "?"), args.cam))
    print("-" * 78)
    print(" %5s | %8s | %8s | %7s | %8s | %s" % ("亮度%", "整幅均值", "ROI 均值", "过曝%", "行σ", "判定"))
    print("-" * 82)

    rows = []
    try:
        for lvl in levels:
            cmd(ser, "LIGHT %d" % lvl, 1.2)
            time.sleep(args.settle)               # 测量纪律：等灯稳 + 等相机自动曝光收敛
            path = grab(args.cam, args.out, "b%02d" % lvl)
            if not path:
                print(" %5d | 抓帧失败（相机被占用？看 %s）" % (lvl, args.out))
                continue
            r = analyse(path)
            r["level"] = lvl
            rows.append(r)
            if r["sat_ratio"] > 0.35:
                verdict = "画面过曝，数据不可用 ⚠"
            elif lvl == 0:
                verdict = "灯灭（基线）"
            elif r["row_sigma"] < 3:
                verdict = "条纹很弱 ✅"
            elif r["row_sigma"] < 6:
                verdict = "轻微"
            else:
                verdict = "明显 ⚠"
            print(" %5d | %8.1f | %9.1f | %6.1f%% | %8.2f | %s"
                  % (lvl, r["overall_mean"], r["roi_mean"], r["sat_ratio"] * 100, r["row_sigma"], verdict))
        print("-" * 82)
        good = [r for r in rows if r["level"] > 0 and r["sat_ratio"] <= 0.35]
        if good:
            avg_row = statistics.mean(r["row_sigma"] for r in good)
            print("  亮灯且不过曝的档位平均 行σ = %.2f   1 kHz 基线 = %.1f" % (avg_row, BASELINE_1KHZ))
            if avg_row < 3:
                print("  结论①：**8 kHz 下条纹已基本消失**（σ %.2f，1 kHz 时 8.8）→ 可关闭 §2.13 待复测项"
                      % avg_row)
            elif avg_row < BASELINE_1KHZ * 0.7:
                print("  结论①：明显改善（σ %.1f → %.2f），但仍有残留" % (BASELINE_1KHZ, avg_row))
            else:
                print("  结论①：改善不明显 → 检查是否真是 8 kHz 固件、以及曝光是否过短")
            lo = min(r["level"] for r in good)
            hi = max(r["level"] for r in good)
            print("  结论②（对采集数据集同样有用）：**相机不过曝的亮度不一定从 %d%% 到 %d%% 都行** ——"
                  % (lo, hi))
            print("           实测可用档位：%s" % ", ".join("%d%%" % r["level"] for r in good))
        else:
            print("  [!] 没有可用的亮灯档数据（都过曝或抓帧失败）——把亮度调低些再测")
        rep = os.path.join(args.out, "banding_report_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        with open(rep, "w", encoding="utf-8") as fh:
            json.dump({"baseline_1khz": BASELINE_1KHZ, "rows": rows}, fh, ensure_ascii=False, indent=2)
        print("  明细: %s" % rep)
    finally:
        off = cmd(ser, "LIGHT 0", 1.5)
        print("-" * 78)
        print("  已关灯（硬件回读 亮度 = %s%%）" % (off or {}).get("brightness") if isinstance(off, dict) else "  已关灯")
        ser.close()
        if not args.keep:
            for f in os.listdir(args.out) if os.path.isdir(args.out) else []:
                if f.startswith("f_"):
                    os.remove(os.path.join(args.out, f))
            print("  抓帧已删除（默认不留画面；要保留加 --keep）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
