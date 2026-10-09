# -*- coding: utf-8 -*-
"""dataset_console.py —— 独立数据集采集台（硬件侧工具，不依赖任何已有软件）

为什么要独立
------------
给"采集样本"这件事做一个**自带页面的小工具**：不改、也不引用任何已提交的系统，
拷到装了 Python 的电脑上就能跑。它与硬件之间只通过我们自己的串口协议对话：

    STATUS         -> {"ok":true,"lux":123.4,"brightness":25,"result":"idle"}
    LIGHT <0-100>  -> 设置灯带亮度，并回一条 STATUS
    RESULT <s>     -> normal / abnormal / idle（采集台不用，留给最终演示）

约定：115200 波特率，一行一条 JSON，命令以换行结尾。

安全约定（重要）
----------------
工具在**连上串口时**和**退出时**都会主动发 `LIGHT 0` 关灯。
固件默认亮度已改为 0%（2026-10-09），这里再兜一层：连上串口与退出时都发 LIGHT 0。

用法
----
    py -3.11 dataset_console.py                    # 自动找 ESP32 串口，开 http://127.0.0.1:8789
    py -3.11 dataset_console.py --no-serial        # 不接硬件也能用（照度记空，只采图）
    py -3.11 dataset_console.py --port COM5 --http-port 8790
    py -3.11 dataset_console.py --out D:\\data\\mydataset

★ 必须用 http://127.0.0.1:8789 打开：localhost 属于浏览器"安全上下文"才允许开摄像头，
  用局域网 IP（如 http://192.168.x.x:8789）打开时浏览器会直接拒绝摄像头权限。
"""
from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import re
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.0"
_SAFE_NAME = re.compile(r"^[a-z0-9]+_\d{4,}\.jpg$", re.IGNORECASE)

# ---------- 找到 dataset_manager.py（本工具自带一份，放在同目录） ----------
for _cand in (HERE, os.path.join(HERE, "..", "app_module"), os.path.join(HERE, "..")):
    if os.path.isfile(os.path.join(_cand, "dataset_manager.py")):
        sys.path.insert(0, os.path.abspath(_cand))
        break
else:
    print("[x] 找不到 dataset_manager.py（应在 上一级/app_module/ 或本目录）")
    sys.exit(1)

from dataset_manager import CLASSES, DatasetManager      # noqa: E402
from flask import Flask, Response, jsonify, request, send_file   # noqa: E402
from label_crop import crop_labels                       # noqa: E402

# 采集台默认固定亮度（实测：100% 会过曝，箱内工作区间 20%~35%）
DEFAULT_BRIGHTNESS = 25


def log(msg: str = "") -> None:
    """控制台输出。GBK 控制台（Windows 默认 cp936）编不出对勾/警告这类花符号，
    会直接抛 UnicodeEncodeError，这里兜一层，保证脚本不会因为打印而崩。"""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(msg.encode(enc, "replace").decode(enc), flush=True)


# ======================================================================
#  硬件链路：只认我们自己的固件协议
# ======================================================================
class HwLink:
    """ESP32 串口链路。所有读写都在同一把锁里，避免"页面轮询"和"按快门"抢串口缓冲区
    （这种抢缓冲区的问题在网页采集小工具上实测出现过：照度被读成 -1）。"""

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

    # ---------- 打开 ----------
    def _candidates(self, port: str | None) -> list[str]:
        if port:
            return [port]
        try:
            from serial.tools import list_ports
        except ImportError:
            self.state["error"] = "未安装 pyserial：py -3.11 -m pip install pyserial"
            return []
        out, other = [], []
        for p in list_ports.comports():
            # CP2102（本板实测用的芯片）优先；其余串口排在后面
            if (p.vid, p.pid) in ((0x10C4, 0xEA60), (0x1A86, 0x7523)):
                out.append(p.device)
            else:
                other.append(p.device)
        if not out and not other:
            self.state["error"] = "系统里没有找到任何串口（ESP32 没插好？）"
        return out + other

    def _open(self, port: str | None, baud: int) -> None:
        try:
            import serial
        except ImportError:
            self.state["error"] = "未安装 pyserial：py -3.11 -m pip install pyserial"
            return
        last_err = None
        for dev in self._candidates(port):
            try:
                ser = serial.Serial(dev, baud, timeout=1.0)
                time.sleep(0.3)          # 打开串口会复位开发板，等它起来
                ser.reset_input_buffer()
                self.ser = ser
                self.state["port"] = dev
                self.state["connected"] = True
                self.state["error"] = ""
                # 安全第一：刚连上先关灯（固件默认已是 0%，这里保证任何情况下都归零）
                self.set_light(0)
                return
            except Exception as exc:      # 串口被占用 / 权限 / 设备消失
                last_err = exc
        self.state["error"] = f"打开串口失败：{last_err}" if last_err else "没有可用串口"

    # ---------- 收发 ----------
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
                        continue          # 固件启动时可能夹杂调试输出
                    try:
                        return json.loads(text)
                    except ValueError:
                        continue
            except Exception as exc:
                self.state["connected"] = False
                self.state["error"] = f"串口通信失败：{exc}"
        return None

    def refresh(self) -> dict:
        """读一次硬件状态（照度 / 亮度 / 结果）"""
        data = self._command("STATUS")
        if data and data.get("ok"):
            self.state["connected"] = True
            self.state["lux"] = data.get("lux")
            self.state["brightness"] = data.get("brightness")
            self.state["result"] = data.get("result", "")
        elif self.ser is None:
            pass
        else:
            self.state["connected"] = False
        return dict(self.state)

    def set_light(self, percent: int) -> dict:
        percent = max(0, min(100, int(percent)))
        data = self._command(f"LIGHT {percent}")
        if data and data.get("ok"):
            self.state["brightness"] = data.get("brightness")
            self.state["lux"] = data.get("lux")
            self.state["result"] = data.get("result", "")
            self.state["connected"] = True
        return dict(self.state)

    def close(self) -> None:
        """退出前务必关灯"""
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


