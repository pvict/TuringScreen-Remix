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
import math

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

_ultima_cor_debug = None

HOST = "127.0.0.1"
PORTA = 6742
DISPOSITIVOS = ["ASRock"]       # trechos do nome dos dispositivos; None = todos
PERFIL_OCIOSO = "purple rain"   # perfil do OpenRGB sem música; None = COR_PADRAO
COR_PADRAO = (255, 255, 255)    # usada se o perfil não for encontrado
# Aumentamos o número de passos e reduzimos o intervalo para ficar extremamente fluido (60 FPS visual)
FADE_PASSOS = 40        # Antes era 20; mais micro-passos geram uma curva contínua
FADE_INTERVALO = 0.02   # 0.02s = 20ms entre cada atualização (~50 atualizações por segundo)
CHECA_A_CADA = 0.2      # Checa atualizações do estado mais frequentemente[cite: 2]
ESPERA_ERRO = 5                 # segundos antes de tentar reconectar
PROTOCOLO = 3                   # 3 evita a espera de ~10 s do pedido de plugins
SATURACAO_MIN = 0.85             # Sobe de 0.65 para 0.85 -> remove o tom "lavado/rosa" dos LEDs
LIMITE_CINZA = 0.08              # Se não for realmente cinza/branco, força tom cheio

def realcar(cor):
    """Preserva a profundidade exata dos tons (como azul-marinho ou vermelho-escuro) sem clarear demais os LEDs."""
    global _ultima_cor_debug
    r, g, b = cor[0], cor[1], cor[2]

    # --- REGISTO DE DEPURAÇÃO (Apenas imprime se a cor mudar) ---
    if cor != _ultima_cor_debug:
        print(f"[DEBUG CORES] Original lido -> R: {r}, G: {g}, B: {b}")
        _ultima_cor_debug = cor
    
    # --- TRATAMENTO PARA TONS CIANO / AZUL-ESVERDEADO ESCURO ---
    # Se o Verde e o Azul estão altos e próximos, mas o vermelho é menor (evita o ciano estourado)
    if abs(g - b) < 15 and g > 40 and b > 40 and r < g:
        g_mod = int(g * 0.20)
        b_mod = int(b * 0.70)
        r_mod = int(r * 0.50)
        
        fator_brilho = 90.0 / max(b_mod, 1)
        return min(255, int(r_mod * fator_brilho)), min(255, int(g_mod * fator_brilho)), min(255, int(b_mod * fator_brilho))

    r_f, g_f, b_f = r / 255.0, g / 255.0, b / 255.0
    h, s, v = colorsys.rgb_to_hsv(r_f, g_f, b_f)
    
    is_vermelho_puro = (r > b * 1.3) or (h < 0.08 or h > 0.92)
    
    if 0.68 <= h <= 0.88 and s > 0.15 and not is_vermelho_puro:
        r_f = min(1.0, r_f * 1.65)
        b_f = max(0.0, b_f * 0.45)
        if s >= LIMITE_CINZA:
            s = max(s, 0.75)
            v = min(v, 0.80)
        r_f, g_f, b_f = colorsys.hsv_to_rgb(h, s, v)
        return int(r_f * 255), int(g_f * 255), int(b_f * 255)

    # 1. Se o VERMELHO for dominante
    if r > g and r > b:
        if g > r * 0.45:
            b = int(b * 0.35)
            g = int(g * 0.8)  
            fator_brilho = 255.0 / max(r, 1)
            return min(255, int(r * fator_brilho)), min(255, int(g * fator_brilho)), min(255, int(b * fator_brilho))
        else:
            # Mantém o verde e o azul extremamente baixos para fechar no tom vinho/bordô
            g = int(r * 0.05)
            b = int(r * 0.02)
            
            fator_brilho = 95.0 / max(r, 1) if r > 40 else 1.0
            return min(255, int(r * fator_brilho)), min(255, int(g * fator_brilho)), min(255, int(b * fator_brilho))
        
    # 2. Se o AZUL for dominante
    if b > r and b > g:
        r = min(r, int(b * 0.25))
        g = min(g, int(b * 0.15))
        
        fator_brilho = 110.0 / max(b, 1) if b < 140 else 1.0
        return min(255, int(r * fator_brilho)), min(255, int(g * fator_brilho)), min(255, int(b * fator_brilho))

    # 3. Para outras cores
    if s >= LIMITE_CINZA:
        s = max(s, 0.75)
        v = min(v, 0.80)
    r_f, g_f, b_f = colorsys.hsv_to_rgb(h, s, v)
    return int(r_f * 255), int(g_f * 255), int(b_f * 255)


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

def _suavizar_fator(t):
    """Aplica uma curva ease-in-out (senoidal) para suavizar início e fim da transição."""
    return (1 - math.cos(t * math.pi)) / 2

def _misturar(a, b, t):
    # Aplica a curva de suavização ao fator t (que vai de 0.0 a 1.0)
    t_suave = _suavizar_fator(t)
    return {
        k: [
            tuple(int(x + (y - x) * t_suave) for x, y in zip(ca, cb))
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