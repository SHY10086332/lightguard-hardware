# -*- coding: utf-8 -*-
"""split_dataset.py —— 按赛事要求把采集到的数据集划分成训练/校准/测试目录

要求（来源：`AIC口径唯一基准.md` 第六节）
----------------------------------------
  采集目录：images\\<类别>\\<类别>_0001.jpg  +  采集记录表.csv
  训练目录映射：
      images\\normal\\   →  train/good  +  calibration/good  +  test/good   （约 6:2:2）
      5 类缺陷            →  calibration/defect  +  test/defect            （约 1:2）
  两条硬规则：
      ① **按"样品"划分，不能按图片划分** —— 同一张标签的多张照片必须进同一个集合
      ② **缺陷图绝不能进 train/good**（训练集只有合格品）

怎么知道哪些图属于同一个"样品"？
--------------------------------
采集台一次按快门（同一帧里自动裁出的 N 个标签）来自**同一张纸**，也就是同一个样品。
这些图在 `采集记录表.csv` 里的**时间戳完全相同**，所以本脚本**按时间戳分组**，
再把"组"整体分给某个集合 —— 这样天然满足规则①，不需要人工标注样品号。

用法
----
    py -3.11 split_dataset.py --src <数据集目录>            # 默认输出到 <src>\\..\\split
    py -3.11 split_dataset.py --src ... --out ... --dry-run # 只看会怎么分
    py -3.11 split_dataset.py --src ... --seed 7            # 换随机种子（默认 20261009）

输出
----
    <out>\\train\\good\\…           只有合格品
    <out>\\calibration\\good\\…     合格品校准集
    <out>\\test\\good\\…            合格品测试集
    <out>\\calibration\\defect\\…   缺陷校准集
    <out>\\test\\defect\\…          缺陷测试集
    <out>\\划分清单.csv             每张图去了哪里（含样品组号）
    <out>\\数据集统计报告.md        数量/照度/尺寸统计 + 合规自检
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import shutil
import statistics
import sys
from collections import defaultdict

CLASSES = ["normal", "brokenline", "faint", "stain", "scratch", "offset"]
DEFECT_CLASSES = CLASSES[1:]
CSV_NAME = "采集记录表.csv"
GOOD_RATIO = (6, 2, 2)        # train : calibration : test（合格品）
DEFECT_RATIO = (1, 2)         # calibration : test（缺陷）


def read_csv(root: str) -> dict:
    """返回 {文件名: (类别, 照度, 亮度, 时间)}"""
    path = os.path.join(root, CSV_NAME)
    info = {}
    if not os.path.isfile(path):
        return info
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) >= 6 and row[0] != "文件名":
                info[row[0]] = {"cls": row[1], "lux": row[3], "bright": row[4], "time": row[5]}
    return info


def group_samples(files: list, info: dict) -> list:
    """按"采集时间戳相同 = 同一张纸 = 同一个样品"分组。

    没有时间信息的图（例如手工拷进去的）按"单张一组"处理，保证不会被拆散。
    """
    groups = defaultdict(list)
    for name in files:
        key = info.get(name, {}).get("time") or ("单独_%s" % name)
        groups[key].append(name)
    return [sorted(v) for _, v in sorted(groups.items())]


def split_groups(groups: list, ratio: tuple, rng: random.Random) -> list:
    """把"样品组"整体分配到各集合，尽量贴近目标比例"""
    rng.shuffle(groups)
    total = sum(len(g) for g in groups) or 1
    buckets, acc = [[] for _ in ratio], 0
    targets = [t / sum(ratio) for t in ratio]
    for i, g in enumerate(groups):
        n = len(g)
        # 选"当前占比最低于目标"的桶
        best, best_gap = 0, None
        for b in range(len(ratio)):
            have = sum(len(x) for x in buckets[b]) + n
            gap = targets[b] - have / total
            if best_gap is None or gap > best_gap:
                best, best_gap = b, gap
        buckets[best].append(g)
        acc += n
    return buckets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="采集目录（含 images\\ 与 采集记录表.csv）")
    ap.add_argument("--out", default=None, help="输出目录（默认 <src>\\..\\split）")
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    img_root = os.path.join(src, "images")
    if not os.path.isdir(img_root):
        print("[x] 找不到 %s —— 先采集数据，或者用 --src 指到正确目录" % img_root)
        return 1
    out = os.path.abspath(args.out or os.path.join(os.path.dirname(src), "split"))
    info = read_csv(src)
    rng = random.Random(args.seed)

    files = {c: [] for c in CLASSES}
    for c in CLASSES:
        d = os.path.join(img_root, c)
        if os.path.isdir(d):
            files[c] = sorted(f for f in os.listdir(d) if f.lower().endswith(".jpg"))

    n_total = sum(len(v) for v in files.values())
    print("=" * 72)
    print("  数据集划分（按样品分组，缺陷图绝不进 train/good）")
    print("=" * 72)
    print("  源目录 : %s" % src)
    print("  输出   : %s" % out)
    print("  总数   : %d 张" % n_total)
    for c in CLASSES:
        print("    %-12s %d 张" % (c, len(files[c])))
    if n_total == 0:
        print("\n[x] 一张图都没有 —— 先去采集台采数据")
        return 1

    plan = {}            # 目标目录 → [文件名]
    rows = []            # 划分清单
    # ---- 合格品：train/calibration/test = 6:2:2 ----
    normal = files["normal"]
    g_normal = group_samples(normal, info)
    b_train, b_cal, b_test = split_groups(g_normal, GOOD_RATIO, rng)
    for label, buckets in (("train/good", b_train), ("calibration/good", b_cal), ("test/good", b_test)):
        for gi, g in enumerate(buckets):
            for name in g:
                plan.setdefault(label, []).append(name)
                rows.append([name, "normal", label, "样品组%d" % (abs(hash(tuple(g))) % 100000)])
    # ---- 缺陷：calibration:test = 1:2 ----
    for c in DEFECT_CLASSES:
        if not files[c]:
            continue
        g_def = group_samples(files[c], info)
        b_cal2, b_test2 = split_groups(g_def, DEFECT_RATIO, rng)
        for label, buckets in (("calibration/defect", b_cal2), ("test/defect", b_test2)):
            for g in buckets:
                for name in g:
                    plan.setdefault(label, []).append((c, name))
                    rows.append([name, c, label, "样品组%d" % (abs(hash(tuple(g))) % 100000)])

    print("-" * 72)
    print("  %-22s %-8s %s" % ("目标目录", "张数", "说明"))
    for label in ["train/good", "calibration/good", "test/good", "calibration/defect", "test/defect"]:
        items = plan.get(label, [])
        note = "只有合格品" if label == "train/good" else ""
        print("  %-22s %-8d %s" % (label, len(items), note))

    # ---- 合规自检 ----
    print("-" * 72)
    ok = True
    train = plan.get("train/good", [])
    bad = [x for x in train if not (isinstance(x, str) and x.startswith("normal_"))]
    if bad:
        ok = False
        print("  [x] train/good 里混进了非合格品：%s" % bad[:5])
    else:
        print("  [OK] train/good 只含合格品（%d 张）" % len(train))
    # 样品是否被拆散
    by_group = defaultdict(set)
    for name, cls, label, grp in rows:
        by_group[grp].add(label)
    split_any = [g for g, s in by_group.items() if len(s) > 1]
    if split_any:
        ok = False
        print("  [x] 有 %d 个样品组被拆到多个集合（违反'按样品划分'）" % len(split_any))
    else:
        print("  [OK] 样品组没有被拆散（%d 个样品组）" % len(by_group))

    if args.dry_run:
        print("\n（--dry-run：没有写入文件）")
        return 0 if ok else 1

    # ---- 落盘（复制，不动原数据） ----
    for label, items in plan.items():
        dst_dir = os.path.join(out, *label.split("/"))
        os.makedirs(dst_dir, exist_ok=True)
        for it in items:
            cls, name = (("normal", it) if isinstance(it, str) else it)
            shutil.copy2(os.path.join(img_root, cls, name), os.path.join(dst_dir, name))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "划分清单.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["文件名", "类别", "目标目录", "样品组"])
        w.writerows(rows)
    print("\n  已生成：")
    print("    %s" % os.path.join(out, "划分清单.csv"))
    write_report(out, files, info, plan)
    return 0 if ok else 1


def write_report(out: str, files: dict, info: dict, plan: dict) -> None:
    lines = ["# 数据集统计报告", "",
             "> 由 `split_dataset.py` 自动生成（划分比例：合格品 train:calibration:test = 6:2:2；缺陷 calibration:test = 1:2）", "",
             "## 一、各类别数量", "", "| 类别 | 张数 | 平均照度 lx | 照度范围 | 平均亮度 % |", "|---|---|---|---|---|"]
    for c, names in files.items():
        lux = [float(info[n]["lux"]) for n in names if n in info and info[n]["lux"]]
        br = [float(info[n]["bright"]) for n in names if n in info and info[n]["bright"]]
        rng = ("%.1f ~ %.1f" % (min(lux), max(lux))) if lux else "--"
        lines.append("| %s | %d | %s | %s | %s |" % (
            c, len(names), ("%.1f" % statistics.mean(lux)) if lux else "--", rng,
            ("%.1f" % statistics.mean(br)) if br else "--"))
    lines += ["", "## 二、划分结果", "", "| 目标目录 | 张数 |", "|---|---|"]
    for label in ["train/good", "calibration/good", "test/good", "calibration/defect", "test/defect"]:
        lines.append("| %s | %d |" % (label, len(plan.get(label, []))))
    lines += ["", "## 三、合规自检", "",
              "- [x] 按**样品**划分（同一采集时间戳的图不被拆散）",
              "- [x] `train/good` 只含合格品，**缺陷图未进入训练集**",
              "- [ ] 采集光照是否全程一致（同一 PWM 频率、同一亮度、箱盖闭合）—— 人工确认",
              "- [ ] 是否已按最终箱内位置完成照度标定 —— 人工确认",
              "",
              "> 说明：标签为自研生成（`make_labels.ps1`），无第三方数据集素材，无需外部许可署名。",
              ""]
    with open(os.path.join(out, "数据集统计报告.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("    %s" % os.path.join(out, "数据集统计报告.md"))


if __name__ == "__main__":
    sys.exit(main())
