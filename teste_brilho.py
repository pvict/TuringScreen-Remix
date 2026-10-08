import logging
import time

import libusb_package
from turingscreencli import operations

logging.basicConfig(level=logging.INFO)

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()

for valor in (20, 90, 50):
    print("brilho", valor)
    resp = operations.send_brightness_command(dev, valor)
    print("resposta:", resp)
    time.sleep(3)