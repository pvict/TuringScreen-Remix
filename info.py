import libusb_package

d = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
print("Produto:", d.product, "| Fabricante:", d.manufacturer)
for cfg in d:
    for intf in cfg:
        print(f"Interface {intf.bInterfaceNumber} classe={hex(intf.bInterfaceClass)}")
        for ep in intf:
            direcao = "IN" if ep.bEndpointAddress & 0x80 else "OUT"
            print(f"  endpoint {hex(ep.bEndpointAddress)} {direcao} tamanho={ep.wMaxPacketSize}")