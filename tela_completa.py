import asyncio
import datetime
import io
import json
import math
import os
import re
import threading
import time
import urllib.request

import libusb_package
import usb.util
from PIL import Image, ImageDraw, ImageFont
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions
from turingscreencli import operations
from turingscreencli.transport import (
    build_command_packet_header,
    encrypt_command_packet,
    write_to_device,
)

os.chdir(os.path.dirname(os.path.abspath(__file__)))

ARQUIVO = "video_tela.mp4"
FPS = 30
LARGURA = 300             # largura máxima do texto
TAM_CAPA = 150            # diâmetro da capa do álbum
ESCONDER_PAUSADO = True
FALHAS_MAX = 10
ESPERA_RECONEXAO = 5
ATUALIZA_A_CADA = 2       # segundos entre atualizações dos anéis e do texto
URL_SENSORES = "http://localhost:8085/data.json"

# (rótulo, trecho do nome no LibreHardwareMonitor,
#  limites amarelo/laranja/vermelho em graus, ângulo inicial do arco)
SENSORES = [
    ("CPU", "Tctl", (65, 75, 85), 195),
    ("GPU", "GPU Core", (72, 80, 86), 255),
    ("SSD", "Composite", (55, 65, 70), 315),
]
TRILHA = (255, 255, 255, 60)
GRAU = "\u00b0"

parar = threading.Event()
estado = {
    "musica": None, "capa": None, "pos": 0.0, "dur": 0.0,
    "t_poll": 0.0, "tocando": False, "temps": {},
}


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


F_TITULO = fonte("segoeuib.ttf", 34)
F_ARTISTA = fonte("segoeui.ttf", 24)
F_SENSOR = fonte("segoeuib.ttf", 20)


def cortar(d, texto, f):
    if d.textlength(texto, font=f) <= LARGURA:
        return texto
    while len(texto) > 1 and d.textlength(texto + "…", font=f) > LARGURA:
        texto = texto[:-1]
    return texto + "…"


def txt(d, x, y, texto, f, cor, ancora="mt"):
    d.text(
        (x, y), texto, font=f, fill=cor, anchor=ancora,
        stroke_width=3, stroke_fill=(0, 0, 0, 255),
    )


def cor_temp(v, limites):
    amarelo, laranja, vermelho = limites
    if v >= vermelho:
        return (255, 70, 70, 255)
    if v >= laranja:
        return (255, 150, 40, 255)
    if v >= amarelo:
        return (255, 220, 60, 255)
    return (80, 220, 120, 255)


def preparar_capa(dados):
    img = Image.open(io.BytesIO(dados)).convert("RGB")
    lado = min(img.size)
    x0 = (img.width - lado) // 2
    y0 = (img.height - lado) // 2
    img = img.crop((x0, y0, x0 + lado, y0 + lado))
    img = img.resize((TAM_CAPA, TAM_CAPA), Image.LANCZOS)
    grande = TAM_CAPA * 4
    mascara = Image.new("L", (grande, grande), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, grande - 1, grande - 1), fill=255)
    mascara = mascara.resize((TAM_CAPA, TAM_CAPA), Image.LANCZOS)
    saida = img.convert("RGBA")
    saida.putalpha(mascara)
    return saida


