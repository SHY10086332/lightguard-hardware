# -*- coding: utf-8 -*-
"""hold_light.py —— 保持灯带亮着（调光照、摆灯带、肉眼看均匀度时用）

为什么需要它：
  这块板**串口一开一关就会复位**，而固件复位后默认亮度 0% —— 所以
  "设一下亮度然后断开串口"的结果是：**灯马上就灭了**（实测踩过）。
  想让灯一直亮着，就必须让串口**保持连接**。

行为：
  · 连上串口 → 设亮度 → 保持连接，期间每隔几秒回读一次（打印出来，证明还活着）
  · 到时间（默认 20 分钟）或用 Ctrl+C / 进程被杀 → **发 LIGHT 0 关灯并释放串口**

用法:
    py -3.11 hold_light.py                 # 50%，保持 20 分钟
    py -3.11 hold_light.py --level 25 --minutes 40
    py -3.11 hold_light.py --level 0       # 只用来关灯
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("[x] 没装 pyserial：py -3.11 -m pip install pyserial")
    sys.exit(1)


def pick_port(explicit=None):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    for p in ports:
        if (p.vid, p.pid) in ((0x10C4, 0xEA60), (0x1A86, 0x7523)):
            return p.device
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--level", type=int, default=50)
    ap.add_argument("--minutes", type=float, default=20.0)
    args = ap.parse_args()

    dev = pick_port(args.port)
    if not dev:
        print("[x] 没找到串口")
        return 1
    ser = serial.Serial(dev, 115200, timeout=1.0)
    time.sleep(0.4)
    ser.reset_input_buffer()
    try:
        st = cmd(ser, "LIGHT %d" % args.level, 2.0) or {}
        print("=" * 62)
        print("  灯带已设为 %s%%（串口 %s 保持连接中，所以不会复位）" % (st.get("brightness"), dev))
        print("  保持 %.0f 分钟；期间可随时 Ctrl+C 结束（结束会关灯）" % args.minutes)
        print("  ⚠ 串口被本脚本占用：演示台 / 采集台 / 串口监视器 现在打不开 COM 口")
        print("=" * 62)
        deadline = time.time() + args.minutes * 60
        flag = os.path.join(os.path.dirname(os.path.abspath(__file__)), "关灯.flag")
        if os.path.exists(flag):
            os.remove(flag)
        n = 0
        while time.time() < deadline:
            time.sleep(5)
            n += 1
            # 别的程序（比如双击「关灯_应急.bat」）打不开串口时，会放这个旗标文件来叫我关灯
            if os.path.exists(flag):
                try:
                    os.remove(flag)
                except Exception:
                    pass
                print("  收到「关灯」旗标（另一个程序请求关灯）", flush=True)
                break
            if n % 6 == 0:                       # 每 30 秒回读一次，证明连接还活着
                cur = cmd(ser, "STATUS", 1.0) or {}
                print("  [%s] 亮度 %s%%   照度 %s lx"
                      % (time.strftime("%H:%M:%S"), cur.get("brightness"), cur.get("lux")),
                      flush=True)
    except KeyboardInterrupt:
        print("\n  收到 Ctrl+C")
    finally:
        off = cmd(ser, "LIGHT 0", 1.5) or {}
        print("  已关灯（回读亮度 %s%%），串口已释放" % off.get("brightness"))
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
