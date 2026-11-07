"""Build a fake pass-camera photo: printed food cards (with their QR markers) on a counter, slightly rotated."""
import io, sys
import segno
from PIL import Image, ImageDraw, ImageFilter

def card(item, size=420):
    c = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, size - 1, size - 1], outline="#bbbbbb", width=3)
    d.text((24, 20), item.title(), fill="black")
    buf = io.BytesIO(); segno.make("HH:" + item.upper(), error="m").save(buf, kind="png", scale=9, border=2)
    q = Image.open(buf).convert("RGB").resize((150, 150))
    c.paste(q, (size - 170, size - 170))
    return c

def photo(items, out, w=1280, h=960):
    img = Image.new("RGB", (w, h), (92, 88, 82))
    for i, it in enumerate(items):
        c = card(it).rotate([-7, 5, -3, 9][i % 4], expand=True, fillcolor=(92, 88, 82))
        img.paste(c, (60 + (i % 3) * 400, 80 + (i // 3) * 440))
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    img.save(out, "JPEG", quality=80)

if __name__ == "__main__":
    photo(sys.argv[2:], sys.argv[1])
