import libusb_package
from PIL import Image, ImageDraw, ImageFont
from turingscreencli import operations

# Gera uma imagem de teste 480x480
img = Image.new("RGB", (480, 480), (20, 20, 30))
d = ImageDraw.Draw(img)
d.rectangle((0, 0, 479, 479), outline=(0, 200, 255), width=10)  # borda: mostra se corta
d.rectangle((0, 0, 60, 60), fill=(255, 60, 60))                 # quadrado vermelho = canto superior esquerdo
try:
    fonte = ImageFont.truetype("arial.ttf", 90)
except OSError:
    fonte = ImageFont.load_default()
d.text((240, 240), "TESTE", font=fonte, fill=(255, 255, 255), anchor="mm")
img.save("teste.png")

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()

operations.send_brightness_command(dev, 50)
ok = operations.send_image(dev, "teste.png")
print("enviou:", ok)