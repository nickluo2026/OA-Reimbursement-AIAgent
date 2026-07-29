"""生成一张仿真「增值税电子普通发票」PNG，用于 OCR + 全链路验证。
购买方/销售方/号码均不同于历史样本，验证修复的泛化能力。
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageFont

OUT = sys.argv[1] if len(sys.argv) > 1 else "scripts/test_assets/new_invoice_test.png"

# 候选中文字体（macOS）
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def load_font(size: int):
    for f in FONT_CANDIDATES:
        for idx in (0, 1):
            try:
                return ImageFont.truetype(f, size, index=idx)
            except Exception:
                continue
    return ImageFont.load_default()


W, H = 1600, 1000
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)

title = load_font(54)
big = load_font(34)
mid = load_font(28)
small = load_font(24)

# 标题
d.text((W // 2 - 320, 30), "增值税电子普通发票", font=title, fill="black")
# 发票代码 / 号码 / 日期（右上角机器码样式）
d.text((1180, 40), "发票代码 044001900211", font=small, fill="black")
d.text((1180, 75), "发票号码 25817000001876543210", font=small, fill="black")
d.text((1180, 110), "开票日期 2026-07-21", font=small, fill="black")

# 购买方
d.text((60, 180), "购买方", font=big, fill="black")
d.text((60, 230), "名称：杭州智云网络科技有限公司", font=mid, fill="black")
d.text((60, 275), "纳税人识别号：91330106MA2H3K9X7P", font=mid, fill="black")
d.text(
    (60, 320), "地址、电话：浙江省杭州市西湖区文三路 100 号 0571-88886666", font=mid, fill="black"
)
d.text((60, 365), "开户行及账号：招商银行杭州文三路支行 6214860210023344", font=mid, fill="black")

# 销售方
d.text((60, 450), "销售方", font=big, fill="black")
d.text((60, 500), "名称：上海阿斯兰航空服务有限公司", font=mid, fill="black")
d.text((60, 545), "纳税人识别号：91310115MA1K35Q2XY", font=mid, fill="black")
d.text((60, 590), "地址、电话：上海市浦东新区张杨路 500 号 021-50501234", font=mid, fill="black")
d.text(
    (60, 635), "开户行及账号：工商银行上海张杨路支行 6222021001234567890", font=mid, fill="black"
)

# 货物 / 金额区
d.text((60, 720), "货物或应税劳务、服务名称", font=small, fill="black")
d.text((60, 760), "机票款（北京-上海）", font=small, fill="black")
d.text((900, 760), "金额 1981.13  税率 9%  税额 178.29", font=small, fill="black")

# 价税合计
d.text((60, 830), "价税合计（大写）   贰仟壹佰伍拾玖元肆角贰分", font=mid, fill="black")
d.text((60, 880), "（小写）  ¥2159.42", font=big, fill="black")

# 红色印章（半透明，验证对比度增强抗干扰）
seal = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
sd = ImageDraw.Draw(seal)
sd.ellipse((10, 10, 190, 190), outline=(200, 0, 0, 160), width=6)
sd.text((55, 80), "发票专用章", font=small, fill=(200, 0, 0, 160))
seal = seal.rotate(18, expand=True)
img.paste(seal, (1150, 600), seal)

img.save(OUT, "PNG")
print("已生成测试发票:", OUT, "size=", img.size)
