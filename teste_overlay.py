import time

import libusb_package
from PIL import Image, ImageDraw, ImageFont
from turingscreencli import operations
from turingscreencli.transport import (
    build_command_packet_header,
    encrypt_command_packet,
    write_to_device,
)

ARQUIVO = "video_tela.mp4"
FPS = 30
BRILHO = 60
ENVIAR_NO_PEDACO = 15  # manda o texto depois de 15 pedaços de vídeo


def gerar_overlay():
    try:
        f1 = ImageFont.truetype("segoeuib.ttf", 44)
        f2 = ImageFont.truetype("segoeui.ttf", 30)
    except OSError:
        f1 = f2 = ImageFont.load_default()
    img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))  # fundo totalmente transparente
    d = ImageDraw.Draw(img)
    for (x, y, txt, f, cor) in [
        (240, 190, "Titulo da musica", f1, (255, 255, 255, 255)),
        (240, 250, "Nome do artista", f2, (210, 210, 210, 255)),
    ]:
        d.text((x + 2, y + 2), txt, font=f, fill=(0, 0, 0, 255), anchor="mt")  # sombra
        d.text((x, y), txt, font=f, fill=cor, anchor="mt")
    img.save("overlay.png")


gerar_overlay()

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()
h264 = operations.extract_h264_from_mp4(ARQUIVO)


def cmd(n):
    write_to_device(dev, encrypt_command_packet(build_command_packet_header(n)))


for n in (111, 112, 13):
    cmd(n)
operations.send_brightness_command(dev, BRILHO)
cmd(41)
operations.clear_image(dev)
operations.send_frame_rate_command(dev, FPS)

print("tocando... o texto vai ser enviado depois de alguns segundos. Ctrl+C para parar")
enviado = False
contador = 0
try:
    while True:
        with open(h264, "rb") as fh:
            while True:
                data = fh.read(202752)
                if not data:
                    break
                pacote = build_command_packet_header(121)
                pacote[8:12] = len(data).to_bytes(4, "big")
                resp = write_to_device(dev, encrypt_command_packet(pacote) + data)
                time.sleep(0.03)
                if resp is None or len(resp) < 9 or resp[8] <= 3:
                    operations.delay(dev, 2)

                contador += 1
                if contador == ENVIAR_NO_PEDACO and not enviado:
                    print("enviando overlay...")
                    print("resultado:", operations.send_image(dev, "overlay.png"))
                    enviado = True
except KeyboardInterrupt:
    pass

cmd(123)
print("parado")