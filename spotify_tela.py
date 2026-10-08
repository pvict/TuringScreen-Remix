import asyncio

import libusb_package
from PIL import Image, ImageDraw, ImageFont
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
)
from turingscreencli import operations

LARGURA = 340  # largura máxima do texto, dentro do círculo


def fonte(nome, tamanho):
    try:
        return ImageFont.truetype(nome, tamanho)
    except OSError:
        return ImageFont.load_default()


F_TITULO = fonte("segoeuib.ttf", 40)
F_ARTISTA = fonte("segoeui.ttf", 28)


def cortar(d, texto, f):
    if d.textlength(texto, font=f) <= LARGURA:
        return texto
    while len(texto) > 1 and d.textlength(texto + "…", font=f) > LARGURA:
        texto = texto[:-1]
    return texto + "…"


def quebrar(d, texto, f, max_linhas):
    linhas, atual = [], ""
    for palavra in texto.split():
        teste = (atual + " " + palavra).strip()
        if d.textlength(teste, font=f) <= LARGURA:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    linhas = linhas[:max_linhas] if len(linhas) <= max_linhas else linhas[:max_linhas - 1] + [" ".join(linhas[max_linhas - 1:])]
    return [cortar(d, l, f) for l in linhas]


def gerar(titulo, artista):
    img = Image.new("RGB", (480, 480), (0, 0, 0))
    d = ImageDraw.Draw(img)
    l_tit = quebrar(d, titulo, F_TITULO, 2)
    l_art = quebrar(d, artista, F_ARTISTA, 1) if artista else []
    total = len(l_tit) * 48 + (10 + len(l_art) * 36 if l_art else 0)
    y = 240 - total // 2
    for linha in l_tit:
        d.text((240, y), linha, font=F_TITULO, fill=(255, 255, 255), anchor="mt")
        y += 48
    y += 10
    for linha in l_art:
        d.text((240, y), linha, font=F_ARTISTA, fill=(160, 160, 160), anchor="mt")
        y += 36
    img.save("musica.png")


async def musica_do_spotify(mgr):
    for s in mgr.get_sessions():
        if "spotify" in s.source_app_user_model_id.lower():
            info = await s.try_get_media_properties_async()
            return info.title, info.artist
    return None


async def main():
    mgr = await MediaManager.request_async()
    dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
    dev.set_configuration()
    operations.send_brightness_command(dev, 50)

    ultima = "inicio"
    while True:
        atual = await musica_do_spotify(mgr)
        if atual != ultima:  # só redesenha quando a música muda
            ultima = atual
            titulo, artista = atual if atual else ("Nada tocando", "")
            gerar(titulo, artista)
            operations.send_image(dev, "musica.png")
        await asyncio.sleep(2)


asyncio.run(main())