# ======================================================================
#  Web 服务
# ======================================================================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False     # 让返回的 JSON 直接是中文，便于人看
DM: DatasetManager | None = None
HW: HwLink | None = None


def _page() -> str:
    with open(os.path.join(HERE, "console.html"), "r", encoding="utf-8") as fh:
        return fh.read()


@app.route("/")
def index():
    return Response(_page(), mimetype="text/html; charset=utf-8")


@app.route("/api/status")
def api_status():
    hw = HW.refresh()
    ds = DM.status()
    return jsonify({
        "ok": True,
        "hardware": hw,
        "dataset": {
            "per_class": ds["per_class"],
            "total": ds["total"],
            "last": ds["last"],
            "root": ds["root"],
            "csv": ds["csv"],
        },
        "classes": [{"key": k, "label": v} for k, v in CLASSES],
        "default_brightness": DEFAULT_BRIGHTNESS,
    })


@app.route("/api/capture", methods=["POST"])
def api_capture():
    """采集一张。

    mode=label（默认）：**一图一标签** —— 从整帧里自动找出每个完整标签，
                        逐个裁成单独图片存下；被画面边缘裁掉的一律剔除。
    mode=frame        ：整帧存一张（一图多标签的老行为）。
    """
    file = request.files.get("image")
    cls = (request.form.get("class") or "").strip()
    mode = (request.form.get("mode") or "label").strip().lower()
    if file is None:
        return jsonify({"ok": False, "error": "没有收到图片"}), 400
    try:
        raw = file.read()
        # 照度/亮度以**服务端刚读到的硬件状态**为准，前端传不了假数据
        hw = HW.refresh()
        lux, bright = hw.get("lux"), hw.get("brightness")

        if mode == "frame":
            info = DM.save(cls, raw, lux=lux, brightness=bright)
            return jsonify({"ok": True, "mode": "frame", "dataset": info,
                            "saved": 1, "dropped": 0})

        # ---- 一图一标签：自动裁切（可只保留指定列） ----
        cols_arg = (request.form.get("columns") or "").strip()
        want_cols = None
        if cols_arg:
            try:
                want_cols = [int(x) for x in cols_arg.replace("，", ",").split(",") if x.strip()]
            except ValueError:
                return jsonify({"ok": False, "error": "列号要写成 2,3 这样"}), 400
        result = crop_labels(raw, columns=want_cols)
        kept = result["kept"]
        if not kept:
            extra = "（当前只保留第 %s 列）" % cols_arg if want_cols else ""
            return jsonify({
                "ok": False,
                "error": "没找到可用的完整标签%s：检出 %d 个，其中 %d 个被画面边缘裁掉"
                         % (extra, result["total_found"], result["dropped_count"]),
                "columns": result["columns"],
            }), 400
        files = []
        for item in kept:
            info = DM.save(cls, item["jpeg"], lux=lux, brightness=bright)
            files.append({"file": info["file"], "box": item["box"], "row": item["row"],
                          "col": item["col"], "size": item["size"],
                          "brightness": item["brightness"]})
        # 标注预览图（顶部标出每列编号与亮度；绿框=已存、红框=剔除），方便当场复核
        preview_url = None
        if result.get("preview"):
            name = "_preview_%s.jpg" % time.strftime("%H%M%S")
            with open(os.path.join(DM.root, name), "wb") as fh:
                fh.write(result["preview"])
            preview_url = "/api/preview/" + name
        return jsonify({
            "ok": True, "mode": "label",
            "saved": len(files), "dropped": result["dropped_count"],
            "files": files, "preview_url": preview_url,
            "columns": result["columns"], "keep_columns": want_cols,
            "lux": lux, "brightness": bright,
            "message": "已保存 %d 个标签（%d 个被剔除）" % (len(files), result["dropped_count"]),
        })
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"保存失败：{exc}"}), 500


