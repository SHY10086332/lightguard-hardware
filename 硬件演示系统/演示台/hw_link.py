# -*- coding: utf-8 -*-
"""hw_link.py —— ESP32 串口链路（上位机软件共用的一份薄封装）

协议就是我们自己固件实现的那套，115200 波特率、一行一条 JSON：

    PING           -> {"ok":true,"device":"LIGHTGUARD_ESP32"}
    STATUS         -> {"ok":true,"lux":123.4,"brightness":25,"result":"idle"}
    LIGHT <0-100>  -> 设置灯带亮度，并回一条 STATUS
    RESULT <s>     -> normal / abnormal / idle，并回一条 STATUS

线程安全：所有读写都在同一把锁里 —— 页面的轮询线程和闭环控制线程会同时用串口，
不锁的话两个请求会互相抢串口缓冲区（网页采集小工具上实测出现过照度被读成 -1）。

安全：连上串口时、以及对象销毁/关闭时都会发 LIGHT 0 关灯。
（正式固件复位后默认亮度 45%，"绝不留下亮着的灯带"必须由上位机兜住。）
"""
from __future__ import annotations

import json
import threading
import time


class HwLink:
    def __init__(self, port: str | None = None, baud: int = 115200, enabled: bool = True) -> None:
        self.lock = threading.Lock()
        self.ser = None
        self.state = {
            "connected": False, "port": None, "device": "", "error": "",
            "lux": None, "brightness": None, "result": "",
        }
        if enabled:
            self._open(port, baud)
        else:
            self.state["error"] = "已用 --no-serial 启动（不连硬件）"

    # ---------------- 打开 ----------------
    def _candidates(self, port: str | None) -> list[str]:
        if port:
            return [port]
        try:
            from serial.tools import list_ports
        except ImportError:
            self.state["error"] = "未安装 pyserial：py -3.11 -m pip install pyserial"
            return []
        first, rest = [], []
        for p in list_ports.comports():
            if (p.vid, p.pid) in ((0x10C4, 0xEA60), (0x1A86, 0x7523)):
                first.append(p.device)          # CP2102 / CH340 优先
            else:
                rest.append(p.device)
        if not first and not rest:
            self.state["error"] = "系统里没有找到任何串口（ESP32 没插好？）"
        return first + rest

    def _open(self, port: str | None, baud: int) -> None:
        try:
            import serial
        except ImportError:
            self.state["error"] = "未安装 pyserial：py -3.11 -m pip install pyserial"
            return
        last = None
        for dev in self._candidates(port):
            try:
                ser = serial.Serial(dev, baud, timeout=1.0)
                time.sleep(0.3)
                ser.reset_input_buffer()
                self.ser = ser
                self.state["port"] = dev
                self.state["connected"] = True
                self.state["error"] = ""
                ping = self._command("PING")
                if ping:
                    self.state["device"] = ping.get("device", "")
                self.set_light(0)               # 连上先关灯
                return
            except Exception as exc:
                last = exc
        self.state["error"] = f"打开串口失败：{last}" if last else "没有可用串口"

    # ---------------- 收发 ----------------
    def _command(self, cmd: str, timeout: float = 1.2) -> dict | None:
        if not self.ser:
            return None
        with self.lock:
            try:
                self.ser.reset_input_buffer()
                self.ser.write((cmd.strip() + "\n").encode("ascii"))
                self.ser.flush()
                deadline = time.time() + timeout
                while time.time() < deadline:
                    raw = self.ser.readline()
                    if not raw:
                        continue
                    text = raw.decode("utf-8", "replace").strip()
                    if not text.startswith("{"):
                        continue
                    try:
                        return json.loads(text)
                    except ValueError:
                        continue
            except Exception as exc:
                self.state["connected"] = False
                self.state["error"] = f"串口通信失败：{exc}"
        return None

    def _absorb(self, data: dict | None) -> None:
        if data and data.get("ok"):
            self.state["connected"] = True
            if "lux" in data:
                self.state["lux"] = data.get("lux")
            if "brightness" in data:
                self.state["brightness"] = data.get("brightness")
            if "result" in data:
                self.state["result"] = data.get("result", "")

    def status(self) -> dict:
        data = self._command("STATUS")
        if data and data.get("ok"):
            self._absorb(data)
        elif self.ser is not None:
            self.state["connected"] = False
        return dict(self.state)

    def set_light(self, percent: int) -> dict:
        percent = max(0, min(100, int(percent)))
        self._absorb(self._command(f"LIGHT {percent}"))
        return dict(self.state)

    def set_result(self, value: str) -> dict:
        value = (value or "idle").strip().lower()
        self._absorb(self._command(f"RESULT {value}"))
        return dict(self.state)

    def close(self) -> None:
        if self.ser:
            try:
                self.set_light(0)
            except Exception:
                pass
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.state["connected"] = False
