"""生成应用图标 app.ico：靛蓝→紫渐变圆角方块 + 白色播放键。"""
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角矩形遮罩
radius = 56
mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)

# 垂直渐变 #6366f1 -> #8b5cf6
top = (99, 102, 241)
bot = (139, 92, 246)
grad = Image.new("RGBA", (1, S))
for y in range(S):
    t = y / (S - 1)
    r = int(top[0] + (bot[0] - top[0]) * t)
    g = int(top[1] + (bot[1] - top[1]) * t)
    b = int(top[2] + (bot[2] - top[2]) * t)
    grad.putpixel((0, y), (r, g, b, 255))
grad = grad.resize((S, S))
img.paste(grad, (0, 0), mask)

# 白色播放三角（居中，略偏右）
cx, cy = S // 2, S // 2 + 4
w, h = 78, 92
d = ImageDraw.Draw(img)
# 圆角播放按钮：用三角
tri = [(cx - 28, cy - h // 2), (cx - 28, cy + h // 2), (cx + w, cy)]
d.polygon(tri, fill=(255, 255, 255, 245))

# 高光：顶部一条细白弧（简单用半透明圆角条）
hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
hd = ImageDraw.Draw(hl)
hd.rounded_rectangle([28, 18, S - 28, 40], radius=11, fill=(255, 255, 255, 60))
img = Image.alpha_composite(img, hl)

out = r"D:\Files\video-dewatermark\backend\app.ico"
# 多尺寸保存为 ico
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("saved", out)
