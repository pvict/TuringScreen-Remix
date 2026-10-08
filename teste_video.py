import time

import libusb_package
from turingscreencli import operations
from turingscreencli.transport import (
    build_command_packet_header,
    encrypt_command_packet,
    write_to_device,
)

ARQUIVO = "video_tela.mp4"
FPS = 30      # taxa de reprodução na tela; o vídeo foi gerado a 30
BRILHO = 60

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()

h264 = operations.extract_h264_from_mp4(ARQUIVO)  # usa o ffmpeg; cria video_tela.mp4.h264


def cmd(n):
    write_to_device(dev, encrypt_command_packet(build_command_packet_header(n)))


for n in (111, 112, 13):
    cmd(n)
operations.send_brightness_command(dev, BRILHO)
cmd(41)
operations.clear_image(dev)
operations.send_frame_rate_command(dev, FPS)

print("tocando... Ctrl+C para parar")
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
except KeyboardInterrupt:
    pass

cmd(123)
print("parado")