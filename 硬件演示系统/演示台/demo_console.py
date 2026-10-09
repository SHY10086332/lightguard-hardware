# -*- coding: utf-8 -*-
"""demo_console.py —— 硬件演示台（上位机软件，独立可用）

一个页面把硬件闭环跑完整：

    ① 稳光   上位机闭环：读照度 → 调 LIGHT n → 直到落在目标 ± 容差（算法见 light_loop.py）
    ② 采集   摄像头取帧 → 存图（照度/亮度由服务端现读，记进 CSV）
    ③ 判定   内置"参考图比对"（detect_ref.py）；也可手动点「判为正常/异常」
    ④ 反馈   通过串口发 RESULT normal|abnormal → 硬件亮绿灯/红灯 + 异常蜂鸣
    ⑤ 记录   每次判定写进 演示记录.csv（时间/照度/亮度/判定/差异占比）

不依赖任何已有软件：只用我们自己固件的串口协议 + 本目录三个模块。

用法
----
    py -3.11 demo_console.py                       # 自动找串口，开 http://127.0.0.1:8790
    py -3.11 demo_console.py --no-serial           # 没接硬件也能看界面
    py -3.11 demo_console.py --detect-threshold 0.25

★ 必须用 http://127.0.0.1:8790 打开（localhost 才算浏览器安全上下文，否则摄像头被拒）
★ 退出时（Ctrl+C）会自动关灯并复位指示
"""
from __future__ import annotations

import argparse
import atexit
import csv
import io
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (HERE,):
    if os.path.isfile(os.path.join(_cand, "hw_link.py")):
        sys.path.insert(0, os.path.abspath(_cand))
        break
else:
    print("[x] 找不到 hw_link.py / light_loop.py / detect_ref.py（应与本文件同目录）")
    sys.exit(1)

from detect_ref import ReferenceDetector          # noqa: E402
from flask import Flask, Response, jsonify, request, send_file   # noqa: E402
from hw_link import HwLink                        # noqa: E402
from light_loop import LightLoop                  # noqa: E402
from PIL import Image                             # noqa: E402

VERSION = "1.0"
CSV_HEADER = ["时间", "图片", "照度lx", "亮度%", "判定", "差异%", "相似度%", "备注"]


def _to_jpeg(raw: bytes, quality: int = 92) -> bytes:
    """把上传的图片统一转成真正的 JPEG 再落盘。

    两个好处：
      1. 扩展名与内容一定一致（之前接口收到 PNG 也会照原样存成 .jpg）；
      2. **顺手丢掉 EXIF 等元数据** —— 相机照片可能带设备/GPS/时间信息，
         数据集是要交付出去的，不该把这些一起带出去。
    """
    with Image.open(io.BytesIO(raw)) as im:
        rgb = im.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=quality)
        return buf.getvalue()

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

HW: HwLink | None = None
LOOP: LightLoop | None = None
DET: ReferenceDetector | None = None
OUT = ""
PHOTOS = ""
CSV_PATH = ""
REF_PATH = ""
LOG_LOCK = threading.Lock()


def log(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(msg.encode(enc, "replace").decode(enc), flush=True)


def _ensure_csv() -> None:
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerow(CSV_HEADER)


def _append_csv(row: list) -> None:
    with LOG_LOCK:
        _ensure_csv()
        with open(CSV_PATH, "a", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerow(row)


def _read_csv(limit: int) -> list:
    if not os.path.exists(CSV_PATH):
        return []
    with LOG_LOCK:
        with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as fh:
            rows = [r for r in csv.reader(fh) if r]
    if not rows:
        return []
    body = rows[1:]
    return body[-limit:][::-1]


def _stats() -> dict:
    rows = _read_csv(100000)
    normal = sum(1 for r in rows if len(r) >= 5 and r[4] == "正常")
    abnormal = sum(1 for r in rows if len(r) >= 5 and r[4] == "异常")
    return {"total": len(rows), "normal": normal, "abnormal": abnormal}


# ======================================================================
#  页面与状态
# ======================================================================
@app.route("/")
def index():
    with open(os.path.join(HERE, "demo.html"), "r", encoding="utf-8") as fh:
        return Response(fh.read(), mimetype="text/html; charset=utf-8")


@app.route("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "hardware": HW.status(),
        "loop": LOOP.status(),
        "detect": {
            "has_reference": DET.has_reference(),
            "threshold": DET.threshold,
            "reference_size": DET.ref_size,
        },
        "stats": _stats(),
        "out_dir": OUT,
        "csv": CSV_PATH,
    })


# ======================================================================
#  灯光
# ======================================================================
@app.route("/api/light", methods=["POST"])
def api_light():
    data = request.get_json(silent=True) or {}
    try:
        percent = int(data.get("brightness", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "亮度必须是 0-100 的整数"}), 400
    if not 0 <= percent <= 100:
        return jsonify({"ok": False, "error": "亮度必须是 0-100"}), 400
    st = HW.set_light(percent)
    if not st.get("connected"):
        return jsonify({"ok": False, "error": "未连接硬件，灯带没动", "hardware": st}), 409
    return jsonify({"ok": True, "hardware": st})


@app.route("/api/warm", methods=["POST"])
def api_warm():
    data = request.get_json(silent=True) or {}
    try:
        target = float(data.get("target", 300))
        tol = float(data.get("tolerance", 30))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "目标照度/容差必须是数字"}), 400
    if not 0 < target <= 20000:
        return jsonify({"ok": False, "error": "目标照度要在 0~20000 lx 之间"}), 400
    start = data.get("start")
    result = LOOP.start(target, tol, int(start) if start not in (None, "") else None)
    if not result.get("ok"):
        return jsonify(result), 409
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    return jsonify(LOOP.stop())


