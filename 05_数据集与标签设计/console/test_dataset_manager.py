# -*- coding: utf-8 -*-
"""dataset_manager.py 的自测脚本（不依赖 app.py，可独立运行）

运行：  py -3.11 test_dataset_manager.py
"""
import csv
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dataset_manager import DatasetManager, CLASS_KEYS, CSV_HEADER

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def read_csv(p):
    with open(p, "r", encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.reader(f) if r]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ds_test_"))
    print(f"临时目录：{tmp}\n")
    try:
        dm = DatasetManager(tmp / "dataset")

        # --- 1. 初始状态 ---
        print("1) 初始化")
        check("CSV 已创建且表头正确", read_csv(dm.csv_path)[0] == CSV_HEADER)
        check("images 目录已创建", dm.images_dir.is_dir())
        st = dm.status()
        check("初始 total = 0", st["total"] == 0, st["total"])
        check("六个类别都在", set(st["per_class"].keys()) == set(CLASS_KEYS))

        # --- 2. 保存样本 ---
        print("\n2) 保存样本")
        r1 = dm.save("normal", b"\xff\xd8\xff\xe0FAKEJPEG1", lux=410.8, brightness=25)
        check("第 1 张文件名 = normal_0001.jpg", r1["file"] == "normal_0001.jpg", r1["file"])
        check("返回值 count=1", r1["count"] == 1)
        r2 = dm.save("normal", b"\xff\xd8\xff\xe0FAKEJPEG2", lux=412.1, brightness=25)
        check("第 2 张序号递增", r2["file"] == "normal_0002.jpg", r2["file"])
        r3 = dm.save("brokenline", b"\xff\xd8\xff\xe0FAKEJPEG3", lux=405.0, brightness=25)
        check("换类别后序号从 0001 开始", r3["file"] == "brokenline_0001.jpg", r3["file"])
        check("文件真的落盘了", (tmp / "dataset" / "images" / "normal" / "normal_0001.jpg").exists())

        # --- 3. CSV 内容 ---
        print("\n3) 记录表")
        rows = read_csv(dm.csv_path)
        check("CSV 行数 = 表头 + 3", len(rows) == 4, len(rows))
        check("第 1 行文件名正确", rows[1][0] == "normal_0001.jpg")
        check("normal 的缺陷类型写「无」", rows[1][2] == "无", rows[1][2])
        check("brokenline 的缺陷类型写中文「断线」", rows[3][2] == "断线", rows[3][2])
        check("照度被记录", rows[1][3] == "410.8", rows[1][3])
        check("亮度被记录", rows[1][4] == "25", rows[1][4])

        # --- 4. 统计 ---
        print("\n4) 统计")
        st = dm.status()
        check("normal = 2", st["per_class"]["normal"] == 2, st["per_class"])
        check("brokenline = 1", st["per_class"]["brokenline"] == 1)
        check("total = 3", st["total"] == 3)
        check("last 是最后保存的那张", st["last"]["file"] == "brokenline_0001.jpg", st["last"])

        # --- 5. 撤销上一张 ---
        print("\n5) 撤销上一张")
        d = dm.delete_last("normal")
        check("撤销返回 ok", d["ok"] is True)
        check("被删的是 normal_0002.jpg", d["deleted"] == "normal_0002.jpg", d["deleted"])
        check("撤销后 normal = 1", dm.count("normal") == 1)
        check("CSV 里也删掉了那一行", all(r[0] != "normal_0002.jpg" for r in read_csv(dm.csv_path)))
        check("文件也删掉了", not (tmp / "dataset" / "images" / "normal" / "normal_0002.jpg").exists())
        r4 = dm.save("normal", b"\xff\xd8\xff\xe0FAKEJPEG4", lux=411.0, brightness=25)
        check("撤销后再拍，序号不冲突", r4["file"] == "normal_0002.jpg", r4["file"])

        # --- 6. 最近文件 / 导出 ---
        print("\n6) 最近文件 / 导出")
        rec = dm.recent_files(5)
        check("recent_files 返回内容", len(rec) >= 2 and "file" in rec[0], rec)
        z = dm.export_zip()
        check("zip 非空", len(z) > 100, len(z))
        check("zip 是 PK 头", z[:2] == b"PK", z[:2])
        import zipfile, io
        with zipfile.ZipFile(io.BytesIO(z)) as zf:
            names = zf.namelist()
        check("zip 里有 CSV", any(n.endswith(".csv") for n in names), names)
        check("zip 里有图片", any(n.startswith("images/") for n in names), names)

        # --- 7. 错误处理 ---
        print("\n7) 错误处理")
        for bad, desc in [("notexist", "未知类别"), ("", "空类别")]:
            try:
                dm.save(bad, b"x")
                check(f"{desc} 应报错", False, "没报错")
            except ValueError:
                check(f"{desc} 正确报错", True)
        try:
            dm.save("normal", b"")
            check("空图片应报错", False, "没报错")
        except ValueError:
            check("空图片正确报错", True)
        d = dm.delete_last("stain")
        check("空类别撤销返回 ok=False", d["ok"] is False, d)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 结果：通过 {PASS}，失败 {FAIL} =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