@app.route("/api/preview/<name>")
def api_preview(name: str):
    """裁切预览图（只允许取本目录下 _preview_*.jpg）"""
    if not name.startswith("_preview_") or not name.endswith(".jpg") or "/" in name or "\\" in name:
        return jsonify({"ok": False, "error": "非法文件名"}), 400
    path = os.path.join(DM.root, name)
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/api/undo", methods=["POST"])
def api_undo():
    data = request.get_json(silent=True) or {}
    try:
        result = DM.delete_last(data.get("class", ""))
        if not result.get("ok"):
            return jsonify({"ok": False, "error": result.get("error", "撤销失败")}), 400
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/light", methods=["POST"])
def api_light():
    data = request.get_json(silent=True) or {}
    try:
        percent = int(data.get("brightness", DEFAULT_BRIGHTNESS))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "亮度必须是 0-100 的整数"}), 400
    if not 0 <= percent <= 100:
        return jsonify({"ok": False, "error": "亮度必须是 0-100"}), 400
    state = HW.set_light(percent)
    if not state.get("connected"):
        # 别假装成功：没连硬件时灯带不会有任何变化
        return jsonify({
            "ok": False,
            "error": "未连接硬件，灯带没动。检查 ESP32 是否插好、串口是否被别的程序占用",
            "hardware": state,
        }), 409
    return jsonify({"ok": True, "hardware": state})


@app.route("/api/recent")
def api_recent():
    try:
        limit = max(1, min(24, int(request.args.get("limit", 12))))
    except ValueError:
        limit = 12
    return jsonify({"ok": True, "files": DM.recent_files(limit)})


@app.route("/api/thumb/<cls>/<name>")
def api_thumb(cls: str, name: str):
    """缩略图：只允许取 images/<已知类别>/<合法文件名>，防目录穿越"""
    from dataset_manager import CLASS_KEYS
    if cls not in CLASS_KEYS or not _SAFE_NAME.match(name):
        return jsonify({"ok": False, "error": "非法文件名"}), 400
    path = DM.images_dir / cls / name
    if not path.is_file():
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/api/export")
def api_export():
    try:
        blob = DM.export_zip()
        return send_file(io.BytesIO(blob), mimetype="application/zip",
                         as_attachment=True, download_name="lightguard_dataset.zip")
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ======================================================================
#  启动
# ======================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="独立数据集采集台")
    ap.add_argument("--out", default=os.path.join(HERE, "dataset"),
                    help="数据集输出目录（默认：本目录下的 dataset\\）")
    ap.add_argument("--port", default=None, help="ESP32 串口，如 COM5（默认自动查找）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--http-port", type=int, default=8789)
    ap.add_argument("--no-serial", action="store_true", help="不连硬件（照度记空，只采图）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    global DM, HW
    DM = DatasetManager(os.path.abspath(args.out))
    HW = HwLink(args.port, args.baud, enabled=not args.no_serial)
    atexit.register(HW.close)       # 无论怎么退出，都关灯

    hw = HW.state
    log("=" * 62)
    log(f"  独立数据集采集台 v{VERSION}")
    log("=" * 62)
    log(f"  数据集目录 : {DM.root}")
    log(f"  记录表     : {DM.csv_path}")
    if hw["connected"]:
        log(f"  硬件       : 已连接 {hw['port']}（已先关灯，按需点「设为 {DEFAULT_BRIGHTNESS}%」）")
    else:
        log(f"  硬件       : 未连接（{hw['error'] or '未指定'}）")
        log("               ——照度会记空，其余功能照常，可先练手采集流程")
    log(f"  页面地址   : http://127.0.0.1:{args.http_port}/   (必须用 127.0.0.1 打开，否则摄像头会被拦)")
    log("=" * 62)
    log("  快捷键：Enter 采集一张 / Z 撤销上一张 / 0 关灯")
    log("  退出：在本窗口按 Ctrl+C（退出时会自动关灯）")
    log("=" * 62)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.http_port}/")).start()

    try:
        app.run(host="127.0.0.1", port=args.http_port, debug=False, use_reloader=False, threaded=True)
    finally:
        HW.close()
        log("[OK] 已退出，并已把灯带关掉（LIGHT 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
