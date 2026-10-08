import time

import libusb_package
from PIL import Image, ImageDraw
from turingscreencli import operations

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()
operations.send_brightness_command(dev, 50)

N = 40
t0 = time.time()
for i in range(N):
    img = Image.new("RGB", (480, 480), (0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 40 + (i * 10) % 400
    d.ellipse((x - 40, 200, x + 40, 280), fill=(0, 200, 255))
    img.save("quadro.png")
    operations.send_image(dev, "quadro.png")
dt = time.time() - t0
print(f"{N} quadros em {dt:.1f}s = {N / dt:.1f} quadros/s")