def renderizar(snap):
    img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
    big = Image.new("RGBA", (960, 960), (0, 0, 0, 0))  # anéis em 2x (suaviza)
    bd = ImageDraw.Draw(big)

    def arco(raio, larg, ini, fim, cor):
        if fim > ini:
            r = raio * 2
            bd.arc(
                (480 - r, 480 - r, 480 + r, 480 + r), ini, fim,
                fill=cor, width=larg * 2,
            )

    temps = snap["temps"]
    alarme = False
    for rotulo, _, limites, ang0 in SENSORES:
        if rotulo in temps:
            v = temps[rotulo]
            frac = min(max(v / 100, 0), 1)
            arco(205, 6, ang0, ang0 + 50, TRILHA)
            arco(205, 6, ang0, ang0 + 50 * frac, cor_temp(v, limites))
            if v >= limites[2]:
                alarme = True

    musica = snap["musica"]
    vermelho = (255, 70, 70, 255)
    if musica and snap["dur"] > 0:
        pos = snap["pos"]
        if snap["tocando"]:
            pos += time.monotonic() - snap["t_poll"]
        frac = min(max(pos / snap["dur"], 0), 1)
        ang = int(360 * frac / 3) * 3
        arco(226, 5, 0, 360, TRILHA)
        arco(226, 5, -90, -90 + ang, vermelho if alarme else (30, 215, 96, 255))
    elif alarme:
        arco(226, 5, 0, 360, vermelho)
    img.alpha_composite(big.resize((480, 480), Image.LANCZOS))

    d = ImageDraw.Draw(img)
    for rotulo, _, limites, ang0 in SENSORES:
        if rotulo in temps:
            rad = math.radians(ang0 + 25)
            x = 240 + 170 * math.cos(rad)
            y = 240 + 170 * math.sin(rad)
            texto = f"{rotulo} {temps[rotulo]:.0f}{GRAU}"
            txt(d, x, y, texto, F_SENSOR, cor_temp(temps[rotulo], limites), "mm")

    if musica:
        titulo, artista = musica
        capa = snap["capa"]
        if capa is not None:
            img.alpha_composite(capa, (240 - TAM_CAPA // 2, 115))
            y = 280
        else:
            y = 205
        txt(d, 240, y, cortar(d, titulo or "", F_TITULO), F_TITULO,
            (255, 255, 255, 255))
        if artista:
            txt(d, 240, y + 44, cortar(d, artista, F_ARTISTA), F_ARTISTA,
                (220, 220, 220, 255))
    return img


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

        ultimo_check = time.time()
        ultimo_overlay = 0.0
        ultimo_hash = None
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

                    if agora - ultimo_overlay >= ATUALIZA_A_CADA:
                        ultimo_overlay = agora
                        img = renderizar(dict(estado))
                        h = hash(img.tobytes())
                        if h != ultimo_hash:  # só envia se mudou
                            ultimo_hash = h
                            img.save("overlay.png")
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


def percorrer(no):
    for filho in no.get("Children", []):
        yield from percorrer(filho)
    valor = no.get("Value", "")
    if GRAU + "C" in valor:
        m = re.search(r"-?\d+(?:[.,]\d+)?", valor)
        if m:
            yield no.get("Text", ""), float(m.group().replace(",", "."))


def vigiar_sensores():
    while not parar.is_set():
        temps = {}
        try:
            with urllib.request.urlopen(URL_SENSORES, timeout=2) as r:
                dados = json.load(r)
            folhas = list(percorrer(dados))
            for rotulo, trecho, _, _ in SENSORES:
                for nome, valor in folhas:
                    if trecho.lower() in nome.lower():
                        temps[rotulo] = valor
                        break
        except Exception:
            pass
        estado["temps"] = temps
        parar.wait(2)


async def ler_capa(info):
    try:
        if info.thumbnail is None:
            return None
        fluxo = await info.thumbnail.open_read_async()
        tamanho = fluxo.size
        buf = Buffer(tamanho)
        await fluxo.read_async(buf, tamanho, InputStreamOptions.READ_AHEAD)
        fluxo.close()
        return preparar_capa(bytes(memoryview(buf)))
    except Exception as exc:
        log(f"capa: {exc}")
        return None


async def vigiar_spotify():
    mgr = await MediaManager.request_async()
    chave, tentativas, ultimo_erro = None, 0, ""
    while not parar.is_set():
        try:
            sessao = None
            for s in mgr.get_sessions():
                if "spotify" in s.source_app_user_model_id.lower():
                    sessao = s
                    break
            tocando = False
            if sessao is not None:
                tocando = int(sessao.get_playback_info().playback_status) == 4

            if sessao is None or (ESCONDER_PAUSADO and not tocando):
                estado["musica"] = None
                estado["capa"] = None
                chave = None
            else:
                info = await sessao.try_get_media_properties_async()
                nova = (info.title, info.artist)
                if nova != chave:
                    chave, tentativas = nova, 0
                    estado["capa"] = None
                if estado["capa"] is None and tentativas < 3:
                    tentativas += 1
                    estado["capa"] = await ler_capa(info)

                tl = sessao.get_timeline_properties()
                dur = (tl.end_time - tl.start_time).total_seconds()
                pos = (tl.position - tl.start_time).total_seconds()
                if tocando:
                    lu = tl.last_updated_time
                    if lu.tzinfo is None:
                        lu = lu.replace(tzinfo=datetime.timezone.utc)
                    agora = datetime.datetime.now(datetime.timezone.utc)
                    extra = (agora - lu).total_seconds()
                    if 0 <= extra <= dur:
                        pos += extra
                estado.update(
                    musica=nova, pos=pos, dur=dur,
                    t_poll=time.monotonic(), tocando=tocando,
                )
        except Exception as exc:
            if str(exc) != ultimo_erro:
                ultimo_erro = str(exc)
                log(f"spotify: {exc}")
        await asyncio.sleep(2)


def main():
    t = threading.Thread(target=transmitir, daemon=True)
    t.start()
    threading.Thread(target=vigiar_sensores, daemon=True).start()
    try:
        asyncio.run(vigiar_spotify())
    except KeyboardInterrupt:
        pass
    parar.set()
    t.join(timeout=5)
    log("parado")


main()