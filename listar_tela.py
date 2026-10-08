import logging

import libusb_package
from turingscreencli import operations

logging.basicConfig(level=logging.INFO, format="%(message)s")

dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
dev.set_configuration()

operations.send_refresh_storage_command(dev)
operations.send_list_storage_command(dev, "/tmp/sdcard/mmcblk0p1/video/")
operations.send_list_storage_command(dev, "/tmp/sdcard/mmcblk0p1/img/")