@app.route("/api/result", methods=["POST"])
def api_result():
    data = request.get_json(silent=True) or {}
    value = (data.get("value") or "idle").strip().lower()
    if value not in ("normal", "abnormal", "idle"):
        return jsonify({"ok": False, "error": "只能是 normal / abnormal / idle"}), 400
    st = HW.set_result(value)
    if not st.get("connected"):
        return jsonify({"ok": False, "error": "未连接硬件，指示没变", "hardware": st}), 409
    return jsonify({"ok": True, "hardware": st})


# ======================================================================
#  参考图与阈值
# ======================================================================
@app.route("/api/reference", methods=["POST"])
def api_reference():
    f = request.files.get("image")
    if f is None:
        return jsonify({"ok": False, "error": "没有收到图片"}), 400
    try:
        raw = _to_jpeg(f.read())        # 统一成 JPEG（同时去掉 EXIF 元数据）
    except Exception as exc:
        return jsonify({"ok": False, "error": f"图片无法解码：{exc}"}), 400
    try:
        info = DET.set_reference(raw)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"参考图设置失败：{exc}"}), 400
    with open(REF_PATH, "wb") as fh:
        fh.write(raw)
    return jsonify({"ok": True, **info, "message": "参考图已设置（用合格品照片）"})


@app.route("/api/reference/clear", methods=["POST"])
def api_reference_clear():
    DET.clear_reference()
    if os.path.exists(REF_PATH):
        os.remove(REF_PATH)
    return jsonify({"ok": True})


@app.route("/api/reference.jpg")
def api_reference_jpg():
    if not os.path.exists(REF_PATH):
        return jsonify({"ok": False, "error": "还没有参考图"}), 404
    return send_file(REF_PATH, mimetype="image/jpeg")


@app.route("/api/threshold", methods=["POST"])
def api_threshold():
    data = request.get_json(silent=True) or {}
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "阈值必须是数字"}), 400
    if not 0 < value <= 100:
        return jsonify({"ok": False, "error": "阈值要在 0~100 之间（百分数）"}), 400
    DET.threshold = value
    return jsonify({"ok": True, "threshold": value})


