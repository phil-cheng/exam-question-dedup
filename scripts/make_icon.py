"""生成试题查重应用的 Windows 多尺寸图标。

图形：圆角绿底 + 两张重叠试卷 + 命中勾。颜色对齐界面「开始查重」按钮。
小尺寸会简化细节，避免 16px 糊成一团。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_ICO = ROOT / "assets" / "app.ico"
OUT_PNG = ROOT / "assets" / "app.png"

# 对齐界面主色 #2FA572，略加深保证任务栏上够醒目
GREEN_TOP = (36, 176, 118, 255)
GREEN_BOT = (24, 122, 82, 255)
PAPER_BACK = (214, 236, 224, 255)
PAPER_FRONT = (255, 255, 255, 255)
LINE = (168, 204, 186, 255)
BADGE = (18, 92, 60, 255)
CHECK = (255, 255, 255, 255)
SHADOW = (8, 40, 28, 70)


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _gradient_tile(size: int, radius: int) -> Image.Image:
    """竖向渐变圆角方块。"""
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pix = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    p = pix.load()
    for y in range(size):
        color = _lerp(GREEN_TOP, GREEN_BOT, y / max(size - 1, 1))
        for x in range(size):
            p[x, y] = color
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    base.paste(pix, (0, 0), mask)
    return base


def _paper(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, lines: bool) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    r = max(2, int(min(w, h) * 0.10))
    draw.rounded_rectangle(box, radius=r, fill=fill)
    if not lines:
        return
    pad_x = int(w * 0.16)
    # 三行题干线，最后一行短一点，像试卷文字
    for i, frac in enumerate((0.28, 0.46, 0.64)):
        ly = y0 + int(h * frac)
        lw = int(w * (0.68 if i < 2 else 0.42))
        thick = max(2, int(h * 0.045))
        draw.rounded_rectangle(
            (x0 + pad_x, ly, x0 + pad_x + lw, ly + thick),
            radius=thick // 2,
            fill=LINE,
        )


def _check(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    """实心圆 + 对勾。"""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BADGE)
    w = max(2, r // 4)
    # 对勾三个点：左、底、右上
    p1 = (cx - int(r * 0.48), cy + int(r * 0.02))
    p2 = (cx - int(r * 0.10), cy + int(r * 0.42))
    p3 = (cx + int(r * 0.50), cy - int(r * 0.36))
    draw.line([p1, p2, p3], fill=CHECK, width=w, joint="curve")


def _draw_at(canvas: int, detail: str) -> Image.Image:
    """在 canvas 像素上画一版，再缩到目标尺寸。"""
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    # 四周留一点透明边，任务栏小图标不会贴边
    margin = int(canvas * 0.04)
    tile = canvas - margin * 2
    radius = int(tile * 0.22)
    tile_img = _gradient_tile(tile, radius)

    # 轻投影，大尺寸才画
    if detail != "tiny":
        shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        off = max(2, canvas // 64)
        sd.rounded_rectangle(
            (margin + off, margin + off, margin + tile + off, margin + tile + off),
            radius=radius,
            fill=SHADOW,
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(1, canvas // 48)))
        img = Image.alpha_composite(img, shadow)

    img.paste(tile_img, (margin, margin), tile_img)
    d = ImageDraw.Draw(img)

    # 两张试卷：后一张偏右下，前一张居中偏左，表示左右对照
    inner = tile * 0.14
    left = margin + inner
    top = margin + inner * 1.05
    pw = tile * 0.44
    ph = tile * 0.56
    gap_x, gap_y = tile * 0.16, tile * 0.12
    back = (
        int(left + gap_x),
        int(top + gap_y),
        int(left + gap_x + pw),
        int(top + gap_y + ph),
    )
    front = (int(left), int(top), int(left + pw), int(top + ph))
    show_lines = detail == "full"
    _paper(d, back, PAPER_BACK, lines=False)
    _paper(d, front, PAPER_FRONT, lines=show_lines)

    if detail != "tiny":
        r = int(tile * (0.16 if detail == "full" else 0.18))
        cx = int(margin + tile * 0.76)
        cy = int(margin + tile * 0.76)
        _check(d, cx, cy, r)
    return img


def render(size: int) -> Image.Image:
    # 4 倍超采样再缩小，边缘更干净
    if size <= 24:
        detail = "tiny"
    elif size <= 48:
        detail = "simple"
    else:
        detail = "full"
    hi = _draw_at(size * 4, detail)
    return hi.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    sizes = (16, 24, 32, 48, 64, 128, 256)
    frames = [render(s) for s in sizes]
    frames[-1].save(OUT_PNG)
    # 必须以最大帧作主图；Pillow 会丢掉比主图更大的尺寸
    frames[-1].save(
        OUT_ICO,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    print(f"已写入 {OUT_ICO.relative_to(ROOT)} 与 {OUT_PNG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
