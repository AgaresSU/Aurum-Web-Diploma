from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFilter, ImageFont

IMAGE_SIZE = (1080, 560)
BRAND_GREEN = (5, 24, 15)
DEEP_GREEN = (8, 42, 26)
CARD_GREEN = (14, 44, 30)
GOLD = (232, 198, 106)
GOLD_DARK = (184, 139, 54)
CREAM = (255, 248, 231)
MUTED = (185, 205, 190)
INK = (4, 18, 12)
WHITE = (255, 255, 255)


def _font_paths():
    return {
        "regular": [
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ],
        "bold": [
            Path("C:/Windows/Fonts/seguisb.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ],
    }


def _font(size, bold=False):
    key = "bold" if bold else "regular"
    for path in _font_paths()[key]:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _money(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0.00")
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def _draw_text(draw, xy, text, font, fill, anchor=None):
    draw.text(xy, str(text or ""), font=font, fill=fill, anchor=anchor)


def _draw_wrapped(draw, text, xy, font, fill, max_width, line_gap=8):
    words = str(text or "").split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    x, y = xy
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + line_gap
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _rounded_layer(size, radius, fill):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=fill)
    return layer


def _shadow(size, radius, alpha=90, blur=18):
    layer = _rounded_layer(size, radius, (0, 0, 0, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _paste_logo(image, position, size):
    logo_path = settings.WEBSITE_STATIC_DIR / "brand" / "aurumweb-mark-squircle-new.png"
    if not logo_path.exists():
        draw = ImageDraw.Draw(image)
        x, y = position
        draw.rounded_rectangle((x, y, x + size, y + size), radius=18, outline=GOLD, width=3, fill=(4, 31, 19))
        draw.text((x + size / 2, y + size / 2), "AW", font=_font(28, bold=True), fill=GOLD, anchor="mm")
        return

    logo = Image.open(logo_path).convert("RGBA")
    logo.thumbnail((size, size), Image.Resampling.LANCZOS)
    image.alpha_composite(logo, position)


def _draw_background(draw):
    width, height = IMAGE_SIZE
    for y in range(height):
        ratio = y / height
        r = int(BRAND_GREEN[0] * (1 - ratio) + DEEP_GREEN[0] * ratio)
        g = int(BRAND_GREEN[1] * (1 - ratio) + DEEP_GREEN[1] * ratio)
        b = int(BRAND_GREEN[2] * (1 - ratio) + DEEP_GREEN[2] * ratio)
        draw.line((0, y, width, y), fill=(r, g, b))
    for x in range(0, width, 72):
        draw.line((x, 0, x, height), fill=(22, 62, 41), width=1)
    for y in range(0, height, 72):
        draw.line((0, y, width, y), fill=(22, 62, 41), width=1)
    draw.ellipse((650, -120, 1180, 420), fill=(18, 82, 51))
    draw.ellipse((-220, 260, 420, 780), fill=(6, 51, 31))


def _draw_receipt_card(base, invoice, payment, paid_at):
    receipt_pos = (640, 44)
    receipt_size = (380, 472)
    shadow = _shadow(receipt_size, radius=24, alpha=110, blur=20)
    base.alpha_composite(shadow, (receipt_pos[0] + 16, receipt_pos[1] + 18))

    receipt = _rounded_layer(receipt_size, radius=24, fill=(255, 251, 239, 255))
    draw = ImageDraw.Draw(receipt)
    draw.rounded_rectangle((18, 18, 362, 454), radius=18, outline=(214, 190, 135), width=2)
    draw.rectangle((20, 24, 360, 96), fill=(245, 232, 195))

    _draw_text(draw, (190, 46), "AURUMWEB", _font(25, True), INK, anchor="mm")
    _draw_text(draw, (190, 74), "квитанция об оплате", _font(15), (72, 83, 74), anchor="mm")

    rows = [
        ("Статус", "Оплачено"),
        ("Счет", f"#{invoice.pk}"),
        ("Платеж", f"#{payment.pk}"),
        ("Услуга", invoice.title or "Консультация"),
        ("Сумма", f"{_money(payment.amount)} ₽"),
        ("Дата", paid_at),
    ]
    y = 128
    for label, value in rows:
        _draw_text(draw, (40, y), label, _font(13, True), (89, 93, 84))
        y = _draw_wrapped(draw, value, (132, y - 2), _font(16, True), INK, max_width=198, line_gap=0)
        y += 8
        draw.line((40, y, 340, y), fill=(218, 206, 176), width=1)
        y += 14

    draw.rounded_rectangle((48, 394, 332, 438), radius=14, fill=(7, 37, 23))
    _draw_text(draw, (190, 416), "СПАСИБО ЗА ОПЛАТУ", _font(17, True), GOLD, anchor="mm")

    for x in range(36, 346, 22):
        draw.ellipse((x, 452, x + 12, 464), fill=(8, 39, 25))

    base.alpha_composite(receipt, receipt_pos)


def build_quick_consultation_receipt_png(consultation, payment):
    invoice = payment.invoice
    paid_at = timezone.localtime(payment.paid_at or timezone.now()).strftime("%d.%m.%Y %H:%M")

    image = Image.new("RGBA", IMAGE_SIZE, BRAND_GREEN + (255,))
    draw = ImageDraw.Draw(image)
    _draw_background(draw)

    _paste_logo(image, (64, 54), 84)
    _draw_text(draw, (164, 76), "AurumWeb", _font(30, True), GOLD)
    _draw_text(draw, (164, 112), "AurumWeb Fast", _font(18), MUTED)

    _draw_text(draw, (64, 214), "Квитанция", _font(68, True), CREAM)
    _draw_text(draw, (64, 286), "об оплате", _font(68, True), CREAM)
    _draw_text(draw, (68, 382), "Оплата прошла успешно.", _font(26), MUTED)

    badge = _rounded_layer((420, 64), radius=18, fill=(232, 198, 106, 255))
    badge_draw = ImageDraw.Draw(badge)
    _draw_text(
        badge_draw,
        (210, 33),
        f"Сумма: {_money(payment.amount)} ₽",
        _font(24, True),
        INK,
        anchor="mm",
    )
    image.alpha_composite(badge, (66, 452))

    _draw_receipt_card(image, invoice, payment, paid_at)

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
