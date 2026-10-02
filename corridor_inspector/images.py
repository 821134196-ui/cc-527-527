"""用 PNG 字节绘制占位巡查照片 (纯标准库 zlib 手写 PNG, 无需 Pillow)。

每种照片生成一张带标注的简单示意图: 楼道、物品框、风险色条、清晰度标记。
"""
from __future__ import annotations

import struct
import zlib

# 浅灰 / 深绿 / 消防红 与界面一致
BG = (240, 241, 242)
DARK_GREEN = (31, 90, 66)
FIRE_RED = (194, 32, 32)
INK = (51, 51, 51)
WHITE = (255, 255, 255)
BOX = (120, 120, 120)


def _png(width: int, height: int, rows: list[bytearray]) -> bytes:
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    comp = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", comp)
        + chunk(b"IEND", b"")
    )


class Canvas:
    def __init__(self, w: int = 480, h: int = 300, bg=BG):
        self.w, self.h = w, h
        self.px = bytearray()
        for _ in range(h):
            row = bytearray()
            for _ in range(w):
                row += bytes(bg)
            self.px.append(row) if False else None
        self.rows = [bytearray(bytes(bg) * w) for _ in range(h)]

    def rect(self, x0, y0, x1, y1, color, fill=True):
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                if fill or y in (y0, y1 - 1) or x in (x0, x1 - 1):
                    i = x * 3
                    self.rows[y][i:i + 3] = bytes(color)

    def stripe(self, color, y0=0, height=14):
        self.rect(0, y0, self.w, y0 + height, color)

    def text(self, x, y, s, color=INK):
        """极简 5x7 点阵字体, 仅支持 ASCII; 未知字符(如中文)画实心块示意。"""
        for ch in s:
            glyph = FONT.get(ch)
            if glyph is None:
                self.rect(x, y, x + 5, y + 7, color)
                x += 7
                continue
            for ry, bits in enumerate(glyph):
                for rx in range(5):
                    if bits & (1 << (4 - rx)):
                        if 0 <= y + ry < self.h and 0 <= x + rx < self.w:
                            i = (x + rx) * 3
                            self.rows[y + ry][i:i + 3] = bytes(color)
            x += 7

    def png_bytes(self) -> bytes:
        return _png(self.w, self.h, self.rows)


_F = {
    # 每个字符 7 行, 每行 5 位
    "A": [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    "B": [0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110],
    "C": [0b01111, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b01111],
    "D": [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110],
    "E": [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
    "F": [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
    "G": [0b01111, 0b10000, 0b10000, 0b10111, 0b10001, 0b10001, 0b01111],
    "H": [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    "I": [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b11111],
    "J": [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100],
    "K": [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001],
    "L": [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
    "M": [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
    "N": [0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001],
    "O": [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    "P": [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
    "Q": [0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101],
    "R": [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
    "S": [0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110],
    "T": [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
    "U": [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
    "V": [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100],
    "W": [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001],
    "X": [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001],
    "Y": [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100],
    "Z": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111],
    "0": [0b01110, 0b10011, 0b10101, 0b10101, 0b11001, 0b10001, 0b01110],
    "1": [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    "2": [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
    "3": [0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110],
    "4": [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
    "5": [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
    "6": [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
    "7": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
    "8": [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
    "9": [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
    " ": [0] * 7,
    "-": [0, 0, 0, 0b11111, 0, 0, 0],
    ".": [0, 0, 0, 0, 0, 0b00110, 0b00110],
    ":": [0, 0b00110, 0, 0, 0, 0b00110, 0],
    "/": [0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000],
    "?": [0b01110, 0b10001, 0b00001, 0b00110, 0b00100, 0b00000, 0b00100],
    "!": [0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00000, 0b00100],
    "#": [0b01010, 0b01010, 0b11111, 0b01010, 0b11111, 0b01010, 0b01010],
}
FONT = {k: v for k, v in _F.items()}


def make_photo(code: str, label: str, level: str, clear: bool = True,
               kind: str = "public") -> bytes:
    """生成一张占位照片。kind 影响标注: detail 带特征封条暗示, sealed 带封箱, damage 带裂痕。"""
    c = Canvas()
    color = {"high": FIRE_RED, "medium": (200, 130, 20), "low": DARK_GREEN}[level]
    c.stripe(color)
    c.text(10, 3, code)
    # 楼道透视示意: 两侧墙线
    c.rect(60, 90, 200, 250, BOX, fill=False)
    c.rect(280, 90, 420, 250, BOX, fill=False)
    c.rect(200, 60, 280, 250, INK, fill=False)
    # 物品框
    c.rect(150, 170, 250, 230, color, fill=False)
    c.rect(252, 170, 330, 230, color, fill=False)
    c.text(12, 28, label[:42])
    c.text(12, 40, f"RISK:{level.upper()}")
    if not clear:
        # 模糊: 画网格遮挡暗示照片不清
        for x in range(140, 340, 8):
            c.rect(x, 160, x + 1, 240, (170, 170, 170))
        for y in range(160, 240, 8):
            c.rect(140, y, 340, y + 1, (170, 170, 170))
        c.text(12, 52, "UNCLEAR PHOTO - NEED RESHOOT", FIRE_RED)
    if kind == "sealed":
        c.rect(150, 170, 330, 230, DARK_GREEN, fill=False)
        c.rect(148, 196, 332, 208, DARK_GREEN)
        c.text(220, 198, "SEALED", WHITE)
    elif kind == "damage":
        c.text(150, 175, "DAMAGE!", FIRE_RED)
        c.rect(230, 175, 236, 225, FIRE_RED)
        c.rect(180, 200, 236, 206, FIRE_RED)
        c.rect(236, 206, 300, 212, FIRE_RED)
    elif kind == "detail":
        c.text(12, 64, "DETAIL: NON-PUBLIC FEATURE", DARK_GREEN)
    return c.png_bytes()
