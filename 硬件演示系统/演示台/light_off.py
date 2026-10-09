# -*- coding: utf-8 -*-
"""light_off.py —— 应急关灯（一条命令把灯带关掉）

什么时候用：
  · 演示台被强杀（关窗口 / 任务管理器）后灯还亮着；
  · 板子刚复位，固件回到默认亮度；
  · 现场收拾东西前想确认灯已灭。

原理：直接发固件协议命令 `LIGHT 0`，并把回读的亮度打印出来（不靠"软件自称"，看硬件回读）。
用法: py -3.11 light_off.py            （自动找串口）
      py -3.11 light_off.py COM5
"""
import json
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


def main() -> int:
    dev = pick_port(sys.argv[1] if len(sys.argv) > 1 else None)
    if not dev:
        print("[x] 没找到串口：ESP32 没插或串口被占用")
        return 1
    try:
        ser = serial.Serial(dev, 115200, timeout=1.0)
    except Exception as exc:
        print("[x] 打不开 %s：%s" % (dev, exc))
        print("    可能是 Arduino 串口监视器 / 采集台 / 演示台 还占着串口，先关掉它们")
        return 1
    time.sleep(0.4)
    ser.reset_input_buffer()
    try:
        before = cmd(ser, "STATUS")
        if before:
            print("关灯前: 亮度 %s%%   照度 %s lx" % (before.get("brightness"), before.get("lux")))
        off = cmd(ser, "LIGHT 0", 1.5)
        after = cmd(ser, "STATUS", 1.0)
        bright = (after or off or {}).get("brightness")
        if bright == 0:
            print("[OK] 灯带已关（硬件回读 亮度 = 0%）")
            return 0
        print("[!] 回读亮度 = %s，可能没关成功，再试一次" % bright)
        cmd(ser, "LIGHT 0", 1.5)
        again = cmd(ser, "STATUS", 1.0)
        ok = (again or {}).get("brightness") == 0
        print("[OK] 已关（回读 0%）" if ok else "[x] 仍未关掉：检查 5V 电源，或直接拔掉灯带电源")
        return 0 if ok else 1
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main())
