# -*- coding: utf-8 -*-
"""cli_capture.py —— 命令行版数据集采集（**不需要浏览器**）

为什么要有它：实测这台 USB 摄像头在浏览器里（Edge/Chrome）预览一直是黑屏，
但用 ffmpeg 抓帧完全正常。所以把采集改成"服务端抓帧"，绕开浏览器的摄像头权限与
MJPG 兼容问题。逻辑与网页采集台完全一致：
    设亮度 → 等相机自动曝光收敛 → ffmpeg 抓帧 → 找完整标签 → 逐个裁成单张 → 写记录表

用法（在采集台目录下）：
    py -3.11 cli_capture.py --class normal --count 40
    py -3.11 cli_capture.py --class stain --count 20 --columns 2,3
    py -3.11 cli_capture.py --class normal --count 10 --auto 6     # 自动：每 6 秒一张（不按回车）

流程（手动模式）：每放好一张纸 → 按【回车】→ 它抓帧、裁切、记账、打印结果 → 换下一张
结束：输入 q 回车；或采满 --count；无论如何都会关灯。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

import serial
from serial.tools import list_ports

HERE = os.path.dirname(os.path.abspath(__file__))
for _c in (HERE, os.path.join(HERE, "..", "console")):
    if os.path.isfile(os.path.join(_c, "dataset_manager.py")):
        sys.path.insert(0, os.path.abspath(_c))
        break
else:
    print("[x] 找不到 dataset_manager.py / label_crop.py")
    sys.exit(1)

from dataset_manager import CLASSES, DatasetManager      # noqa: E402
from label_crop import crop_labels                       # noqa: E402

CAM = "video=USB Camera"
SETTLE = 12.0          # 改亮度后等相机自动曝光收敛（实测需要十几秒）


def pick_port(explicit=None):
    if explicit:
        return explicit
    for p in list_ports.comports():
        if (p.vid, p.pid) in ((0x10C4, 0xEA60), (0x1A86, 0x7523)):
            return p.device
    ports = list(list_ports.comports())
    return ports[0].device if ports else None


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


def grab(tag: str, tmp: str):
    os.makedirs(tmp, exist_ok=True)
    pattern = os.path.join(tmp, "cap_%s_%%02d.jpg" % tag)
    logf = os.path.join(tmp, "ffmpeg.log")
    with open(logf, "wb") as fh:
        subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "dshow", "-i", CAM,
                        "-frames:v", "5", "-q:v", "2", pattern],
                       stdout=fh, stderr=fh, timeout=90)
    fs = sorted(f for f in os.listdir(tmp) if f.startswith("cap_%s_" % tag))
    return os.path.join(tmp, fs[-1]) if fs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--class", dest="cls", required=True, choices=[k for k, _ in CLASSES])
    ap.add_argument("--count", type=int, default=20, help="目标张数（按裁出来的标签数算）")
    ap.add_argument("--columns", default="", help="只保留列，如 2,3（留空=全部）")
    ap.add_argument("--brightness", type=int, default=25)
    ap.add_argument("--out", default=os.path.join(HERE, "dataset"))
    ap.add_argument("--port", default=None)
    ap.add_argument("--auto", type=float, default=0, help="自动模式：每 N 秒抓一张（不按回车）")
    args = ap.parse_args()

    cols = [int(x) for x in args.columns.replace("，", ",").split(",") if x.strip()] or None
    DM = DatasetManager(os.path.abspath(args.out))
    tmp = os.path.join(HERE, "_tmp_capture")

    dev = pick_port(args.port)
    if not dev:
        print("[x] 没找到串口：ESP32 插好了吗？串口被别的程序占了吗？")
        return 1
    try:
        ser = serial.Serial(dev, 115200, timeout=1.0)
    except Exception as exc:
        print("[x] 打不开 %s：%s" % (dev, exc))
        print("    串口可能被演示台/网页采集台/软件占用 —— 先关掉它们")
        return 1
    time.sleep(0.4)
    ser.reset_input_buffer()

    label = [v for k, v in CLASSES if k == args.cls][0]
    print("=" * 74)
    print("  命令行采集（不依赖浏览器）")
    print("=" * 74)
    print("  类别      : %s（%s）" % (args.cls, label))
    print("  目标张数  : %d 张（按裁出来的标签数计）" % args.count)
    print("  只保留列  : %s" % (",".join(map(str, cols)) if cols else "全部"))
    print("  亮度      : %d%%" % args.brightness)
    print("  输出目录  : %s" % DM.root)
    print("  当前已有  : %d 张" % DM.count(args.cls))
    print("-" * 74)
    print("  【操作】放好一张标签纸 → 按【回车】采一张；输入 q 回车结束")
    if args.auto:
        print("  【自动模式】每 %.1f 秒自动采一张，换纸要跟上" % args.auto)
    print("=" * 74)

    done = 0
    try:
        st = cmd(ser, "LIGHT %d" % args.brightness, 2.0) or {}
        print("  灯带已设为 %s%%，等相机自动曝光收敛（%.0f 秒）…" % (st.get("brightness"), SETTLE))
        time.sleep(SETTLE)
        cur = cmd(ser, "STATUS") or {}
        print("  稳定后照度: %s lx" % cur.get("lux"))
        print("-" * 74)

        i = 0
        while done < args.count:
            if not args.auto:
                try:
                    ans = input("  [%d/%d] 放好纸，按回车采集（q 结束）> " % (done, args.count)).strip().lower()
                except EOFError:
                    break
                if ans in ("q", "quit", "exit"):
                    break
                i += 1
                path = grab(str(i), tmp)
            else:
                time.sleep(args.auto)
                i += 1
                path = grab(str(i), tmp)
            if not path:
                print("      [!] 抓帧失败（摄像头被占用？看 _tmp_capture/ffmpeg.log）")
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            res = crop_labels(raw, columns=cols)
            if not res["kept"]:
                print("      [!] 没找到完整标签（检出 %d 个，剔除 %d 个）——把纸摆正在画面里、相机别贴太近"
                      % (res["total_found"], res["dropped_count"]))
                continue
            hw = cmd(ser, "STATUS") or {}
            lux, bright = hw.get("lux"), hw.get("brightness")
            names = []
            for item in res["kept"]:
                info = DM.save(args.cls, item["jpeg"], lux=lux, brightness=bright)
                names.append(info["file"])
                done += 1
                if done >= args.count:
                    break
            print("      ✔ 保存 %d 张（%s）  照度 %s lx  亮度 %s%%  剔除 %d 个"
                  % (len(names), names[0] if len(names) == 1 else "%s … %s" % (names[0], names[-1]),
                     lux, bright, res["dropped_count"]))
            print("        进度 %d/%d，%s 共 %d 张" % (done, args.count, args.cls, DM.count(args.cls)))
    except KeyboardInterrupt:
        print("\n  收到 Ctrl+C")
    finally:
        off = cmd(ser, "LIGHT 0", 1.5) or {}
        print("-" * 74)
        print("  已关灯（回读亮度 %s%%）" % off.get("brightness"))
        print("  本次采集 %d 张；%s 累计 %d 张" % (done, args.cls, DM.count(args.cls)))
        print("  记录表: %s" % DM.csv_path)
        ser.close()
        for f in os.listdir(tmp) if os.path.isdir(tmp) else []:
            try:
                os.remove(os.path.join(tmp, f))
            except Exception:
                pass
        if os.path.isdir(tmp):
            try:
                os.rmdir(tmp)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
