# -*- coding: utf-8 -*-
"""light_loop.py —— 上位机照度闭环（把"自动稳光"做在上位机里）

为什么做在上位机：正式固件目前只有手动 `LIGHT n`（闭环还留在验证固件里），
而闭环算法的关键参数依赖**传感器与灯带的实际几何**（实测出现过 9.8 / 28.5 / 39 lx 每 1% 三种斜率），
放在上位机就能自适应，不用每次重新烧固件。

算法（都是踩过坑之后加的）：
  1. **比例控制 + 步长限幅**：只按误差比例走，单次最多挪 MAX_STEP%，避免"猛冲→过冲→振荡"
     （固件早期 KP=0.06 配 39 lx/% 的增益，环路增益 2.34，亮度在 0↔100% 之间来回跳）；
  2. **自动估算增益**：用"上次改了多少亮度、照度变了多少"实时估算 lx/每1%，
     步长按 `误差 / 增益 × 阻尼` 取 —— 换几何、换灯带都不用改代码；
  3. **死区**：误差进入 ±容差 就停，不在目标点附近抖；
  4. **最小步长**：误差换算出来小于 1% 时按 1% 走，否则会因为取整永远到不了目标；
  5. **改完亮度必须等**：实测 0.8 秒读数是错的（会读出 12.5 lx 这种乱数），
     等 2.2 秒后连读 3 次取中位才稳（0%/25%/100% 三档极差 0.0~2.5 lx）。
"""
from __future__ import annotations

import statistics
import threading
import time

SETTLE = 2.2            # 改亮度后等待时间（实测得出，别改小）
READS = 3               # 每次取 3 个读数取中位
KP = 0.8                # 阻尼：步长 = 误差 / 增益 × KP
MAX_STEP = 12.0         # 单次最大亮度变化（%）
MIN_STEP = 1.0          # 单次最小亮度变化（%）
DEFAULT_SLOPE = 10.0    # 未知增益时的默认值（本机桌面几何实测约 9.8 lx/每1%）


class LightLoop:
    def __init__(self, hw, settle: float = SETTLE) -> None:
        self.hw = hw
        self.settle = settle
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.state = {
            "running": False,
            "target": None,
            "tolerance": 30.0,
            "brightness": None,
            "lux": None,
            "iterations": 0,
            "converged": False,
            "message": "",
            "slope": None,
            "history": [],          # [{"lux":..,"brightness":..,"err":..}]
        }

    # ---------------- 读照度（带稳定等待） ----------------
    def _measure(self) -> float | None:
        vals = []
        for _ in range(READS):
            st = self.hw.status()
            if st.get("lux") is not None:
                vals.append(float(st["lux"]))
            time.sleep(0.25)
        if not vals:
            return None
        return statistics.median(vals)

    def _apply(self, percent: int) -> None:
        self.hw.set_light(percent)
        time.sleep(self.settle)

    # ---------------- 主循环（在子线程里跑） ----------------
    def _run(self, target: float, tolerance: float, start: int | None) -> None:
        st = self.state
        try:
            cur = self.hw.status()
            if not cur.get("connected"):
                with self.lock:
                    st.update(running=False, converged=False,
                              message="未连接硬件：" + (cur.get("error") or "串口不可用"))
                return

            brightness = start if start is not None else (cur.get("brightness") or 25)
            brightness = max(0, min(100, int(brightness)))
            with self.lock:
                st["brightness"] = brightness
                st["history"] = []
            self._apply(brightness)

            slope = None
            prev_lux, prev_br = None, None
            converged = False
            message = ""

            for i in range(1, 26):
                if self.stop_flag.is_set():
                    message = "已手动停止"
                    break
                lux = self._measure()
                if lux is None:
                    message = "读不到照度（串口断了？）"
                    break

                # 估算增益：上次改动带来多少照度变化
                if prev_lux is not None and abs(brightness - prev_br) >= 2:
                    cand = (lux - prev_lux) / (brightness - prev_br)
                    if cand > 0.5:
                        slope = cand if slope is None else 0.5 * slope + 0.5 * cand
                err = target - lux
                with self.lock:
                    st.update(lux=round(lux, 1), brightness=brightness, iterations=i,
                              slope=None if slope is None else round(slope, 2))
                    st["history"].append({"lux": round(lux, 1), "brightness": brightness,
                                          "err": round(err, 1)})

                if abs(err) <= tolerance:
                    converged = True
                    message = "稳光完成：%.1f lx（目标 %.0f ± %.0f）" % (lux, target, tolerance)
                    break

                g = slope if slope and slope > 0.5 else DEFAULT_SLOPE
                step = (err / g) * KP
                step = max(-MAX_STEP, min(MAX_STEP, step))
                if abs(step) < MIN_STEP:
                    step = MIN_STEP if step >= 0 else -MIN_STEP
                new_br = int(round(brightness + step))
                new_br = max(0, min(100, new_br))
                if new_br == brightness:
                    message = "亮度已到极限（%d%%），照度只能到 %.1f lx" % (brightness, lux)
                    break

                prev_lux, prev_br = lux, brightness
                brightness = new_br
                self._apply(brightness)
            else:
                message = "达到最大迭代次数仍未收敛"

            with self.lock:
                st.update(running=False, converged=converged, message=message,
                          brightness=brightness)
        except Exception as exc:                     # 线程里任何异常都要落回 state
            with self.lock:
                st.update(running=False, converged=False, message="闭环异常：" + str(exc))

    # ---------------- 对外 ----------------
    def start(self, target: float, tolerance: float = 30.0, start: int | None = None) -> dict:
        if self.state["running"]:
            return {"ok": False, "error": "已经有一次稳光在进行中"}
        self.stop_flag.clear()
        with self.lock:
            self.state.update(running=True, converged=False, target=float(target),
                              tolerance=float(tolerance), iterations=0,
                              message="稳光中…", history=[])
        self.thread = threading.Thread(target=self._run, args=(float(target), float(tolerance), start),
                                       daemon=True)
        self.thread.start()
        return {"ok": True}

    def stop(self) -> dict:
        self.stop_flag.set()
        return {"ok": True, "message": "已请求停止"}

    def status(self) -> dict:
        with self.lock:
            st = dict(self.state)
            st["history"] = list(self.state["history"])
        return st
