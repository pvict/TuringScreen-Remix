import asyncio
import datetime
import os
import threading
import time

import libusb_package
import usb.util
from PIL import Image, ImageDraw, ImageFont
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
)
from turingscreencli import operations
from turingscreencli.transport import (
    build_command_packet_header,
    encrypt_command_packet,
    write_to_device,
)

os.chdir(os.path.dirname(os.path.abspath(__file__)))

ARQUIVO = "video_tela.mp4"
FPS = 30
LARGURA = 340             # largura máxima do texto, dentro do círculo
ESCONDER_PAUSADO = True   # esconde o texto quando a música está pausada
FALHAS_MAX = 10           # respostas vazias seguidas antes de reconectar
ESPERA_RECONEXAO = 5      # segundos entre tentativas

parar = threading.Event()
estado = {"musica": None}


def log(msg):
    linha = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linha)
    try:
        with open("tela.log", "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def brilho_para(agora):
    h = agora.hour
    if 1 <= h < 7:
        return 0
    if h >= 22 or h < 1:
        return 20
    return 60


def fonte(nome, tamanho):
    try:
        return ImageFont.truetype(nome, tamanho)
    except OSError:
        return ImageFont.load_default()


F_TITULO = fonte("SFPRODISPLAYBOLD.otf", 36)
F_ARTISTA = fonte("SFPRODISPLAYBOLD.otf", 24)


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
        resto = " ".join(linhas[max_linhas - 1:])
        linhas = linhas[:max_linhas - 1] + [resto]
    return [cortar(d, l, f) for l in linhas]


def gerar_overlay(musica):
    img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))  # transparente
    if musica:
        titulo, artista = musica
        d = ImageDraw.Draw(img)
        l_tit = quebrar(d, titulo or "", F_TITULO, 2)
        l_art = []
        if artista:
            l_art = quebrar(d, artista, F_ARTISTA, 1)
        total = len(l_tit) * 48
        if l_art:
            total += 10 + len(l_art) * 36
        y = (240 - total // 2) + 150
        for linha in l_tit:
            d.text(
                (240, y), linha, font=F_TITULO, fill=(255, 255, 255, 255),
                anchor="mt", stroke_width=1, stroke_fill=(0, 0, 0, 255),
            )
            y += 48
        y += 10
        for linha in l_art:
            d.text(
                (240, y), linha, font=F_ARTISTA, fill=(220, 220, 220, 255),
                anchor="mt", stroke_width=1, stroke_fill=(0, 0, 0, 255),
            )
            y += 36
    img.save("overlay.png")


def sessao_usb():
    dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
    if dev is None:
        raise RuntimeError("tela não encontrada")
    try:
        dev.set_configuration()
        h264 = operations.extract_h264_from_mp4(ARQUIVO)

        def cmd(n):
            pacote = encrypt_command_packet(build_command_packet_header(n))
            return write_to_device(dev, pacote)

        for n in (111, 112, 13):
            cmd(n)
        brilho = brilho_para(datetime.datetime.now())
        operations.send_brightness_command(dev, brilho)
        cmd(41)
        operations.clear_image(dev)
        operations.send_frame_rate_command(dev, FPS)
        log("tela conectada")

        mostrada = "nenhuma"
        ultimo_check = time.time()
        falhas = 0
        while not parar.is_set():
            with open(h264, "rb") as fh:
                while not parar.is_set():
                    data = fh.read(202752)
                    if not data:
                        break
                    pacote = build_command_packet_header(121)
                    pacote[8:12] = len(data).to_bytes(4, "big")
                    carga = encrypt_command_packet(pacote) + data
                    resp = write_to_device(dev, carga)
                    time.sleep(0.03)

                    if resp is None:
                        falhas += 1
                        if falhas >= FALHAS_MAX:
                            raise RuntimeError("tela sem resposta")
                    else:
                        falhas = 0
                    if resp is None or len(resp) < 9 or resp[8] <= 3:
                        operations.delay(dev, 2)

                    agora = time.time()
                    if agora - ultimo_check >= 5:  # confere o horário
                        ultimo_check = agora
                        novo = brilho_para(datetime.datetime.now())
                        if novo != brilho:
                            brilho = novo
                            operations.send_brightness_command(dev, brilho)

                    musica = estado["musica"]
                    if musica != mostrada:  # a música mudou
                        mostrada = musica
                        gerar_overlay(musica)
                        operations.send_image(dev, "overlay.png")
        cmd(123)
    finally:
        usb.util.dispose_resources(dev)


def transmitir():
    while not parar.is_set():
        try:
            sessao_usb()
        except Exception as exc:
            log(f"erro: {exc}")
        if parar.is_set():
            break
        log(f"tentando reconectar em {ESPERA_RECONEXAO}s")
        parar.wait(ESPERA_RECONEXAO)


async def musica_do_spotify(mgr):
    for s in mgr.get_sessions():
        if "spotify" in s.source_app_user_model_id.lower():
            if ESCONDER_PAUSADO:
                status = int(s.get_playback_info().playback_status)
                if status != 4:  # 4 = tocando
                    return None
            info = await s.try_get_media_properties_async()
            return info.title, info.artist
    return None


async def vigiar_spotify():
    mgr = await MediaManager.request_async()
    while not parar.is_set():
        try:
            estado["musica"] = await musica_do_spotify(mgr)
        except Exception:
            pass
        await asyncio.sleep(2)


def main():
    t = threading.Thread(target=transmitir, daemon=True)
    t.start()
    try:
        asyncio.run(vigiar_spotify())
    except KeyboardInterrupt:
        pass
    parar.set()
    t.join(timeout=5)
    log("parado")


main()