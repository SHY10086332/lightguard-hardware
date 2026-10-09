# -*- coding: utf-8 -*-
"""calibrate_lux.py —— 照度标定工具（换几何/换灯带/验收前必跑）

为什么要做这个：本机实测 lx/每1% 亮度**强依赖传感器与灯带的相对位置**，
记录里出现过 8.65 / 9.77 / 28.5 / 39 四种斜率。所以"900 lx 需要多少亮度"这类结论
**必须按当前实际摆放重新标定**，不能沿用别处的数字。

它做什么：
  1. 逐档升亮（默认为一组覆盖高低亮度的档位），每档 **等 2.5 秒**再连读 3 次取中位
     （改亮度后立刻读会得到乱数：实测 15% 读到 12.5 lx、20% 读到 163.3 lx）；
  2. 线性拟合出 照度 ≈ k × 亮度% + b，给出 k（lx/每1%）；
  3. 算出"达到目标照度需要多少亮度"，并与**相机不过曝区间 20%~35%** 对照，直接给结论；
  4. 导出一份 Markdown 报告（可直接贴进验证记录/材料）；
  5. **无论成功失败都发 LIGHT 0 关灯**。

用法:
    py -3.11 calibrate_lux.py                       # 默认目标 900 lx
    py -3.11 calibrate_lux.py --target 300
    py -3.11 calibrate_lux.py --port COM5 --out 标定报告.md
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("[x] 没装 pyserial：py -3.11 -m pip install pyserial")
    sys.exit(1)

STEPS = [0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 75, 90, 100]
SETTLE = 2.5
READS = 3
SAFE_LO, SAFE_HI = 20, 35          # 相机不过曝的亮度区间（实测结论）


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


def read_lux(ser):
    vals = []
    for _ in range(READS):
        st = cmd(ser, "STATUS", 1.0)
        if st and st.get("ok"):
            vals.append(float(st.get("lux") or 0.0))
        time.sleep(0.2)
    return statistics.median(vals) if vals else None


def fit(rows, lo=15, hi=100):
    pts = [(n, lx) for n, lx in rows if lx is not None and lo <= n <= hi]
    if len(pts) < 2:
        return None, None, 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs) or 1e-9
    k = sum((x - mx) * (y - my) for x, y in pts) / denom
    b = my - k * mx
    # 拟合优度 R²
    ss_tot = sum((y - my) ** 2 for y in ys) or 1e-9
    ss_res = sum((y - (k * x + b)) ** 2 for x, y in pts)
    return k, b, max(0.0, 1 - ss_res / ss_tot)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--target", type=float, default=900.0)
    ap.add_argument("--tolerance", type=float, default=30.0)
    ap.add_argument("--out", default=None, help="Markdown 报告路径（默认与脚本同目录）")
    args = ap.parse_args()

    dev = pick_port(args.port)
    if not dev:
        print("[x] 没找到串口")
        return 1
    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "标定报告_%s.md" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    try:
        ser = serial.Serial(dev, args.baud, timeout=0.6)
    except Exception as exc:
        print("[x] 打不开 %s：%s（串口是否被演示台/采集台占用？）" % (dev, exc))
        return 1
    time.sleep(0.4)
    ser.reset_input_buffer()
    print("=" * 66)
    print("  照度标定工具")
    print("=" * 66)
    print("  串口: %s    设备: %s" % (dev, (cmd(ser, "PING", 1.5) or {}).get("device", "未知")))
    print("  目标: %.0f ± %.0f lx" % (args.target, args.tolerance))
    print("  纪律: 每档等 %.1f 秒、连读 %d 次取中位" % (SETTLE, READS))
    print("-" * 66)
    print(" %6s | %11s | %s" % ("亮度%", "照度 lx", "备注"))
    print("-" * 66)

    rows = []
    try:
        for n in STEPS:
            cmd(ser, "LIGHT %d" % n, 1.2)
            time.sleep(SETTLE)
            lux = read_lux(ser)
            rows.append((n, lux))
            note = "环境光（灯带灭）" if n == 0 else ""
            if lux is not None and n > 0 and lux >= args.target:
                note = "已达目标"
            print(" %6d | %11s | %s" % (n, ("%.1f" % lux) if lux is not None else "--", note))

        k, b, r2 = fit(rows)
        print("-" * 66)
        lines = []
        if k:
            need = (args.target - b) / k
            safe = [lx for n, lx in rows if lx is not None and SAFE_LO <= n <= SAFE_HI]
            print(" 拟合: 照度 ≈ %.2f × 亮度%% + %.1f    (R² = %.4f)" % (k, b, r2))
            print(" 斜率: %.2f lx / 每 1%% 亮度" % k)
            print(" 满亮(100%%)预计: %.0f lx" % (k * 100 + b))
            print(" 达到 %.0f lx 需要: %.1f%% 亮度" % (args.target, need))
            if safe:
                print(" 相机不过曝区间 %d%%~%d%% 对应: %.0f ~ %.0f lx"
                      % (SAFE_LO, SAFE_HI, min(safe), max(safe)))
            verdict = ""
            if need > SAFE_HI:
                verdict = ("⚠ **冲突**：达到 %.0f lx 需要 %.1f%% 亮度，超出相机不过曝区间（%d%%~%d%%）。"
                           "要么把 BH1750 移近灯带/移到箱内最终位置后重标，要么把验收点改到安全区间内"
                           "（如 %.0f lx）。" % (args.target, need, SAFE_LO, SAFE_HI,
                                              round(k * 30 + b, -1)))
            else:
                verdict = ("✅ 目标落在相机不过曝区间内（需要 %.1f%% 亮度），可直接作为验收/演示口径。"
                           % need)
            print(" 结论: " + verdict)

            lines += [
                "# 照度标定报告",
                "",
                "- 时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "- 串口：%s    目标：%.0f ± %.0f lx" % (dev, args.target, args.tolerance),
                "- 测量纪律：每档等 %.1f 秒、连读 %d 次取中位" % (SETTLE, READS),
                "",
                "| 亮度% | 照度 lx |",
                "|---|---|",
            ]
            lines += ["| %d | %s |" % (n, "--" if lx is None else "%.1f" % lx) for n, lx in rows]
            lines += [
                "",
                "## 结果",
                "",
                "- 拟合：照度 ≈ **%.2f** × 亮度%% + %.1f（R² = %.4f）" % (k, b, r2),
                "- 斜率：**%.2f lx / 每 1%% 亮度**" % k,
                "- 满亮预计：%.0f lx" % (k * 100 + b),
                "- 达到 %.0f lx 需要 **%.1f%%** 亮度" % (args.target, need),
            ]
            if safe:
                lines.append("- 相机不过曝区间 %d%%~%d%% 对应 %.0f ~ %.0f lx"
                             % (SAFE_LO, SAFE_HI, min(safe), max(safe)))
            lines += ["", "## 结论", "", verdict, "",
                      "> 注意：lx/每1% 与传感器摆放位置强相关（历史实测出现过 8.65 / 9.77 / 28.5 / 39），",
                      "> 换位置后必须重新标定，不能沿用旧数字。", ""]
        else:
            print(" [!] 有效数据不足，无法拟合")
            lines = ["# 照度标定报告", "", "有效数据不足，无法拟合。", ""]
    finally:
        cmd(ser, "LIGHT 0", 1.5)
        after = cmd(ser, "STATUS", 1.0) or {}
        print("=" * 66)
        print("已关灯（硬件回读 亮度 = %s%%）" % after.get("brightness"))
        ser.close()

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        print("报告已保存: %s" % out_path)
    except Exception as exc:
        print("[!] 报告保存失败：%s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
