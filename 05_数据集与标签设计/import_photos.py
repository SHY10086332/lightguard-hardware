# -*- coding: utf-8 -*-
"""import_photos.py —— 把手机拍的照片变成规范数据集（自动裁出一图一标签）

用途：用手机拍打印好的标签纸，然后把照片导进电脑，跑这个脚本，
它会把每张照片里**完整的标签**自动裁成单张图片，按规范命名并写进记录表。

流程：
    手机拍照 → 拷到电脑某个文件夹 → 本脚本 → split_dataset.py → train_custom_category.py → 检测演示

用法：
    py -3.11 import_photos.py --src "D:\\照片\\正常" --class normal
    py -3.11 import_photos.py --src "D:\\照片\\污点" --class stain --auto
    py -3.11 import_photos.py --src "D:\\照片\\整张" --class normal --no-crop   # 不裁，整张照片当一个样本

参数：
    --src     手机照片所在文件夹（.jpg/.jpeg/.png；HEIC 需要先转成 JPG）
    --class   类别（normal / brokenline / faint / stain / scratch / offset）
    --out     数据集目录（默认 ./dataset）
    --auto    按个数自动保留列（同采集台里那个"按个数自动"）
    --columns 只保留指定列，如 2,3
    --no-crop 不裁切，整张照片作为一个样本
    --resize  裁出来的图统一缩放到这个长边像素（默认 0 = 不缩放）

注意（重要）：
  · 手机拍的光照与"箱内相机 + 灯带"的光照**不一致**，用手机数据训练出来的模型，
    拿到箱内演示时可能判不准。建议：手机数据先把"训练→检测"链路跑通并出材料，
    最终演示前**再用箱内相机采一小批同光照样本**（那次采集才是演示基准）。
  · 记录表里的照度/亮度会留空（手机照片没有这两个数据），这是正常的。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 本脚本在 dataset\ 下，dataset_manager.py / label_crop.py 在 dataset\console\ 里
for _c in (HERE, os.path.join(HERE, "console"), os.path.join(HERE, "app_module"),
           os.path.join(HERE, "..", "console")):
    if os.path.isfile(os.path.join(_c, "dataset_manager.py")):
        sys.path.insert(0, os.path.abspath(_c))
        break
else:
    print("[x] 找不到 dataset_manager.py / label_crop.py")
    sys.exit(1)

from dataset_manager import CLASSES, DatasetManager      # noqa: E402
from label_crop import crop_labels                       # noqa: E402

EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
HEIC = (".heic", ".heif")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--class", dest="cls", required=True, choices=[k for k, _ in CLASSES])
    ap.add_argument("--out", default=os.path.join(HERE, "dataset"))
    ap.add_argument("--auto", action="store_true", help="按个数自动保留列")
    ap.add_argument("--columns", default="", help="只保留列，如 2,3")
    ap.add_argument("--no-crop", action="store_true", help="不裁切，整张照片当一个样本")
    ap.add_argument("--resize", type=int, default=0, help="裁图长边缩放到 N 像素（0=不缩放）")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        print("[x] 找不到文件夹：%s" % src)
        return 1
    files = sorted(f for f in os.listdir(src) if f.lower().endswith(EXTS))
    heics = sorted(f for f in os.listdir(src) if f.lower().endswith(HEIC))
    if not files and heics:
        print("[x] 只找到 HEIC 照片（苹果默认格式）——请先把它们转成 JPG，")
        print("    或在手机设置里把相机格式改成「兼容性最佳 / JPEG」再拍。")
        return 1
    if not files:
        print("[x] %s 里没有 jpg/png 照片" % src)
        return 1

    cols = [int(x) for x in args.columns.replace("，", ",").split(",") if x.strip()] or None
    DM = DatasetManager(os.path.abspath(args.out))

    print("=" * 74)
    print("  手机照片 → 数据集")
    print("=" * 74)
    print("  照片目录 : %s（%d 张）" % (src, len(files)))
    print("  类别     : %s" % args.cls)
    print("  裁切     : %s" % ("不裁（整张算一个样本）" if args.no_crop else
                               ("按个数自动保留列" if args.auto else
                                ("只保留列 %s" % ",".join(map(str, cols)) if cols else "保留全部列"))))
    print("  输出     : %s（当前该类已有 %d 张）" % (DM.root, DM.count(args.cls)))
    print("-" * 74)

    saved, skipped, dropped_all = 0, 0, 0
    for i, name in enumerate(files, 1):
        with open(os.path.join(src, name), "rb") as fh:
            raw = fh.read()
        if args.no_crop:
            info = DM.save(args.cls, raw)
            saved += 1
            print("  [%d/%d] %-28s → %s" % (i, len(files), name, info["file"]))
            continue
        res = crop_labels(raw, columns=cols, auto_columns=args.auto)
        if not res["kept"]:
            skipped += 1
            print("  [%d/%d] %-28s → ⚠ 没找到完整标签（检出 %d，剔除 %d），跳过"
                  % (i, len(files), name, res["total_found"], res["dropped_count"]))
            continue
        names = []
        for item in res["kept"]:
            jpg = item["jpeg"]
            if args.resize > 0:
                jpg = _resize(jpg, args.resize)
            info = DM.save(args.cls, jpg)
            names.append(info["file"])
            saved += 1
        dropped_all += res["dropped_count"]
        print("  [%d/%d] %-28s → 裁出 %d 张（%s）" % (i, len(files), name, len(names),
              names[0] if len(names) == 1 else "%s … %s" % (names[0], names[-1])))

    print("-" * 74)
    print("  完成：保存 %d 张；跳过 %d 张照片；累计剔除 %d 个不完整/劣势列标签" % (saved, skipped, dropped_all))
    print("  记录表: %s" % DM.csv_path)
    print("  该类现有: %d 张" % DM.count(args.cls))
    print()
    print("  下一步：")
    print("    1) 6 类都导完后 → py -3.11 split_dataset.py --src \"%s\" --dry-run" % DM.root)
    print("    2) 正式划分     → py -3.11 split_dataset.py --src \"%s\"" % DM.root)
    print("    3) 训练         → 用软件里的 train_custom_category.py（吃上面划分出来的目录）")
    return 0


def _resize(jpeg: bytes, long_side: int) -> bytes:
    import io
    from PIL import Image
    with Image.open(io.BytesIO(jpeg)) as im:
        w, h = im.size
        scale = long_side / max(w, h)
        if scale < 1:
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=92)
        return buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())
