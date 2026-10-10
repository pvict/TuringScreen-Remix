import asyncio
import colorsys
import datetime
import io
import logging
import math
import os
import threading
import time
import sys
import contextlib
import shutil

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
import ao_vivo
import animacao_capa
from spotify_playlist import SpotifyPlaylistWatcher

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
FPS = 60
LARGURA = 300
TAM_CAPA = 240
ESCONDER_PAUSADO = True
FALHAS_MAX = 10
ESPERA_RECONEXAO = 5
KBPS = 5000               # reduz a fila USB mantendo o alvo de 60 FPS

parar = threading.Event()
estado = {
    "musica": None, "capa": None, "cor_capa": (30, 215, 96, 255),
    "cor_viva": (30, 215, 96, 255),
    "capa_playlist": None, "cor_playlist": None, "cor_viva_playlist": None,
    "playlist_uri": None, "playlist_nome": None, "playlist_evento": 0,
    "pos": 0.0, "dur": 0.0, "t_poll": 0.0, "tocando": False,
    "midia_pronta": False,
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


def preparar_capa(dados, calcular_cores=True):
    img = Image.open(io.BytesIO(dados)).convert("RGB")

    if calcular_cores:
        cor_media = img.resize((1, 1), resample=Image.BILINEAR).getpixel((0, 0))
        cor_rgba = (cor_media[0], cor_media[1], cor_media[2], 255)
        cor_viva = cor_viva_da_capa(img, cor_rgba)
    else:
        cor_rgba = cor_viva = None

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


def atualizar_capa_playlist(uri, nome, dados):
    if not dados:
        estado.update(
            capa_playlist=None, cor_playlist=None, cor_viva_playlist=None,
            playlist_uri=None, playlist_nome=None,
        )
        return
    try:
        # Usa o mesmo tamanho e recorte circular já aplicado à capa do álbum.
        capa, cor, cor_viva = preparar_capa(dados, calcular_cores=False)
        mudou_playlist = uri != estado.get("playlist_uri")
        estado.update(
            capa_playlist=capa, cor_playlist=cor, cor_viva_playlist=cor_viva,
            playlist_uri=uri, playlist_nome=nome,
            playlist_evento=estado.get("playlist_evento", 0) + int(mudou_playlist),
        )
        log(f"Spotify playlist: capa carregada ({nome or 'sem nome'}).")
    except Exception as exc:
        log(f"Spotify playlist: erro preparando a capa: {exc}")

_cache_marquee_pos = 0.0
_cache_marquee_titulo = None
_cache_marquee_img = None
_cache_marquee_largura = 0

_cache_halo_cor = None
_cache_halo_img = None

_cache_vol_anim = None
_cache_vol_opacidade = 0.0
_cache_hud_glow_masks = {}

_transicao_musica_atual = None
_capa_anterior = None
_transicao_progresso = 1.0  # 1.0 = transição concluída


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

    # 1. Trilha de fundo do anel
    d.arc(
        (cx - raio_anel, cy - raio_anel, cx + raio_anel, cy + raio_anel),
        0, 360, fill=(255, 255, 255, 35), width=4
    )

    if musica and snap.get("dur", 0) > 0:
        duracao = snap["dur"]
        pos_alvo = snap["pos"]
        
        if snap.get("tocando"):
            pos_alvo += time.monotonic() - snap["t_poll"]
            
        # Inicializa a variável de posição suavizada no cache global se não existir
        global _prog_suave
        if "_prog_suave" not in globals() or _prog_suave is None or abs(_prog_suave - pos_alvo) > 3.0:
            _prog_suave = pos_alvo
            
        # Interpolação linear (LERP): suaviza a transição aproximando o valor atual do valor alvo gradualmente
        _prog_suave += (pos_alvo - _prog_suave) * 0.25
        
        frac = min(max(_prog_suave / duracao, 0), 1)
        ang_fim = -90 + 360 * frac

        # 2. Arco ativo principal com transição ultra suave
        d.arc(
            (cx - raio_anel, cy - raio_anel, cx + raio_anel, cy + raio_anel),
            -90, ang_fim, fill=cor_destaque, width=4
        )

    # 3. Capa do Álbum (Perfeitamente redonda, instantânea e leve)
    capa = snap.get("capa")
    if capa is not None and musica:
        x_capa = cx - (capa.width // 2)
        y_capa = cy - (capa.height // 2)
        
        # Desenha diretamente a capa já tratada e circular, sem sobrecarregar o CPU com filtros por frame
        img.alpha_composite(capa, (x_capa, y_capa))

    # 4. Textos e Marquee (Com fade suave nas pontas)
    texto_alpha = snap.get("texto_alpha", 1.0)
    dy_texto = int(round(snap.get("texto_dy", 0.0)))
    _img_base = None
    if musica:
        if texto_alpha < 0.999:
            # o texto é desenhado numa camada própria para poder aplicar o fade
            _img_base = img
            img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
        modo_playlist_texto = snap.get("modo_playlist_texto", False)
        titulo, artista = musica
        y_texto = cy + raio_anel + 20 + dy_texto
        y_titulo = y_texto
        if modo_playlist_texto:
            txt(d, cx, y_texto, "Você está ouvindo", F_ARTISTA, (200, 200, 200, 255))
            y_titulo += 25
        
        LARGURA_MAXIMA = 325  
        titulo_str = titulo or ""
        largura_titulo = d.textlength(titulo_str, font=F_TITULO)
        
        if largura_titulo <= LARGURA_MAXIMA:
            txt(d, cx, y_titulo, titulo_str, F_TITULO, (255, 255, 255, 255))
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
            tempo_visual = snap.get("tempo_visual")
            if tempo_visual is None:
                tempo_visual = time.monotonic()
            if modo_playlist_texto:
                inicio_mensagem = snap.get("mensagem_playlist_inicio")
                if inicio_mensagem is None:
                    inicio_mensagem = tempo_visual
                tempo_marquee = max(0.0, tempo_visual - inicio_mensagem)
            else:
                tempo_marquee = tempo_visual
            deslocamento_int = int((tempo_marquee * velocidade) % _cache_marquee_largura)
            
            janela = _cache_marquee_img.crop((deslocamento_int, 0, deslocamento_int + LARGURA_MAXIMA, 60))
            
            # --- MÁSCARA DE FADE NAS EXTREMIDADES ---
            global _cache_marquee_mascara
            if "_cache_marquee_mascara" not in globals() or globals()["_cache_marquee_mascara"] is None:
                mascara = Image.new("L", (LARGURA_MAXIMA, 60), 255)
                d_mask = ImageDraw.Draw(mascara)
                fade_w = 25  # Largura da zona de transição nas pontas (em pixels)
                for x in range(fade_w):
                    alpha = int(255 * (x / fade_w))
                    d_mask.line([(x, 0), (x, 60)], fill=alpha)
                    d_mask.line([(LARGURA_MAXIMA - 1 - x, 0), (LARGURA_MAXIMA - 1 - x, 60)], fill=alpha)
                _cache_marquee_mascara = mascara

            # Aplica o gradiente alfa de forma otimizada usando ImageChops
            from PIL import ImageChops
            r, g, b, a = janela.split()
            a_fade = ImageChops.multiply(a, _cache_marquee_mascara)
            janela = Image.merge("RGBA", (r, g, b, a_fade))
            
            x_pos = cx - (LARGURA_MAXIMA // 2)
            img.alpha_composite(janela, (x_pos, int(y_titulo)))
            
        if artista and not modo_playlist_texto:
            txt(d, cx, y_texto + 35, cortar(d, artista, F_ARTISTA), F_ARTISTA, (200, 200, 200, 255))

    if _img_base is not None:
        a_txt = img.getchannel("A").point(lambda v: int(v * texto_alpha))
        img.putalpha(a_txt)
        _img_base.alpha_composite(img)
        img = _img_base
        d = ImageDraw.Draw(img)

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
        
        hud_a = snap.get("hud_alpha")
        if hud_a is None:
            hud_a = 1.0 if agora < exibir_ate else 0.0
        if hud_a > 0.01:
            from PIL import ImageFilter
            
            opac = int(255 * hud_a)
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
                
            cor_inativa = (80, 80, 80, int(100 * hud_a))
            
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
            # Reutiliza a máscara desfocada; ela só depende do número de marcas ativas.
            ticks_ativos = sum(1 for i in range(num_ticks) if i / (num_ticks - 1) <= vol_percentual)
            mascara_glow = _cache_hud_glow_masks.get(ticks_ativos)
            if mascara_glow is None:
                layer_glow_mini = Image.new("L", (120, 120), 0)
                d_glow_mini = ImageDraw.Draw(layer_glow_mini)
                raio_vol_mini = raio_vol / 4.0
                tamanho_tick_mini = tamanho_tick / 4.0
                espessura_tick_mini = max(1, espessura_tick / 4.0)
                cx_p, cy_p = 60, 60
                for i in range(ticks_ativos):
                    frac = i / (num_ticks - 1)
                    ang_deg = angulo_inicio + (angulo_fim - angulo_inicio) * frac
                    ang_rad = math.radians(ang_deg)
                    x_in = cx_p + (raio_vol_mini - tamanho_tick_mini / 2) * math.cos(ang_rad)
                    y_in = cy_p + (raio_vol_mini - tamanho_tick_mini / 2) * math.sin(ang_rad)
                    x_out = cx_p + (raio_vol_mini + tamanho_tick_mini / 2) * math.cos(ang_rad)
                    y_out = cy_p + (raio_vol_mini + tamanho_tick_mini / 2) * math.sin(ang_rad)
                    d_glow_mini.line([(x_in, y_in), (x_out, y_out)], fill=255,
                                     width=int(espessura_tick_mini + 2))
                mascara_glow = layer_glow_mini.filter(ImageFilter.GaussianBlur(4)).resize(
                    (480, 480), Image.BILINEAR
                )
                _cache_hud_glow_masks[ticks_ativos] = mascara_glow

            cor_glow = (cor_ativa[0], cor_ativa[1], cor_ativa[2], 255)
            layer_glow = Image.new("RGBA", (480, 480), cor_glow)
            if opac < 255:
                mascara_frame = mascara_glow.point(lambda v: v * opac // 255)
            else:
                mascara_frame = mascara_glow
            layer_glow.putalpha(mascara_frame)
            # Compõe o glow expansivo e intenso por trás dos ticks nítidos
            img = Image.alpha_composite(img, layer_glow)
            img = Image.alpha_composite(img, layer_hud)
            
    return img


COR_PADRAO = (30, 215, 96, 255)


def _cor_rgba(cor):
    c = tuple(int(round(x)) for x in (cor or COR_PADRAO))
    return c if len(c) == 4 else (c[0], c[1], c[2], 255)


# --- animações (ajuste à vontade) ---
ROTACAO_GRAUS_S = 40.0   # giro do disco enquanto toca (graus por segundo); 0 = capa parada
TEMPO_ACELERAR = 1.0     # s para o disco chegar à velocidade ao tocar/retomar
TEMPO_FREAR = 2.0        # s para o disco parar ao pausar (desacelera até zero)
ATRASO_OCIOSO = 3.0      # s entre pausar e começar a voltar ao vídeo ocioso
FADE_ENTRA = 0.7         # s do crossfade ocioso -> música
FADE_SAI = 1.0           # s do crossfade música -> ocioso
TEXTO_SAI = 0.30         # s para o texto antigo sumir ao trocar de faixa
TEXTO_ENTRA = 0.40       # s para o texto novo aparecer
TEXTO_DESLOC = 12        # px que o texto desliza ao sumir/aparecer
HUD_ENTRA = 0.15         # s de fade do HUD de volume ao aparecer
HUD_SAI = 0.40           # s de fade do HUD de volume ao sumir


def _suave(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class Painel:
    """Monta cada quadro da transmissão ao vivo: vídeo de fundo + painel + animações.

    Tudo vira vídeo H.264 (ao_vivo.py), então as animações não dependem do tempo que a tela
    leva para receber cada imagem. Roda na thread do pipeline.

    Presença do painel (self.p, de 0 a 1): sobe quando toca e desce só ATRASO_OCIOSO segundos
    depois de pausar; o mesmo valor controla o crossfade dos vídeos e o fade do painel.
    Enquanto o painel segue na tela depois da pausa, ele mostra a última faixa, com o disco
    desacelerando até parar."""

    def __init__(self, fundo_musica, fundo_ocioso, brilho_inicial):
        self.fundo_musica = fundo_musica
        self.fundo_ocioso = fundo_ocioso
        self.pipe = None
        self.brilho = brilho_inicial
        self.t_brilho = 0.0
        self.erro_log = ""
        self.t_ultimo = None
        self.estava_tocando = False
        self.t_pausa = None
        self.p = 0.0
        self.hud = 0.0
        self._ultimo_log_hud = 0.0
        self._hud_render_max_ms = 0.0
        self._diag_tempos = {}
        self._diag_playlist_ativa = False
        self._diag_playlist_uri = None
        self._zerar()

    def _zerar(self):
        self.capa = None        # capa exibida (quando não há transição)
        self.cor = None
        self.destino = None     # (capa, cor) para onde a transição em curso vai
        self.alvo = None        # última capa do estado que já provocou uma transição
        self.trans = None
        self.titulo = None      # (título, artista) exibido
        self.titulo_alvo = None
        self.modo_playlist_texto = False
        self.modo_playlist_texto_alvo = False
        self.t_texto = None     # início da animação de troca de texto
        self.angulo = 0.0
        self.vel = 0.0
        self._v_alvo = None
        self._r_t0 = 0.0
        self._r_v0 = 0.0
        self.exibindo_playlist = False
        self._playlist_uri_timer = None
        self._playlist_evento_visto = 0
        self._proxima_mensagem_playlist = None
        self._mensagem_playlist_ate = 0.0
        self._inicio_mensagem_playlist = None

    def _checar_brilho(self, agora):
        if agora - self.t_brilho < 2.0 or self.pipe is None:
            return
        self.t_brilho = agora
        novo = brilho_para(datetime.datetime.now())
        if novo != self.brilho:
            self.brilho = novo
            # só a thread do USB fala com a tela
            self.pipe.env.executar(lambda dev, b=novo: operations.send_brightness_command(dev, b))
            log(f"Brilho alterado para: {novo}%")

    def _fundo(self, ps):
        inicio_fundo = time.perf_counter()
        def ler(dec):
            return dec.proximo() or Image.new("RGB", (480, 480), (10, 10, 20))
        if ps <= 0.0:
            quadro = ler(self.fundo_ocioso)
        elif ps >= 1.0:
            quadro = ler(self.fundo_musica)
        else:
            quadro = Image.blend(ler(self.fundo_ocioso), ler(self.fundo_musica), ps)
        self._diag_tempos["fundo_ms"] = (time.perf_counter() - inicio_fundo) * 1000
        return quadro

    def _girar(self, agora, dt, tocando):
        """Velocidade do disco: acelera suave ao tocar e desacelera até zero ao pausar."""
        alvo = ROTACAO_GRAUS_S if tocando else 0.0
        if alvo != self._v_alvo:
            self._v_alvo, self._r_t0, self._r_v0 = alvo, agora, self.vel
        if alvo == 0.0:
            u = min((agora - self._r_t0) / TEMPO_FREAR, 1.0)
            self.vel = self._r_v0 * (1 - u) ** 2
        else:
            u = min((agora - self._r_t0) / TEMPO_ACELERAR, 1.0)
            self.vel = self._r_v0 + (alvo - self._r_v0) * _suave(u)
        if self.trans is None:      # durante o giro de troca de capa o disco fica na posição 0
            self.angulo = (self.angulo + self.vel * dt) % 360

    def _capa_girada(self):
        if self.capa is None:
            return None
        a = self.angulo % 360
        if a < 0.05 or a > 359.95:
            return self.capa
        return self.capa.rotate(-a, resample=Image.BICUBIC)   # sentido horário

    def _painel(self, snap, agora, dt, tocando):
        inicio_animacao = time.perf_counter()
        musica = snap.get("musica")
        cap = snap.get("capa")
        cap_playlist = snap.get("capa_playlist") if snap.get("mostrar_capa_playlist") else None
        # A paleta da tela e dos LEDs continua vindo da capa do álbum.
        cor_base = snap.get("cor_viva") or snap.get("cor_capa")
        cor_nova = _cor_rgba(cor_base)

        if tocando:
            # Texto da faixa e aviso da playlist compartilham a mesma animação.
            modo_playlist_alvo = bool(snap.get("mensagem_playlist"))
            texto_alvo = (
                (snap.get("playlist_nome") or "", "")
                if modo_playlist_alvo else musica
            )
            if self.titulo is None:
                self.titulo = self.titulo_alvo = texto_alvo
                self.modo_playlist_texto = self.modo_playlist_texto_alvo = modo_playlist_alvo
            elif (texto_alvo != self.titulo_alvo
                  or modo_playlist_alvo != self.modo_playlist_texto_alvo):
                self.titulo_alvo = texto_alvo
                self.modo_playlist_texto_alvo = modo_playlist_alvo
                self.t_texto = agora
            # Playlist e álbum usam a mesma transição circular de troca de capa.
            capa_alvo = cap_playlist if cap_playlist is not None else cap
            self.exibindo_playlist = cap_playlist is not None
            if capa_alvo is not None and capa_alvo is not self.alvo:
                retomou_mesma = (
                    cap_playlist is None and not self.estava_tocando
                    and self.capa is not None and musica == self.titulo
                )
                if retomou_mesma:
                    self.alvo, self.capa, self.cor = capa_alvo, capa_alvo, cor_nova
                else:
                    if self.trans is not None and self.destino is not None:
                        self.capa, self.cor = self.destino
                    ant = self._capa_girada()
                    self.alvo = capa_alvo
                    self.destino = (capa_alvo, cor_nova)
                    self.angulo = 0.0
                    self.trans = animacao_capa.TransicaoCapa(
                        ant, self.cor, capa_alvo, cor_nova, inicio=agora)

        self._girar(agora, dt, tocando and not self.exibindo_playlist)

        capa_img, cor = None, None
        self._diag_tempos["transicao_capa"] = self.trans is not None
        if self.trans is not None:
            capa_img, cor, _metade, fim = self.trans.quadro(agora)
            if fim:
                self.trans = None
                self.capa, self.cor = self.destino
        else:
            if self.capa is None and tocando:
                self.cor = cor_nova                         # sem capa: usa a cor do estado
            capa_img, cor = self._capa_girada(), self.cor or cor_nova

        # texto
        ta, dy = 1.0, 0.0
        if self.t_texto is not None:
            t = agora - self.t_texto
            if t < TEXTO_SAI:
                u = _suave(t / TEXTO_SAI)
                ta, dy = 1 - u, -TEXTO_DESLOC * u
            else:
                self.titulo = self.titulo_alvo
                self.modo_playlist_texto = self.modo_playlist_texto_alvo
                u = (t - TEXTO_SAI) / TEXTO_ENTRA
                if u >= 1:
                    self.t_texto = None
                else:
                    u = _suave(u)
                    ta, dy = u, TEXTO_DESLOC * (1 - u)

        # HUD de volume
        alvo_hud = 1.0 if time.time() < snap.get("volume_exibir_ate", 0.0) else 0.0
        if self.hud <= 0.0 and alvo_hud > 0.0 and self.pipe is not None:
            self.pipe.marcar_evento("HUD de volume entrou")
        if self.hud < alvo_hud:
            self.hud = min(alvo_hud, self.hud + dt / HUD_ENTRA)
        elif self.hud > alvo_hud:
            self.hud = max(alvo_hud, self.hud - dt / HUD_SAI)

        cor = _cor_rgba(cor)
        snap.update(musica=self.titulo, capa=capa_img, cor_viva=cor, cor_capa=cor,
                    texto_alpha=ta, texto_dy=dy, hud_alpha=_suave(self.hud),
                    modo_playlist_texto=self.modo_playlist_texto, tempo_visual=agora)
        inicio_render = time.perf_counter()
        self._diag_tempos["animacao_ms"] = (inicio_render - inicio_animacao) * 1000
        quadro = renderizar(snap)
        render_ms = (time.perf_counter() - inicio_render) * 1000
        self._diag_tempos["desenhar_ms"] = render_ms

        if self.hud > 0.01:
            self._hud_render_max_ms = max(self._hud_render_max_ms, render_ms)
            agora_log = time.monotonic()
            if agora_log - self._ultimo_log_hud >= 1.0:
                fila_video = self.pipe.cod.saida.qsize() if self.pipe else -1
                log(
                    f"diagnóstico HUD volume: render_máx={self._hud_render_max_ms:.1f}ms "
                    f"fila_vídeo={fila_video}"
                )
                self._ultimo_log_hud = agora_log
                self._hud_render_max_ms = 0.0

        return quadro

    def contexto_diagnostico(self):
        return {**self._diag_tempos, "playlist": self.exibindo_playlist,
                "texto_animando": self.t_texto is not None, "hud": self.hud > 0.01}

    def quadro(self, agora):
        self._diag_tempos = {"fundo_ms": 0.0, "animacao_ms": 0.0,
                             "desenhar_ms": 0.0, "compor_ms": 0.0,
                             "transicao_capa": False}
        dt = 0.0 if self.t_ultimo is None else min(max(agora - self.t_ultimo, 0.0), 0.2)
        self.t_ultimo = agora
        self._checar_brilho(agora)

        snap = dict(estado)
        tocando = snap.get("musica") is not None
        uri_playlist = snap.get("playlist_uri") if tocando and snap.get("capa_playlist") is not None else None
        evento_playlist = snap.get("playlist_evento", 0)
        if evento_playlist != self._playlist_evento_visto and uri_playlist:
            # Mudança detectada pelo monitor Spotify: anuncia antes da capa do álbum.
            self._playlist_evento_visto = evento_playlist
            self._playlist_uri_timer = uri_playlist
            self._mensagem_playlist_ate = agora + 7.0
            self._inicio_mensagem_playlist = agora
            self._proxima_mensagem_playlist = agora + 60.0
        elif uri_playlist != self._playlist_uri_timer:
            self._playlist_uri_timer = uri_playlist
            self._proxima_mensagem_playlist = agora + 60.0 if uri_playlist else None
            self._mensagem_playlist_ate = 0.0
            self._inicio_mensagem_playlist = None
        elif uri_playlist and self._proxima_mensagem_playlist is not None and agora >= self._proxima_mensagem_playlist:
            self._mensagem_playlist_ate = agora + 7.0
            self._inicio_mensagem_playlist = agora
            self._proxima_mensagem_playlist = agora + 60.0
        mostrar_playlist = bool(uri_playlist and agora < self._mensagem_playlist_ate)
        if (mostrar_playlist != self._diag_playlist_ativa
                or (mostrar_playlist and uri_playlist != self._diag_playlist_uri)):
            self._diag_playlist_ativa = mostrar_playlist
            self._diag_playlist_uri = uri_playlist if mostrar_playlist else None
            evento = (f"playlist entrou/trocou: {snap.get('playlist_nome')}"
                      if mostrar_playlist else "playlist saiu; retorno ao álbum ou pausa")
            if self.pipe is not None:
                self.pipe.marcar_evento(evento)
        snap["mostrar_capa_playlist"] = mostrar_playlist
        snap["mensagem_playlist"] = (
            f"Você está ouvindo '{snap.get('playlist_nome')}'"
            if mostrar_playlist and snap.get("playlist_nome") else None
        )
        snap["mensagem_playlist_inicio"] = self._inicio_mensagem_playlist
        if tocando:
            self.t_pausa = None
        elif self.estava_tocando:
            self.t_pausa = agora
        mostrar = tocando or (self.t_pausa is not None and agora - self.t_pausa < ATRASO_OCIOSO)
        alvo = 1.0 if mostrar else 0.0
        if self.p < alvo:
            self.p = min(alvo, self.p + dt / FADE_ENTRA)
        elif self.p > alvo:
            self.p = max(alvo, self.p - dt / FADE_SAI)
        ps = _suave(self.p)

        if (self.p <= 0.0 and not tocando) or (not tocando and self.titulo is None):
            self._zerar()
            self.estava_tocando = tocando
            return self._fundo(0.0)

        bg = self._fundo(ps)
        try:
            ov = self._painel(snap, agora, dt, tocando)
            if ps < 0.999:
                a = ov.getchannel("A").point(lambda v: int(v * ps))
                ov.putalpha(a)
        except Exception as exc:
            if str(exc) != self.erro_log:
                self.erro_log = str(exc)
                log(f"erro desenhando o painel: {exc}")
            self.estava_tocando = tocando
            return bg
        self.estava_tocando = tocando
        inicio_compor = time.perf_counter()
        base = bg.convert("RGBA")
        base.alpha_composite(ov)
        quadro = base.convert("RGB")
        self._diag_tempos["compor_ms"] = (time.perf_counter() - inicio_compor) * 1000
        return quadro


def sessao_usb():
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg não encontrado no PATH")
    dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
    if dev is None:
        raise RuntimeError("tela não encontrada")
    try:
        dev.set_configuration()

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

        fundo_musica = ao_vivo.FundoDecoder(
            ARQUIVO_MUSICA, FPS, log=log, nome="fundo da música")
        fundo_ocioso = ao_vivo.FundoDecoder(
            ARQUIVO_BACKGROUND, FPS, log=log, nome="fundo ocioso")
        painel = Painel(fundo_musica, fundo_ocioso, brilho)

        # O pipeline dá parar.set() no próprio evento ao terminar (inclusive por erro);
        # por isso ele usa um evento só da sessão, ligado ao parar global por esta ponte.
        parar_sessao = threading.Event()

        def ponte():
            while not parar_sessao.is_set():
                if parar.wait(0.2):
                    parar_sessao.set()

        threading.Thread(target=ponte, daemon=True).start()

        pipe = ao_vivo.PipelineAoVivo(
            dev, painel.quadro, fps=FPS, kbps=KBPS, parar=parar_sessao, log=log)
        painel.pipe = pipe
        pipe.contexto = painel.contexto_diagnostico
        try:
            pipe.rodar()
        finally:
            parar_sessao.set()
            fundo_musica.fechar()
            fundo_ocioso.fechar()
            try:
                cmd(123)
            except Exception:
                pass
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
    chave, capa_carregada_para, tentativas, ultimo_erro = None, None, 0, ""
    proxima_tentativa_capa = 0.0
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
                estado["midia_pronta"] = True
                chave = None
                capa_carregada_para = None
                tentativas = 0
                proxima_tentativa_capa = 0.0
            else:
                info = await sessao.try_get_media_properties_async()
                nova = (info.title, info.artist)
                
                if nova != chave:
                    chave = nova
                    capa_carregada_para = None
                    tentativas = 0
                    proxima_tentativa_capa = 0.0

                # A miniatura pode chegar depois dos metadados da faixa. Tenta
                # novamente com intervalo, sem repetir o trabalho a cada polling.
                agora_capa = time.monotonic()
                precisa_carregar_capa = (
                    capa_carregada_para != nova
                    and agora_capa >= proxima_tentativa_capa
                )
                if precisa_carregar_capa:
                    tentativas += 1
                    await asyncio.sleep(0.08)
                    capa_img, cor_capa, cor_viva = await ler_capa(info)
                    
                    if capa_img is not None:
                        estado["capa"] = capa_img
                        estado["cor_capa"] = cor_capa
                        estado["cor_viva"] = cor_viva
                        capa_carregada_para = nova
                    else:
                        proxima_tentativa_capa = time.monotonic() + 1.0

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
                # Só libera o OpenRGB após tentar obter a capa que define a cor inicial.
                if estado.get("capa") is not None or tentativas >= 2:
                    estado["midia_pronta"] = True
        except Exception as exc:
            if str(exc) != ultimo_erro:
                ultimo_erro = str(exc)
                log(f"spotify/midia: {exc}")

        await asyncio.sleep(0.15)


async def main_async():
    await vigiar_spotify()


def main():
    watcher_playlist = SpotifyPlaylistWatcher(atualizar_capa_playlist, log)
    watcher_playlist.iniciar()
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
    watcher_playlist.fechar()
    t.join(timeout=5)
    log("parado")


if __name__ == "__main__":
    main()
