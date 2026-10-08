import libusb_package
from PIL import Image, ImageDraw, ImageFont
from turingscreencli import operations

img = Image.new("RGB", (480, 480), (15, 15, 20))
d = ImageDraw.Draw(img)
try:
    fonte = ImageFont.truetype("arial.ttf", 20)
except OSError:
    fonte = ImageFont.load_default()

c = 240
# círculos concêntricos: raio 240, 220, 200, ... , 120
cores = [(255, 80, 80), (255, 160, 60), (255, 230, 60), (80, 220, 100), (60, 200, 255), (160, 120, 255), (255, 120, 220)]
for i, r in enumerate(range(240, 100, -20)):
    d.ellipse((c - r, c - r, c + r, c + r), outline=cores[i % len(cores)], width=3)

# cruz central com marcas a cada 40 px
d.line((0, c, 479, c), fill=(90, 90, 90), width=1)
d.line((c, 0, c, 479), fill=(90, 90, 90), width=1)
for dist in range(40, 241, 40):
    d.text((c + dist, c + 4), str(dist), font=fonte, fill=(255, 255, 255), anchor="mt")
    d.text((c + 6, c - dist), str(dist), font=fonte, fill=(255, 255, 255), anchor="lm")

img.save("area.png")

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()
operations.send_brightness_command(dev, 50)
print("enviou:", operations.send_image(dev, "area.png"))