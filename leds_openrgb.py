"""Cor da capa do álbum nos LEDs do PC (OpenRGB) com o mesmo brilho da tela.

Requisitos:
  - pip install openrgb-python
  - OpenRGB aberto como administrador, com o servidor SDK ligado (porta 6742).

O brilho vem de brilho_para() (0 a 100), o mesmo valor que controla a tela,
então o modo noite vale para os dois.

Com música tocando, os LEDs seguem a cor da capa. Sem música, usam as cores
do perfil PERFIL_OCIOSO do OpenRGB (lidas uma vez ao conectar, por LED).
Se o perfil tiver animação, os LEDs ficam estáticos nas cores dele.
"""
import colorsys
import datetime
import threading
import time

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

HOST = "127.0.0.1"
PORTA = 6742
DISPOSITIVOS = ["ASRock"]       # trechos do nome dos dispositivos; None = todos
PERFIL_OCIOSO = "purple rain"   # perfil do OpenRGB sem música; None = COR_PADRAO
COR_PADRAO = (255, 255, 255)    # usada se o perfil não for encontrado
FADE_PASSOS = 20                 # passos da transição de cor
FADE_INTERVALO = 0.3           # segundos entre os passos
CHECA_A_CADA = 0.5              # segundos entre verificações
ESPERA_ERRO = 5                 # segundos antes de tentar reconectar
PROTOCOLO = 3                   # 3 evita a espera de ~10 s do pedido de plugins
SATURACAO_MIN = 0.65             # 0 a 1; 1.0 = cores cheias (menos branco), 0.6 = pastel
LIMITE_CINZA = 0.05             # abaixo disso a cor é tratada como cinza/branco


def realcar(cor):
    """Deixa a cor média da capa mais viva para os LEDs."""
    r, g, b = (c / 255 for c in cor[:3])
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s >= LIMITE_CINZA:  # capa em preto e branco continua branca
        s = max(s, SATURACAO_MIN)
    r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def _escalar(cores, fator):
    return [tuple(int(c * fator) for c in cor) for cor in cores]


def cores_alvo(estado, brilho, devs, perfil):
    """Cores desejadas por LED, para cada dispositivo (chave = id)."""
    fator = max(0, min(100, brilho)) / 100
    tocando = estado["musica"] is not None
    cor = estado.get("cor_viva") or estado["cor_capa"]
    album = realcar(cor) if tocando else None
    alvo = {}
    for d in devs:
        n = len(d.leds)
        if tocando:
            base = [album] * n
        else:
            base = perfil.get(d.id)
            if base is None or len(base) != n:
                base = [COR_PADRAO] * n
        alvo[d.id] = _escalar(base, fator)
    return alvo


def _misturar(a, b, t):
    return {
        k: [
            tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))
            for ca, cb in zip(a[k], b[k])
        ]
        for k in b
    }


def _quer(nome):
    if DISPOSITIVOS is None:
        return True
    return any(t.lower() in nome.lower() for t in DISPOSITIVOS)


def _modo_fixo(dev):
    nomes = [m.name.lower() for m in dev.modes]
    for modo in ("direct", "static"):
        if modo in nomes:
            dev.set_mode(modo)
            return modo
    return None


def _ler_perfil(cli, log):
    """Carrega o perfil no OpenRGB e lê as cores por LED que ele aplicou."""
    if not PERFIL_OCIOSO:
        return {}
    try:
        nomes = [p.name for p in cli.profiles]
        if PERFIL_OCIOSO.lower() not in [n.lower() for n in nomes]:
            log(f"openrgb: perfil '{PERFIL_OCIOSO}' não encontrado; perfis: {nomes}")
            return {}
        cli.load_profile(PERFIL_OCIOSO)
        time.sleep(1.0)  # dá tempo de o servidor aplicar o perfil
        leitor = OpenRGBClient(
            HOST, PORTA, "tela_completa_leitura", protocol_version=PROTOCOLO
        )
        try:
            perfil = {}
            for d in leitor.devices:
                if _quer(d.name):
                    perfil[d.id] = [(c.red, c.green, c.blue) for c in d.colors]
        finally:
            leitor.disconnect()
        total = sum(len(v) for v in perfil.values())
        log(f"openrgb: perfil '{PERFIL_OCIOSO}' lido ({total} LEDs)")
        return perfil
    except Exception as exc:
        log(f"openrgb: não consegui ler o perfil: {exc}")
        return {}


def _laco(estado, brilho_para, parar, log):
    cli = None
    devs = []
    perfil = {}
    atual = None
    ultimo_erro = ""
    while not parar.is_set():
        try:
            if cli is None:
                log(f"openrgb: conectando (protocolo {PROTOCOLO or 'auto'})...")
                t0 = time.time()
                cli = OpenRGBClient(
                    HOST, PORTA, "tela_completa", protocol_version=PROTOCOLO
                )
                log(f"openrgb: conectou em {time.time() - t0:.1f}s")
                todos = [d.name for d in cli.devices]
                devs = [d for d in cli.devices if _quer(d.name)]
                log(f"openrgb: dispositivos {todos}")
                perfil = _ler_perfil(cli, log)
                modos = [_modo_fixo(d) for d in devs]
                log(f"openrgb: controlando {[d.name for d in devs]} {modos}")
                atual = None
                ultimo_erro = ""

            novo = cores_alvo(estado, brilho_para(datetime.datetime.now()), devs, perfil)
            if novo != atual:
                passos = 1 if atual is None else FADE_PASSOS
                inicio = novo if atual is None else atual
                for i in range(1, passos + 1):
                    cores = _misturar(inicio, novo, i / passos)
                    for d in devs:
                        d.set_colors([RGBColor(*c) for c in cores[d.id]])
                    if i < passos and parar.wait(FADE_INTERVALO):
                        return
                atual = novo
        except Exception as exc:
            if str(exc) != ultimo_erro:
                ultimo_erro = str(exc)
                log(f"openrgb: {exc}")
            try:
                if cli is not None:
                    cli.disconnect()
            except Exception:
                pass
            cli = None
            parar.wait(ESPERA_ERRO)
            continue
        parar.wait(CHECA_A_CADA)


def iniciar(estado, brilho_para, parar, log):
    t = threading.Thread(
        target=_laco, args=(estado, brilho_para, parar, log), daemon=True
    )
    t.start()
    return t
