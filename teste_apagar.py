import time

import libusb_package
from turingscreencli import operations

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()

print("apagando...")
operations.send_brightness_command(dev, 0)
time.sleep(5)

print("voltando para 50...")
operations.send_brightness_command(dev, 50)