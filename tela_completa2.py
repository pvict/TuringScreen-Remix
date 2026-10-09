import asyncio
import datetime
import io
import os
import threading
import time

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

ARQUIVO_MUSICA = "video_tela.mp4"       # Vídeo com o vinil/efeitos quando toca música
ARQUIVO_BACKGROUND = "video_fundo.mp4"  # Vídeo de fundo em loop quando nada está a tocar
FPS = 30
LARGURA = 300             # largura máxima do texto
TAM_CAPA = 240            # diâmetro da capa do álbum
ESCONDER_PAUSADO = True
FALHAS_MAX = 10
ESPERA_RECONEXAO = 5
ATUALIZA_A_CADA = 0.15       # atualiza o anel/progresso a cada 0.15 segundo

parar = threading.Event()
estado = {
    "musica": None, "capa": None, "cor_capa": (30, 215, 96, 255),
    "pos": 0.0, "dur": 0.0, "t_poll": 0.0, "tocando": False,
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
    return 100


def fonte(nome_arquivo, tamanho):
    try:
        if os.path.exists(nome_arquivo):
            return ImageFont.truetype(nome_arquivo, tamanho)
        caminho_windows = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", nome_arquivo)
        if os.path.exists(caminho_windows):
            return ImageFont.truetype(caminho_windows, tamanho)
        return ImageFont.truetype(nome_arquivo, tamanho)
    except Exception as e:
        print(f"[ERRO] Não foi possível carregar a fonte '{nome_arquivo}': {e}")
        return ImageFont.load_default()

F_TITULO = fonte("SFPRODISPLAYBOLD.otf", 32)
F_ARTISTA = fonte("SFPRODISPLAYREGULAR.otf", 20)


def cortar(d, texto, f):
    if d.textlength(texto, font=f) <= LARGURA:
        return texto
    while len(texto) > 1 and d.textlength(texto + "…", font=f) > LARGURA:
        texto = texto[:-1]
    return texto + "…"


def txt(d, x, y, texto, f, cor, ancora="mt"):
    d.text(
        (x, y), texto, font=f, fill=cor, anchor=ancora,
        stroke_width=0, stroke_fill=(0, 0, 0, 255),
    )


def preparar_capa(dados):
    img = Image.open(io.BytesIO(dados)).convert("RGB")
    
    cor_media = img.resize((1, 1), resample=Image.BILINEAR).getpixel((0, 0))
    cor_rgba = (cor_media[0], cor_media[1], cor_media[2], 255)

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
    return saida, cor_rgba


_cache_marquee_titulo = None
_cache_marquee_img = None
_cache_marquee_largura = 0

def renderizar(snap):
    global _cache_marquee_titulo, _cache_marquee_img, _cache_marquee_largura

    img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx, cy = 240, 240          
    raio_anel = (TAM_CAPA // 2) + 18   

    musica = snap["musica"]
    cor_destaque = snap.get("cor_capa", (30, 215, 96, 255))
    
    # --- HALO DE LUZ DINÂMICO ---
    if musica and cor_destaque:
        camada_halo = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
        d_halo = ImageDraw.Draw(camada_halo)
        
        raio_halo = raio_anel + 25
        cor_halo = (cor_destaque[0], cor_destaque[1], cor_destaque[2], 120)
        
        d_halo.ellipse(
            (cx - raio_halo, cy - raio_halo, cx + raio_halo, cy + raio_halo),
            fill=cor_halo
        )
        
        from PIL import ImageFilter
        camada_halo = camada_halo.filter(ImageFilter.GaussianBlur(30))
        img = Image.alpha_composite(img, camada_halo)
        d = ImageDraw.Draw(img)

    # 1. Trilha de fundo do anel
    d.arc(
        (cx - raio_anel, cy - raio_anel, cx + raio_anel, cy + raio_anel),
        0, 360, fill=(255, 255, 255, 35), width=4
    )

    if musica and snap["dur"] > 0:
        pos = snap["pos"]
        if snap["tocando"]:
            pos += time.monotonic() - snap["t_poll"]
        frac = min(max(pos / snap["dur"], 0), 1)
        ang_fim = -90 + int(360 * frac)

        # 2. Arco ativo principal
        d.arc(
            (cx - raio_anel, cy - raio_anel, cx + raio_anel, cy + raio_anel),
            -90, ang_fim, fill=cor_destaque, width=4
        )

    # 3. Capa do Álbum
    capa = snap["capa"]
    if capa is not None and musica:
        x_capa = cx - (TAM_CAPA // 2)
        y_capa = cy - (TAM_CAPA // 2)
        img.alpha_composite(capa, (x_capa, y_capa))

    # 4. Textos e Marquee
    if musica:
        titulo, artista = musica
        y_texto = cy + raio_anel + 20
        
        LARGURA_MAXIMA = 325  
        titulo_str = titulo or ""
        largura_titulo = d.textlength(titulo_str, font=F_TITULO)
        
        if largura_titulo <= LARGURA_MAXIMA:
            txt(d, cx, y_texto, titulo_str, F_TITULO, (255, 255, 255, 255))
        else:
            if _cache_marquee_titulo != titulo_str:
                _cache_marquee_titulo = titulo_str
                texto_duplicado = titulo_str + "    •    " + titulo_str + "    •    "
                largura_total_img = int(d.textlength(texto_duplicado, font=F_TITULO)) + 100
                
                _cache_marquee_img = Image.new("RGBA", (largura_total_img, 60), (0, 0, 0, 0))
                d_cache = ImageDraw.Draw(_cache_marquee_img)
                d_cache.text(
                    (0, 0), texto_duplicado, font=F_TITULO, fill=(255, 255, 255, 255),
                    stroke_width=0, stroke_fill=(0, 0, 0, 255)
                )
                _cache_marquee_largura = int(d.textlength(titulo_str + "    •    ", font=F_TITULO))

            velocidade = 35.0
            deslocamento_float = (time.monotonic() * velocidade) % _cache_marquee_largura
            deslocamento_int = int(deslocamento_float)
            
            janela = _cache_marquee_img.crop((deslocamento_int, 0, deslocamento_int + LARGURA_MAXIMA, 60))
            x_pos = cx - (LARGURA_MAXIMA // 2)
            img.alpha_composite(janela, (x_pos, int(y_texto)))

        if artista:
            txt(d, cx, y_texto + 35, cortar(d, artista, F_ARTISTA), F_ARTISTA, (200, 200, 200, 255))

    raio_tela = 239
    cor_aro_externo = (cor_destaque[0], cor_destaque[1], cor_destaque[2], 40)
    d.arc(
        (cx - raio_tela, cy - raio_tela, cx + raio_tela, cy + raio_tela),
        0, 360, fill=cor_aro_externo, width=5
    )
            
    return img


def sessao_usb():
    dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
    if dev is None:
        raise RuntimeError("tela não encontrada")
    try:
        dev.set_configuration()
        
        h264_musica = operations.extract_h264_from_mp4(ARQUIVO_MUSICA)
        h264_fundo = operations.extract_h264_from_mp4(ARQUIVO_BACKGROUND)

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
        log(f"tela conectada - brilho inicial: {brilho}%")

        ultimo_check = 0.0
        ultimo_overlay = 0.0
        ultimo_hash = None
        falhas = 0

        fh_musica = open(h264_musica, "rb")
        fh_fundo = open(h264_fundo, "rb")
        tinha_midia = estado["musica"] is not None
        
        try:
            while not parar.is_set():
                agora = time.time()
                if agora - ultimo_check >= 5:
                    ultimo_check = agora
                    novo = brilho_para(datetime.datetime.now())
                    if novo != brilho:
                        brilho = novo
                        operations.send_brightness_command(dev, brilho)
                        log(f"Brilho alterado para: {brilho}%")

                tem_midia = estado["musica"] is not None
                if tem_midia != tinha_midia:
                    tinha_midia = tem_midia
                    (fh_musica if tem_midia else fh_fundo).seek(0)
                fh_ativo = fh_musica if tem_midia else fh_fundo
                
                data = fh_ativo.read(202752)
                if not data:
                    fh_ativo.seek(0)
                    data = fh_ativo.read(202752)
                
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
                if agora - ultimo_check >= 2:
                    ultimo_check = agora
                    novo = brilho_para(datetime.datetime.now())
                    if novo != brilho:
                        brilho = novo
                        operations.send_brightness_command(dev, brilho)
                        log(f"Brilho alterado para: {brilho}%")

                if agora - ultimo_overlay >= ATUALIZA_A_CADA:
                    ultimo_overlay = agora
                    
                    if tem_midia:
                        img = renderizar(dict(estado))
                    else:
                        img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))

                    h = hash(img.tobytes())
                    if h != ultimo_hash:
                        ultimo_hash = h
                        img.save("overlay.png")
                        operations.send_image(dev, "overlay.png")
        finally:
            fh_musica.close()
            fh_fundo.close()
            
        cmd(123)
    finally:
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        

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


async def ler_capa(info):
    try:
        if info.thumbnail is None:
            return None, None
        fluxo = await info.thumbnail.open_read_async()
        tamanho = fluxo.size
        buf = Buffer(tamanho)
        await fluxo.read_async(buf, tamanho, InputStreamOptions.READ_AHEAD)
        fluxo.close()
        return preparar_capa(bytes(memoryview(buf)))
    except Exception as exc:
        log(f"capa: {exc}")
        return None, None


async def vigiar_spotify():
    mgr = await MediaManager.request_async()
    chave, tentativas, ultimo_erro = None, 0, ""
    while not parar.is_set():
        try:
            sessao = None
            sessoes = list(mgr.get_sessions())
            
            # Só o Spotify é considerado: procura a sessão dele a tocar
            for s in sessoes:
                app_id = s.source_app_user_model_id.lower()
                if "spotify" in app_id:
                    status = int(s.get_playback_info().playback_status)
                    if status == 4:
                        sessao = s
                        break
            
            # Se o Spotify estiver pausado e ESCONDER_PAUSADO for False, mostra mesmo assim
            if sessao is None and not ESCONDER_PAUSADO:
                for s in sessoes:
                    if "spotify" in s.source_app_user_model_id.lower():
                        sessao = s
                        break

            tocando = False
            if sessao is not None:
                tocando = int(sessao.get_playback_info().playback_status) == 4

            if sessao is None or (ESCONDER_PAUSADO and not tocando):
                estado["musica"] = None
                estado["capa"] = None
                estado["cor_capa"] = (30, 215, 96, 255)
                estado["tocando"] = False
                chave = None
            else:
                info = await sessao.try_get_media_properties_async()
                nova = (info.title, info.artist)
                if nova != chave:
                    chave, tentativas = nova, 0
                    estado["capa"] = None
                    estado["cor_capa"] = (30, 215, 96, 255)
                    
                if estado["capa"] is None and tentativas < 3:
                    tentativas += 1
                    capa_img, cor_capa = await ler_capa(info)
                    if capa_img is not None:
                        estado["capa"] = capa_img
                        estado["cor_capa"] = cor_capa

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
                log(f"spotify/midia: {exc}")
        await asyncio.sleep(2)


async def main_async():
    await vigiar_spotify()


def main():
    t = threading.Thread(target=transmitir, daemon=True)
    t.start()
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
    parar.set()
    t.join(timeout=5)
    log("parado")


if __name__ == "__main__":
    main()