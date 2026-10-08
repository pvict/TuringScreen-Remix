import asyncio
import datetime
import os
import time

import cv2
import libusb_package
from PIL import Image, ImageDraw, ImageFont
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
)
from turingscreencli import operations

os.chdir(os.path.dirname(os.path.abspath(__file__)))

VIDEO = "fundo.mp4"
FPS_ALVO = 5        # quadros por segundo enviados à tela
ESCURECER = 0.45    # 1.0 = sem escurecer; menor = mais escuro (ajuda a ler o texto)
LARGURA = 340       # largura máxima do texto, dentro do círculo


def brilho_para(agora):
    return 60


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
    if len(linhas) > max_linhas:
        linhas = linhas[:max_linhas - 1] + [" ".join(linhas[max_linhas - 1:])]
    return [cortar(d, l, f) for l in linhas]


def desenhar(fundo, titulo, artista):
    img = fundo.copy()
    d = ImageDraw.Draw(img)

    def escrever(x, y, txt, f, cor):
        d.text((x + 2, y + 2), txt, font=f, fill=(0, 0, 0), anchor="mt")  # sombra
        d.text((x, y), txt, font=f, fill=cor, anchor="mt")

    l_tit = quebrar(d, titulo, F_TITULO, 2)
    l_art = quebrar(d, artista, F_ARTISTA, 1) if artista else []
    total = len(l_tit) * 48 + (10 + len(l_art) * 36 if l_art else 0)
    y = 240 - total // 2
    for linha in l_tit:
        escrever(240, y, linha, F_TITULO, (255, 255, 255))
        y += 48
    y += 10
    for linha in l_art:
        escrever(240, y, linha, F_ARTISTA, (200, 200, 200))
        y += 36
    return img


def proximo_quadro(cap, pular):
    for _ in range(pular - 1):
        cap.grab()
    ok, frame = cap.read()
    if not ok:  # acabou o vídeo: volta ao início
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
        if not ok:
            return None
    h, w = frame.shape[:2]
    lado = min(h, w)
    y0, x0 = (h - lado) // 2, (w - lado) // 2
    frame = frame[y0:y0 + lado, x0:x0 + lado]
    frame = cv2.resize(frame, (480, 480))
    frame = cv2.convertScaleAbs(frame, alpha=ESCURECER)
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


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

    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        raise SystemExit(f"Não consegui abrir {VIDEO}")
    pular = max(1, round((cap.get(cv2.CAP_PROP_FPS) or 30) / FPS_ALVO))
    intervalo = 1 / FPS_ALVO

    brilho, musica, ultima_checagem = None, None, 0
    while True:
        inicio = time.time()

        novo = brilho_para(datetime.datetime.now())
        if novo != brilho:
            brilho = novo
            operations.send_brightness_command(dev, brilho)
        if brilho == 0:  # apagada: não gasta CPU nem USB
            await asyncio.sleep(30)
            continue

        if inicio - ultima_checagem > 2:
            musica = await musica_do_spotify(mgr)
            ultima_checagem = inicio

        quadro = proximo_quadro(cap, pular)
        if quadro is None:
            raise SystemExit("Não consegui ler o vídeo")
        if musica:
            quadro = desenhar(quadro, *musica)
        quadro.save("quadro.png")
        operations.send_image(dev, "quadro.png")

        await asyncio.sleep(max(0, intervalo - (time.time() - inicio)))


asyncio.run(main())