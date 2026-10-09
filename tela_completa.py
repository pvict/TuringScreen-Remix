import asyncio
import colorsys
import datetime
import io
import logging
import os
import threading
import time
import sys
import contextlib

# --- CORREÇÃO CRÍTICA PARA O WINRT (SPOTIFY) ---
sys.coinit_flags = 0  # 0 = COINIT_MULTITHREADED
import comtypes
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

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

logging.getLogger("turingscreencli").setLevel(logging.ERROR)
logging.getLogger("usb").setLevel(logging.CRITICAL)
logging.getLogger("pyusb").setLevel(logging.CRITICAL)

@contextlib.contextmanager
def silenciar_saida():
    with open(os.devnull, "w") as devnull:
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stderr = old_stderr

ARQUIVO_MUSICA = "video_tela.mp4"
ARQUIVO_BACKGROUND = "video_fundo.mp4"
FPS = 30
LARGURA = 300
TAM_CAPA = 240
ESCONDER_PAUSADO = True
FALHAS_MAX = 10
ESPERA_RECONEXAO = 5
ATUALIZA_A_CADA = 0.03       # Atualiza a imagem a ~33 quadros por segundo

parar = threading.Event()
estado = {
    "musica": None, "capa": None, "cor_capa": (30, 215, 96, 255),
    "cor_viva": (30, 215, 96, 255),
    "pos": 0.0, "dur": 0.0, "t_poll": 0.0, "tocando": False,
    "volume": None, "volume_exibir_ate": 0.0,
}


