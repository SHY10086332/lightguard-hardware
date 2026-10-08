# -*- coding: utf-8 -*-
"""
dataset_manager.py —— 数据集采集管理模块（独立采集台与"可选软件接入"共用）

设计原则
--------
1. **完全独立**：不 import app.py 里的任何东西，可以单独跑、单独测。
   接入时 app.py 只需要在末尾追加几条路由，不碰原有逻辑。
2. **只做三件事**：保存样本图片、维护采集记录 CSV、统计各类别数量。
3. **目录结构**（存在软件目录下的 dataset\\）：
       dataset\\
       ├── images\\
       │   ├── normal\\    normal_0001.jpg  normal_0002.jpg …
       │   ├── brokenline\\ …
       │   └── …
       └── 采集记录表.csv    文件名,类别,缺陷类型,照度lx,亮度%,时间

用法（接入示例）
----------------
    from dataset_manager import DatasetManager, CLASSES
    dm = DatasetManager(os.path.join(os.path.dirname(__file__), "dataset"))
    dm.save("normal", image_bytes, lux=410.8, brightness=25)
    dm.status()          # {"normal": 12, "brokenline": 3, ..., "total": 15}
    dm.delete_last("normal")
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 类别定义：与 make_labels.ps1 / 硬件演示库保持一致
CLASSES: List[Tuple[str, str]] = [
    ("normal",     "正常（合格品）"),
    ("brokenline", "断线"),
    ("faint",      "缺墨"),
    ("stain",      "污点"),
    ("scratch",    "划痕"),
    ("offset",     "偏位重影"),
]
CLASS_KEYS = [k for k, _ in CLASSES]
CLASS_LABELS = dict(CLASSES)

CSV_HEADER = ["文件名", "类别", "缺陷类型", "照度lx", "亮度%", "时间"]
_NAME_RE = re.compile(r"^([a-z0-9]+)_(\d{4,})\.jpg$", re.IGNORECASE)


class DatasetManager:
    """数据集目录 + 记录表的管理器（线程安全由调用方保证；单用户本地使用足够）"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.images_dir = self.root / "images"
        self.csv_path = self.root / "采集记录表.csv"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_csv()

    # ---------------- 内部 ----------------
    def _ensure_csv(self) -> None:
        if not self.csv_path.exists():
            # utf-8-sig：带 BOM，Excel 双击打开中文不乱码
            with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow(CSV_HEADER)

    def _read_rows(self) -> List[List[str]]:
        if not self.csv_path.exists():
            return [list(CSV_HEADER)]
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if r]
        if not rows:
            rows = [list(CSV_HEADER)]
        return rows

    def _write_rows(self, rows: List[List[str]]) -> None:
        with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)

    def _check_class(self, cls: str) -> str:
        cls = (cls or "").strip().lower()
        if cls not in CLASS_KEYS:
            raise ValueError(f"未知类别 {cls!r}，可用：{', '.join(CLASS_KEYS)}")
        return cls

    # ---------------- 对外 ----------------
    def class_dir(self, cls: str) -> Path:
        d = self.images_dir / self._check_class(cls)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def count(self, cls: str) -> int:
        d = self.images_dir / cls
        if not d.is_dir():
            return 0
        return sum(1 for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".jpg")

    def next_index(self, cls: str) -> int:
        """下一个序号 = 当前已有张数 + 1（序号会接着已有的往下排）"""
        return self.count(cls) + 1

    def save(
        self,
        cls: str,
        image_bytes: bytes,
        lux: Optional[float] = None,
        brightness: Optional[int] = None,
    ) -> Dict[str, Any]:
        """保存一张样本：写图片 + 追加一行记录"""
        cls = self._check_class(cls)
        if not image_bytes:
            raise ValueError("图片内容为空")

        idx = self.next_index(cls)
        name = f"{cls}_{idx:04d}.jpg"
        path = self.class_dir(cls) / name
        path.write_bytes(image_bytes)

        # 缺陷类型用中文（和「无」同一语言，Excel 里直接看得懂）；类别列仍是英文 key，与目录名一致
        defect = "无" if cls == "normal" else CLASS_LABELS[cls]
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = self._read_rows()
        rows.append([
            name, cls, defect,
            "" if lux is None else f"{float(lux):.1f}",
            "" if brightness is None else str(int(brightness)),
            ts,
        ])
        self._write_rows(rows)

        return {
            "ok": True,
            "file": name,
            "class": cls,
            "class_label": CLASS_LABELS[cls],
            "index": idx,
            "count": self.count(cls),
            "lux": None if lux is None else round(float(lux), 1),
            "brightness": brightness,
            "time": ts,
        }

    def delete_last(self, cls: str) -> Dict[str, Any]:
        """撤销上一张（放错标签、拍糊了时用）"""
        cls = self._check_class(cls)
        d = self.images_dir / cls
        if not d.is_dir():
            return {"ok": False, "error": "该类别还没有样本"}

        files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".jpg"]
        if not files:
            return {"ok": False, "error": "该类别还没有样本"}

        def seq(p: Path) -> int:
            m = _NAME_RE.match(p.name)
            return int(m.group(2)) if m else 0

        victim = max(files, key=seq)
        victim.unlink()

        rows = self._read_rows()
        rows = [r for r in rows if not (len(r) >= 1 and r[0] == victim.name)]
        self._write_rows(rows)

        return {"ok": True, "deleted": victim.name, "count": self.count(cls)}

    def status(self) -> Dict[str, Any]:
        """各类别数量 + 总数 + 最近一张的信息"""
        per_class = {k: self.count(k) for k in CLASS_KEYS}
        total = sum(per_class.values())

        last = None
        rows = self._read_rows()
        for r in reversed(rows[1:]):
            if len(r) >= 6:
                last = {"file": r[0], "class": r[1], "lux": r[3], "brightness": r[4], "time": r[5]}
                break

        return {
            "per_class": per_class,
            "labels": CLASS_LABELS,
            "total": total,
            "last": last,
            "root": str(self.root),
            "csv": str(self.csv_path),
        }

    def recent_files(self, limit: int = 8) -> List[Dict[str, Any]]:
        """最近采集的若干张（按序号倒序，跨类别）"""
        items: List[Tuple[int, Path]] = []
        for k in CLASS_KEYS:
            d = self.images_dir / k
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if p.is_file() and p.suffix.lower() == ".jpg":
                    m = _NAME_RE.match(p.name)
                    items.append((int(m.group(2)) if m else 0, p))
        items.sort(key=lambda t: (t[1].stat().st_mtime, t[0]), reverse=True)
        return [
            {"file": p.name, "class": p.parent.name,
             "size_kb": round(p.stat().st_size / 1024, 1),
             "time": datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M:%S")}
            for _, p in items[:limit]
        ]

    def export_zip(self) -> bytes:
        """把整个数据集打包成 zip（返回字节，供页面下载后交给训练脚本）"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for k in CLASS_KEYS:
                d = self.images_dir / k
                if not d.is_dir():
                    continue
                for p in sorted(d.iterdir()):
                    if p.is_file():
                        z.write(p, arcname=f"images/{k}/{p.name}")
            if self.csv_path.exists():
                z.write(self.csv_path, arcname=self.csv_path.name)
        return buf.getvalue()
