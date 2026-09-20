#!/usr/bin/env python3
"""生成 PWA 图标（PNG）与 OG 图。

设计以 frontend/public/favicon.svg 为准，做两种版式：

1. any（icon-192 / icon-512）：图形铺满画布，与 favicon 视觉一致。
2. maskable（icon-maskable-512）：Android 会把图标裁成圆形/水滴等形状，
   必须把主体缩到中心 80% 的安全区内（外圈留同色底），否则会被裁掉边缘。

用 SVG 模板 + cairosvg 渲染，保证矢量清晰度；脚本可重复执行，产物确定。
"""
from __future__ import annotations

import sys
from pathlib import Path

import cairosvg

PUBLIC = Path(__file__).resolve().parent.parent / "public"

# 品牌图标的矢量描述（与 favicon.svg 同源）：
# 黑底方块 + 克莱因蓝描边方块 + 白色下载箭头与横线
BLUE = "#2440fb"
BLACK = "#111111"


def icon_svg(size: int, *, scale: float = 1.0, bg: str | None = None) -> str:
    """按比例缩放绘制图标主体；scale<1 时四周留出背景色（给 maskable 用安全边距）。"""
    s = size
    # favicon 原始坐标系是 64x64，这里按目标尺寸等比换算
    k = s / 64.0 * scale
    off = (s - 64 * k) / 2.0  # 居中偏移

    rect = f'<rect width="{s}" height="{s}" fill="{bg}"/>' if bg else ""

    # 元素坐标来自 favicon.svg，先乘 k 再平移 off
    def x(v: float) -> float:
        return off + v * k

    def w(v: float) -> float:
        return v * k

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" width="{s}" height="{s}">
  {rect}
  <rect x="{x(11)}" y="{x(11)}" width="{w(44)}" height="{w(44)}" rx="{w(7)}" fill="{BLACK}"/>
  <rect x="{x(7)}" y="{x(7)}" width="{w(44)}" height="{w(44)}" rx="{w(7)}"
        fill="{BLUE}" stroke="{BLACK}" stroke-width="{w(4)}"/>
  <g fill="#ffffff">
    <rect x="{x(25.5)}" y="{x(13)}" width="{w(7)}" height="{w(14)}" rx="{w(1.5)}"/>
    <polygon points="{x(19)},{x(26)} {x(41)},{x(26)} {x(30)},{x(38)}"/>
    <rect x="{x(19)}" y="{x(41)}" width="{w(22)}" height="{w(5)}" rx="{w(1.5)}"/>
  </g>
</svg>'''


def render(svg: str, out: Path, size: int) -> None:
    cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        write_to=str(out),
        output_width=size,
        output_height=size,
    )
    print(f"  ✓ {out.name}  ({size}x{size}, {out.stat().st_size // 1024} KB)")


def main() -> int:
    if not PUBLIC.is_dir():
        print(f"找不到目录: {PUBLIC}", file=sys.stderr)
        return 1

    print("生成 PWA 图标（any，铺满画布）:")
    render(icon_svg(192), PUBLIC / "icon-192.png", 192)
    render(icon_svg(512), PUBLIC / "icon-512.png", 512)

    print("生成 PWA 图标（maskable，主体缩到中心 80% 安全区）:")
    # 0.8 缩放：Android 自适应图标的安全区约为直径 80%，超出部分可能被裁掉
    render(
        icon_svg(512, scale=0.8, bg=BLUE),
        PUBLIC / "icon-maskable-512.png",
        512,
    )

    print("生成 apple-touch-icon（iOS 添加到主屏）：")
    # iOS 不接受 maskable，用铺满版即可，会被系统自己加圆角
    render(icon_svg(180), PUBLIC / "apple-touch-icon.png", 180)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
