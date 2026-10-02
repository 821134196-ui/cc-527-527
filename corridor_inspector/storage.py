"""本地图片存储: 复制/落盘到 data 目录, 返回相对路径。"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


class ImageStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, data: bytes, suffix: str = ".png") -> str:
        digest = hashlib.sha256(data).hexdigest()[:16]
        name = f"{digest}{suffix}"
        target = self.root / name
        if not target.exists():
            target.write_bytes(data)
        return name

    def save_file(self, src: str | Path) -> str:
        src = Path(src)
        return self.save_bytes(src.read_bytes(), src.suffix or ".png")

    def path(self, name: str) -> Path:
        return self.root / name