def log(msg):
    linha = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linha)
    try:
        with open("tela.log", "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


BRILHO_DIA = 100


def brilho_para(agora):
    h = agora.hour
    if 1 <= h < 7:
        return 30
    if h >= 18 or h < 1:
        return 60
    return BRILHO_DIA


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


def cor_viva_da_capa(img, fallback):
    pequena = img.resize((64, 64), Image.BILINEAR)
    paleta = getattr(Image, "Palette", Image).ADAPTIVE
    pequena = pequena.convert("P", palette=paleta, colors=8)
    pal = pequena.getpalette()
    melhor, melhor_pontos = None, 0.0
    for contagem, idx in pequena.getcolors():
        r, g, b = pal[idx * 3: idx * 3 + 3]
        h, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if sat < 0.25 or val < 0.25:
            continue
        pontos = contagem * sat * sat * val
        if pontos > melhor_pontos:
            melhor, melhor_pontos = (r, g, b, 255), pontos
    return melhor or fallback


def preparar_capa(dados):
    img = Image.open(io.BytesIO(dados)).convert("RGB")
    
    cor_media = img.resize((1, 1), resample=Image.BILINEAR).getpixel((0, 0))
    cor_rgba = (cor_media[0], cor_media[1], cor_media[2], 255)
    cor_viva = cor_viva_da_capa(img, cor_rgba)

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
    return saida, cor_rgba, cor_viva

_cache_marquee_pos = 0.0
_cache_marquee_titulo = None
_cache_marquee_img = None
_cache_marquee_largura = 0

_cache_halo_cor = None
_cache_halo_img = None

_cache_vol_anim = None
_cache_vol_opacidade = 0.0


def _laco_volume():
    """Lê o volume do Windows em background de forma ultra-rápida e responsiva."""
    comtypes.CoInitialize()
    try:
        dispositivo = AudioUtilities.GetSpeakers()
        
        try:
            volume_interface = dispositivo.EndpointVolume.QueryInterface(IAudioEndpointVolume)
        except AttributeError:
            from ctypes import cast, POINTER
            interface = dispositivo.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
            volume_interface = cast(interface, POINTER(IAudioEndpointVolume))

        while not parar.is_set():
            try:
                # Leitura direta do volume atual do sistema
                vol = int(round(volume_interface.GetMasterVolumeLevelScalar() * 100))
                vol_atual = estado.get("volume")
                
                if vol_atual is None:
                    estado["volume"] = vol
                elif vol_atual != vol:
                    estado["volume"] = vol
                    # Mantém o HUD visível por 3 segundos após a última alteração
                    estado["volume_exibir_ate"] = time.time() + 3.0
            except Exception as e:
                log(f"erro lendo volume: {e}")
            
            # Reduzido de 0.02 para 0.01 (10ms) para capturar instantaneamente o comando do teclado
            parar.wait(0.01)
    finally:
        comtypes.CoUninitialize()


def renderizar(snap):
    global _cache_marquee_pos
    global _cache_marquee_titulo, _cache_marquee_img, _cache_marquee_largura
    global _cache_halo_cor, _cache_halo_img
    global _cache_vol_anim, _cache_vol_opacidade

    img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx, cy = 240, 240          
    raio_anel = (TAM_CAPA // 2) + 18   

    musica = snap.get("musica")
    cor_destaque = snap.get("cor_viva") or snap.get("cor_capa", (30, 215, 96, 255))
    
    # --- HALO DE LUZ DINÂMICO ---
    if musica and cor_destaque:
        if _cache_halo_cor != cor_destaque:
            _cache_halo_cor = cor_destaque
            
            camada_halo = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
            d_halo = ImageDraw.Draw(camada_halo)
            
            cx_p, cy_p = 60, 60
            raio_halo_p = int((raio_anel + 25) / 4)
            cor_halo = (cor_destaque[0], cor_destaque[1], cor_destaque[2], 160)
            
            d_halo.ellipse(
                (cx_p - raio_halo_p, cy_p - raio_halo_p, cx_p + raio_halo_p, cy_p + raio_halo_p),
                fill=cor_halo
            )
            
            from PIL import ImageFilter
            camada_halo = camada_halo.filter(ImageFilter.GaussianBlur(12))
            _cache_halo_img = camada_halo.resize((480, 480), Image.BILINEAR)

        if _cache_halo_img is not None:
            img = Image.alpha_composite(img, _cache_halo_img)
            d = ImageDraw.Draw(img)
    else:
        _cache_halo_cor = None
        _cache_halo_img = None

    # 1. Trilha de fundo do anel e progresso (Apenas desenha se houver música ativa)
    if musica:
        d.arc(
            (cx - raio_anel, cy - raio_anel, cx + raio_anel, cy + raio_anel),
            0, 360, fill=(255, 255, 255, 35), width=4
        )

        if snap.get("dur", 0) > 0:
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
    capa = snap.get("capa")
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
            # Usa diretamente o tempo atual (time.monotonic()) para calcular o deslocamento contínuo em alta precisão
            deslocamento_int = int((time.monotonic() * velocidade) % _cache_marquee_largura)
            
            janela = _cache_marquee_img.crop((deslocamento_int, 0, deslocamento_int + LARGURA_MAXIMA, 60))
            x_pos = cx - (LARGURA_MAXIMA // 2)
            img.alpha_composite(janela, (x_pos, int(y_texto)))
            
        if artista:
            txt(d, cx, y_texto + 35, cortar(d, artista, F_ARTISTA), F_ARTISTA, (200, 200, 200, 255))

    raio_tela = 239
    # Se houver música usa a cor viva da capa com transparência; se não, fica apagado/neutro
    if musica and cor_destaque:
        cor_aro_externo = (cor_destaque[0], cor_destaque[1], cor_destaque[2], 40)
    else:
        cor_aro_externo = (255, 255, 255, 10)
        
    d.arc(
        (cx - raio_tela, cy - raio_tela, cx + raio_tela, cy + raio_tela),
        0, 360, fill=cor_aro_externo, width=5
    )

# 5. Overlay de Volume
    vol_real = snap.get("volume")
    exibir_ate = snap.get("volume_exibir_ate", 0)
    
    if _cache_vol_anim is None and vol_real is not None:
        _cache_vol_anim = float(vol_real)
        
    if vol_real is not None:
        if vol_real is not None:
            # Aumentamos a velocidade de transição de 0.4 para 0.8 para o indicador acompanhar o dedo instantaneamente
            _cache_vol_anim += (vol_real - _cache_vol_anim) * 0.8
        
        agora = time.time()
        
        if agora < exibir_ate:
            import math
            from PIL import ImageFilter
            
            opac = 255
            layer_hud = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
            d_hud = ImageDraw.Draw(layer_hud)
            
            raio_vol = 175         
            tamanho_tick = 12      
            espessura_tick = 3     
            num_ticks = 46         
            
            angulo_inicio = 140
            angulo_fim = 400
            
            vol_percentual = _cache_vol_anim / 100.0
            
            cor_ativa = (255, 255, 255, opac)
            if cor_destaque:
                cor_ativa = (cor_destaque[0], cor_destaque[1], cor_destaque[2], opac)
                
            cor_inativa = (80, 80, 80, 100)
            
            # --- DESENHO DOS TICKS PRINCIPAIS ---
            for i in range(num_ticks):
                frac = i / (num_ticks - 1)
                ang_deg = angulo_inicio + (angulo_fim - angulo_inicio) * frac
                ang_rad = math.radians(ang_deg)
                
                x_in = cx + (raio_vol - tamanho_tick / 2) * math.cos(ang_rad)
                y_in = cy + (raio_vol - tamanho_tick / 2) * math.sin(ang_rad)
                x_out = cx + (raio_vol + tamanho_tick / 2) * math.cos(ang_rad)
                y_out = cy + (raio_vol + tamanho_tick / 2) * math.sin(ang_rad)
                
                cor_tick = cor_ativa if frac <= vol_percentual else cor_inativa
                d_hud.line([(x_in, y_in), (x_out, y_out)], fill=cor_tick, width=espessura_tick)
            
            # --- EFEITO DE GLOW OTIMIZADO (ALTA VISIBILIDADE & ZERO LAG) ---
            # Criamos uma camada miniatura (120x120, um quarto do tamanho) para o desfoque voar no processamento
            layer_glow_mini = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
            d_glow_mini = ImageDraw.Draw(layer_glow_mini)
            
            cor_glow = (cor_ativa[0], cor_ativa[1], cor_ativa[2], 255) if cor_destaque else (255, 255, 255, 255)
            
            raio_vol_mini = raio_vol / 4.0
            tamanho_tick_mini = tamanho_tick / 4.0
            espessura_tick_mini = max(1, espessura_tick / 4.0)
            cx_p, cy_p = 60, 60
            
            for i in range(num_ticks):
                frac = i / (num_ticks - 1)
                if frac <= vol_percentual:
                    ang_deg = angulo_inicio + (angulo_fim - angulo_inicio) * frac
                    ang_rad = math.radians(ang_deg)
                    
                    x_in = cx_p + (raio_vol_mini - tamanho_tick_mini / 2) * math.cos(ang_rad)
                    y_in = cy_p + (raio_vol_mini - tamanho_tick_mini / 2) * math.sin(ang_rad)
                    x_out = cx_p + (raio_vol_mini + tamanho_tick_mini / 2) * math.cos(ang_rad)
                    y_out = cy_p + (raio_vol_mini + tamanho_tick_mini / 2) * math.sin(ang_rad)
                    
                    d_glow_mini.line([(x_in, y_in), (x_out, y_out)], fill=cor_glow, width=int(espessura_tick_mini + 2))
            
            # Desfoque super rápido numa imagem pequena e expansão suave para o tamanho total (480x480)
            layer_glow_mini = layer_glow_mini.filter(ImageFilter.GaussianBlur(4))
            layer_glow = layer_glow_mini.resize((480, 480), Image.BILINEAR)
            
            # Compõe o glow expansivo e intenso por trás dos ticks nítidos
            img = Image.alpha_composite(img, layer_glow)
            img = Image.alpha_composite(img, layer_hud)
            
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

        with silenciar_saida():
            for n in (111, 112, 13):
                try:
                    cmd(n)
                    time.sleep(0.05)
                except Exception:
                    pass

            brilho = brilho_para(datetime.datetime.now())

            try:
                operations.send_brightness_command(dev, brilho)
                time.sleep(0.05)
                cmd(41)
                time.sleep(0.05)
                operations.clear_image(dev)
                time.sleep(0.05)
                operations.send_frame_rate_command(dev, FPS)
                time.sleep(0.05)
            except Exception:
                pass
        
        log(f"tela conectada - brilho inicial: {brilho}%")

        ultimo_check = 0.0
        ultimo_overlay = 0.0
        ultimo_hash = None
        falhas = 0

        # CONTADOR DE AQUECIMENTO DO VÍDEO AO DESPAUSAR
        frames_aquecimento = 0

        fh_musica = open(h264_musica, "rb")
        fh_fundo = open(h264_fundo, "rb")
        tinha_midia = estado.get("musica") is not None
        
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

                tem_midia = estado.get("musica") is not None
                if tem_midia != tinha_midia:
                    tinha_midia = tem_midia
                    (fh_musica if tem_midia else fh_fundo).seek(0)
                    if tem_midia:
                        frames_aquecimento = 1  # Ignora a capa nos primeiros 4 quadros (~120ms) para o vídeo arrancar limpo    
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
                    
                    if frames_aquecimento > 0:
                        frames_aquecimento -= 1
                        # Envia apenas o vídeo puro (sem renderizar capa, arcos ou texto por cima)
                        img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
                    elif tem_midia or _cache_vol_opacidade > 0:
                        img = renderizar(dict(estado))
                    else:
                        img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))

                    # Envia diretamente o frame contínuo para garantir a fluidez do letreiro
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
            return None, None, None
        fluxo = await info.thumbnail.open_read_async()
        tamanho = fluxo.size
        buf = Buffer(tamanho)
        await fluxo.read_async(buf, tamanho, InputStreamOptions.READ_AHEAD)
        fluxo.close()
        return preparar_capa(bytes(memoryview(buf)))
    except Exception as exc:
        log(f"capa: {exc}")
        return None, None, None


async def vigiar_spotify():
    mgr = await MediaManager.request_async()
    chave, tentativas, ultimo_erro = None, 0, ""
    while not parar.is_set():
        try:
            sessao = None
            sessoes = list(mgr.get_sessions())
            
            for s in sessoes:
                app_id = s.source_app_user_model_id.lower()
                if "spotify" in app_id:
                    status = int(s.get_playback_info().playback_status)
                    if status == 4:
                        sessao = s
                        break
            
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
                estado["cor_viva"] = (30, 215, 96, 255)
                estado["tocando"] = False
                chave = None
            else:
                info = await sessao.try_get_media_properties_async()
                nova = (info.title, info.artist)
                
                if nova != chave:
                    chave = nova
                    tentativas = 0
                    precisa_carregar_capa = True
                else:
                    precisa_carregar_capa = estado.get("capa") is None

                if precisa_carregar_capa and tentativas < 2:
                    tentativas += 1
                    await asyncio.sleep(0.08)
                    capa_img, cor_capa, cor_viva = await ler_capa(info)
                    
                    if capa_img is not None:
                        estado["capa"] = capa_img
                        estado["cor_capa"] = cor_capa
                        estado["cor_viva"] = cor_viva

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

        await asyncio.sleep(0.15)


async def main_async():
    await vigiar_spotify()


def main():
    try:
        import leds_openrgb
        leds_openrgb.iniciar(estado, brilho_para, parar, log)
    except ImportError as exc:
        log(f"LEDs desligados (openrgb-python não instalado): {exc}")
    
    t_vol = threading.Thread(target=_laco_volume, daemon=True)
    t_vol.start()

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