# ======================================================================
#  采集 + 判定
# ======================================================================
@app.route("/api/capture", methods=["POST"])
def api_capture():
    f = request.files.get("image")
    if f is None:
        return jsonify({"ok": False, "error": "没有收到图片"}), 400
    mode = (request.form.get("mode") or "auto").strip().lower()   # auto=自动判定 / manual=只存图
    try:
        raw = _to_jpeg(f.read())        # 统一成 JPEG（同时去掉 EXIF 元数据）
    except Exception as exc:
        return jsonify({"ok": False, "error": f"图片无法解码：{exc}"}), 400

    name = "photo_%s.jpg" % datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(PHOTOS, name)
    with open(path, "wb") as fh:
        fh.write(raw)

    st = HW.status()
    lux, bright = st.get("lux"), st.get("brightness")

    detect = None
    verdict = "未判定"
    note = ""
    if mode == "auto":
        if not DET.has_reference():
            return jsonify({"ok": False, "error": "还没有参考图：先用一张合格品照片点「设为参考图」"}), 400
        try:
            detect = DET.compare(raw)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"判定失败：{exc}"}), 500
        verdict = "异常" if detect["anomaly"] else "正常"
        note = detect["message"]
        if st.get("connected"):
            HW.set_result(detect["verdict"])          # 硬件亮红/绿灯 + 异常蜂鸣
        diff_path = os.path.join(OUT, "last_diff.jpg")
        if detect.get("diff_jpeg"):
            with open(diff_path, "wb") as fh:
                fh.write(detect["diff_jpeg"])
        detect = {k: v for k, v in detect.items() if k != "diff_jpeg"}

    _append_csv([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name,
                 "" if lux is None else "%.1f" % float(lux),
                 "" if bright is None else int(bright),
                 verdict,
                 "" if not detect else detect["defect_ratio"],
                 "" if not detect else detect["similarity"],
                 note or ("手动模式（未自动判定）" if mode == "manual" else "")])

    return jsonify({
        "ok": True, "file": name, "mode": mode, "verdict": verdict,
        "lux": lux, "brightness": bright, "detect": detect,
        "photo_url": "/api/photo/" + name,
        "diff_url": "/api/diff.jpg" if detect else None,
    })


@app.route("/api/photo/<name>")
def api_photo(name: str):
    if not name.replace("_", "").replace(".", "").isalnum() or not name.endswith(".jpg"):
        return jsonify({"ok": False, "error": "非法文件名"}), 400
    path = os.path.join(PHOTOS, name)
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/api/diff.jpg")
def api_diff():
    path = os.path.join(OUT, "last_diff.jpg")
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "还没有差异图"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/api/log")
def api_log():
    try:
        limit = max(1, min(100, int(request.args.get("limit", 20))))
    except ValueError:
        limit = 20
    return jsonify({"ok": True, "rows": _read_csv(limit), "stats": _stats()})


# ======================================================================
#  启动
# ======================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="硬件演示台（Photometric closed loop + verdict feedback）")
    ap.add_argument("--out", default=os.path.join(HERE, "demo_output"))
    ap.add_argument("--port", default=None, help="串口，如 COM5（默认自动查找）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--http-port", type=int, default=8790)
    ap.add_argument("--detect-threshold", type=float, default=0.25,
                    help="参考图比对的差异占比阈值（百分数，默认 0.25）")
    ap.add_argument("--no-serial", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    global HW, LOOP, DET, OUT, PHOTOS, CSV_PATH, REF_PATH
    OUT = os.path.abspath(args.out)
    PHOTOS = os.path.join(OUT, "photos")
    CSV_PATH = os.path.join(OUT, "演示记录.csv")
    REF_PATH = os.path.join(OUT, "reference.jpg")
    os.makedirs(PHOTOS, exist_ok=True)
    _ensure_csv()

    DET = ReferenceDetector(args.detect_threshold)
    HW = HwLink(args.port, args.baud, enabled=not args.no_serial)
    LOOP = LightLoop(HW)
    atexit.register(HW.close)          # 退出一定关灯

    hw = HW.state
    log("=" * 64)
    log(f"  硬件演示台 v{VERSION}")
    log("=" * 64)
    log(f"  输出目录 : {OUT}")
    log(f"  记录表   : {CSV_PATH}")
    if hw["connected"]:
        log(f"  硬件     : 已连接 {hw['port']}（{hw['device'] or 'LIGHTGUARD_ESP32'}，已先关灯）")
    else:
        log(f"  硬件     : 未连接（{hw['error']}）")
        log("             ——界面与检测仍可用（照度记空），接上硬件后重启即可")
    log(f"  判定阈值 : 差异占比 > {args.detect_threshold}% 判为异常")
    log(f"  页面     : http://127.0.0.1:{args.http_port}/   (必须用 127.0.0.1 打开)")
    log("=" * 64)
    log("  流程：设参考图 → 稳光 → 拍照判定（自动发 RESULT → 红绿灯/蜂鸣）")
    log("  退出：Ctrl+C（会自动关灯并复位指示）")
    log("=" * 64)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.http_port}/")).start()

    try:
        app.run(host="127.0.0.1", port=args.http_port, debug=False, use_reloader=False, threaded=True)
    finally:
        try:
            HW.set_result("idle")
        except Exception:
            pass
        HW.close()
        log("[OK] 已退出：灯带已关（LIGHT 0），指示已复位（idle）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
