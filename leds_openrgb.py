"""Cor da capa do álbum nos LEDs do PC (OpenRGB) com o mesmo brilho da tela.

Requisitos:
  - pip install openrgb-python
  - OpenRGB aberto como administrador, com o servidor SDK ligado (porta 6742).

O brilho vem de brilho_para() (0 a 100), o mesmo valor que controla a tela,
então o modo noite vale para os dois.

Com música tocando, os LEDs seguem a cor da capa. Sem música, usam a cor do
perfil PERFIL_OCIOSO do OpenRGB.

Escritas gentis com o controlador da placa (ASRock B450M Steel Legend):
  - reúne as zonas listadas em ZONAS em um envio por dispositivo por passo;
    as demais conservam as cores informadas pelo OpenRGB;
  - mantém uma pausa equivalente aos antigos envios separados;
  - o perfil é lido uma única vez e guardado em ARQUIVO_PERFIL; reconectar NÃO
    recarrega o perfil (load_profile mexe em todos os dispositivos). Apague o
    arquivo, ou use RELER_PERFIL = True, para ler de novo.
"""
import colorsys
import datetime
import json
import os
import threading
import time
import math

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

_ultima_cor_debug = None

HOST = "127.0.0.1"
PORTA = 6742
DISPOSITIVOS = ["ASRock"]       # trechos do nome dos dispositivos; None = todos
ZONAS = ["Addressable Header", "PCH", "IO Cover"]  # trechos do nome das zonas escritas; None = todas
ARQUIVO_PERFIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perfil_ocioso.json")
RELER_PERFIL = False            # True = ignora o cache e recarrega o perfil no OpenRGB
PERFIL_OCIOSO = 'purple rain'  # perfil do OpenRGB sem música; None = COR_PADRAO
COR_PADRAO = (255, 255, 255)    # usada se o perfil não for encontrado
# --- TRANSITION RESPOSTA RÁPIDA E FLUIDA ---
FADE_PASSOS = 12        # Um meio-termo seguro para a ASRock
FADE_INTERVALO = 0.0    # O intervalo por passo pode ser zero, pois a trava será por zona
INTERVALO_ZONAS = 0.10  # 100ms entre gravações para reduzir a carga no controlador
INTERVALO_ADDRESSABLE_EXTRA = 0.20  # Limita a frequência de gravações no header ARGB durante fades
CHECA_A_CADA = 0.05     # Resposta quase instantânea ao trocar de música
ESPERA_ERRO = 10                 # segundos antes de tentar reconectar
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

    # --- TRATAMENTO AUTOMÁTICO PARA ROSAS DE TOM MAIS AVERMELHADO ---
    # Identifica rosas pelo matiz antes das regras de vermelho/roxo. Isso inclui
    # tons como (254, 95, 153), sem puxar laranjas/salmões cujo matiz é mais quente.
    h_original, s_original, v_original = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if r > 120 and 0.92 <= h_original <= 0.985 and s_original >= 0.25 and b > g:
        # Preserva um pouco do verde e reforça menos o azul para um rosa mais claro,
        # evitando que o resultado se aproxime demais do roxo/magenta.
        fator_brilho = 255.0 / max(r, 1) if r > 220 else 1.0
        r_mod = min(255, int(r * fator_brilho))
        g_mod = min(255, int(g * 0.85))
        b_mod = min(255, int(b * 1.05 * fator_brilho))
        return r_mod, g_mod, b_mod
    
    # --- TRATAMENTO PARA ROXO / MAGENTA (Ex: Graduation - Kanye West) ---
    # Protege tons onde Vermelho e Azul são altos e próximos para não caírem no bloco do vermelho
    if r > 100 and b > 80 and abs(r - b) < 80 and g < min(r, b) * 0.8:
        fator_brilho = 180.0 / max(r, b) if max(r, b) < 180 else 1.0
        r_mod = min(255, int(r * fator_brilho))
        g_mod = int(g * 0.2)  
        b_mod = min(255, int(b * fator_brilho * 1.15)) 
        return r_mod, g_mod, b_mod

    # --- TRATAMENTO AUTOMÁTICO PARA ROSA / CARMIM ---
    # Detecta rosa mesmo quando a capa contém bastante verde (ex.: 255, 76, 128).
    # Vermelhos com matiz próximo de 360° (ex.: 190, 42, 50) seguem a calibração
    # de vermelho dominante abaixo. Os rosas já tratados acima são preservados.
    # Os limites relativos também mantêm laranjas/salmões fora deste ajuste.
    if (
        r > 120
        and h_original < 0.985
        and g < r * 0.35
        and r * 0.2 < b < r * 0.8
        and b > g * 1.1
    ):
        # Mantém a forte presença do vermelho, corta o verde e reforça o azul
        fator_brilho = 220.0 / max(r, 1) if r > 220 else 1.0
        r_mod = min(255, int(r * fator_brilho))
        g_mod = 0  # Corta o verde a zero para anular qualquer hipótese de o LED puxar para laranja ou amarelo
        b_mod = min(255, int(b * 1.3 * fator_brilho)) # Reforça o azul físico para manter o aspeto "pink/carmim"
        return r_mod, g_mod, b_mod

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
            cor_base = (min(255, int(r * fator_brilho)), min(255, int(g * fator_brilho)), min(255, int(b * fator_brilho)))
        else:
            # Mantém o verde e o azul extremamente baixos para fechar no tom vinho/bordô
            g = int(r * 0.05)
            b = int(r * 0.02)
            
            fator_brilho = 95.0 / max(r, 1) if r > 40 else 1.0
            cor_base = (min(255, int(r * fator_brilho)), min(255, int(g * fator_brilho)), min(255, int(b * fator_brilho)))

        # Laranjas escuros e saturados (ex.: 152, 68, 27): suaviza a cor usando
        # o matiz e a intensidade originais, antes dos cortes de verde e azul.
        # O peso cai gradualmente fora dessa faixa; vermelhos, rosas e tons
        # claros ou pouco saturados conservam a calibração anterior.
        peso_laranja = max(0.0, min(
            (h_original - 0.025) / 0.02,
            (0.11 - h_original) / 0.02,
            (s_original - 0.55) / 0.15,
            (v_original - 0.30) / 0.15,
            (0.85 - v_original) / 0.15,
            1.0,
        ))
        if peso_laranja > 0.0:
            peso_laranja = peso_laranja ** 2 * (3 - 2 * peso_laranja)
            s_suave = s_original * 0.95  # Preserva mais laranja, com menos mistura de branco.
            v_suave = min(v_original * 0.60, 95 / 255)
            cor_suave = tuple(int(c * 255) for c in
                              colorsys.hsv_to_rgb(h_original, s_suave, v_suave))
            return tuple(round(a + (b - a) * peso_laranja)
                         for a, b in zip(cor_base, cor_suave))
        return cor_base
        
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


def _escalar(cor, fator):
    return tuple(int(c * fator) for c in cor)


def _quer(nome):
    if DISPOSITIVOS is None:
        return True
    return any(t.lower() in nome.lower() for t in DISPOSITIVOS)


def _quer_zona(nome):
    if ZONAS is None:
        return True
    return any(t.lower() in nome.lower() for t in ZONAS)


def _chave(dev, zona):
    return f"{dev.name}|{zona.name}"


def _zonas(devs):
    """(chave, dispositivo, zona) das zonas que o script controla."""
    return [(_chave(d, z), d, z) for d in devs for z in d.zones if _quer_zona(z.name)]


def cores_alvo(estado, brilho, zonas, perfil):
    """Cores desejadas por LED de cada zona, chave = 'dispositivo|zona'."""
    fator = max(0, min(100, brilho)) / 100
    modo = estado.get("modo_exibicao", "dinamico")
    midia = estado
    if modo == "spotify" and estado.get("musica") is None:
        midia = estado.get("ultima_midia") or estado
    tocando = modo != "video" and midia.get("musica") is not None
    
    # Se não está tocando, evita completamente o processamento da capa e usa a cor padrão/ociosa de forma limpa
    if not tocando:
        alvo = {}
        for chave, _d, z in zonas:
            n = len(z.leds)
            base = perfil.get(chave)
            if base is None or len(base) != n:
                base = [COR_PADRAO] * n
            alvo[chave] = [_escalar(c, fator) for c in base]
        return alvo

    cor = midia.get("cor_viva") or midia["cor_capa"]
    album = realcar(cor)
    alvo = {}
    for chave, _d, z in zonas:
        n = len(z.leds)
        base = [album] * n
        alvo[chave] = [_escalar(c, fator) for c in base]
    return alvo


def _suavizar_fator(t):
    """Curva ease-in-out (senoidal) para suavizar início e fim da transição."""
    return (1 - math.cos(t * math.pi)) / 2


def _misturar(a, b, t):
    t_suave = _suavizar_fator(t)
    return {
        k: [
            tuple(int(x + (y - x) * t_suave) for x, y in zip(ca, cb))
            for ca, cb in zip(a[k], b[k])
        ]
        for k in b
    }


def _enviar_etapa(zonas, cores, escrito, etapa, passos, parar, log):
    """Um UPDATELEDS por placa; o driver ASRock já percorre todos os LEDs."""
    dispositivos = []
    for _chave_zona, dev, _zona in zonas:
        if not any(dev is existente for existente in dispositivos):
            dispositivos.append(dev)
    for dev in dispositivos:
        selecionadas = [(k, z) for k, d, z in zonas if d is dev]
        alteradas = [(k, z) for k, z in selecionadas if escrito.get(k) != cores[k]]
        if not alteradas:
            continue
        pacote = list(dev.colors)
        if len(pacote) != len(dev.leds):
            raise ValueError("OpenRGB: quantidade de cores da placa inconsistente")
        offset = 0
        for zona in dev.zones:
            chave = _chave(dev, zona)
            quantidade = len(zona.leds)
            if chave in cores:
                if len(cores[chave]) != quantidade:
                    raise ValueError(f"OpenRGB: quantidade de cores inconsistente em {chave}")
                pacote[offset:offset + quantidade] = [RGBColor(*c) for c in cores[chave]]
            offset += quantidade
        if offset != len(pacote):
            raise ValueError("OpenRGB: mapa de zonas não corresponde aos LEDs da placa")
        resumo = {k: cores[k][0] if cores[k] else None for k, _z in selecionadas}
        log(f"openrgb: envio único dispositivo={dev.name} leds={len(pacote)} cores={resumo} etapa={etapa}/{passos}")
        inicio_envio = time.monotonic()
        dev.set_colors(pacote)
        for chave, _zona in selecionadas:
            escrito[chave] = list(cores[chave])
        log(f"openrgb: envio único concluído etapa={etapa}/{passos} duração={time.monotonic() - inicio_envio:.3f}s")
        # Mantém o ritmo anterior mesmo com apenas um pacote SDK.
        intervalo = INTERVALO_ZONAS * len(selecionadas)
        if passos > 1 and any("addressable header" in z.name.lower() for _k, z in selecionadas):
            intervalo += INTERVALO_ADDRESSABLE_EXTRA
        if parar.wait(intervalo):
            return True
    return False


def _modo_fixo(dev):
    nomes = [m.name.lower() for m in dev.modes]
    for modo in ("direct", "static"):
        if modo in nomes:
            try:
                if nomes[dev.active_mode] == modo:
                    return f"{modo} (já ativo)"
            except Exception:
                pass
            dev.set_mode(modo)
            return modo
    return None


def _ler_perfil(cli, log):
    """Cor do perfil por zona. Usa o cache; só chama load_profile se não houver cache."""
    if not PERFIL_OCIOSO:
        return {}

    if not RELER_PERFIL and os.path.exists(ARQUIVO_PERFIL):
        try:
            with open(ARQUIVO_PERFIL, encoding="utf-8") as f:
                dados = json.load(f)
            if dados.get("perfil") == PERFIL_OCIOSO and dados.get("versao") == 2:
                perfil = {k: [tuple(c) for c in v] for k, v in dados["cores"].items()}
                log(f"openrgb: perfil '{PERFIL_OCIOSO}' do cache ({len(perfil)} zonas)")
                return perfil
        except Exception as exc:
            log(f"openrgb: cache do perfil inválido ({exc}); lendo do OpenRGB")
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
                if not _quer(d.name):
                    continue
                for z in d.zones:
                    if _quer_zona(z.name) and len(z.colors):
                        perfil[_chave(d, z)] = [(c.red, c.green, c.blue) for c in z.colors]
        finally:
            leitor.disconnect()
        log(f"openrgb: perfil '{PERFIL_OCIOSO}' lido ({len(perfil)} zonas)")
        try:
            with open(ARQUIVO_PERFIL, "w", encoding="utf-8") as f:
                json.dump({"versao": 2, "perfil": PERFIL_OCIOSO, "cores": perfil}, f)
        except OSError as exc:
            log(f"openrgb: não consegui salvar o cache do perfil: {exc}")
        return perfil
    except Exception as exc:
        log(f"openrgb: não consegui ler o perfil: {exc}")
        return {}


def _laco(estado, brilho_para, parar, log):
    cli = None
    zonas = []
    perfil = {}
    atual = None
    escrito = {}            # última cor realmente enviada, por zona
    ultimo_erro = ""
    inicio_espera_midia = time.monotonic()
    avisou_espera_midia = False
    liberou_por_timeout = False
    while not parar.is_set():
        if not estado.get("midia_pronta", False) and not liberou_por_timeout:
            if time.monotonic() - inicio_espera_midia < 5.0:
                if not avisou_espera_midia:
                    log("openrgb: aguardando a leitura inicial da mídia antes de enviar cores")
                    avisou_espera_midia = True
                if parar.wait(0.05):
                    return
                continue
            log("openrgb: leitura inicial da mídia demorou; iniciando com o estado disponível")
            liberou_por_timeout = True

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
                zonas = _zonas(devs)
                log(f"openrgb: controlando zonas {[k for k, _, _ in zonas]} {modos}")
                atual = None
                escrito = {}
                ultimo_erro = ""

            novo = cores_alvo(estado, brilho_para(datetime.datetime.now()), zonas, perfil)
            
            if novo != atual:
                # Se já tínhamos uma cor sendo exibida, a transição parte da cor ATUAL exata de onde parou
                inicio = atual if atual is not None else novo
                passos = 1 if atual is None else FADE_PASSOS
                log(f"openrgb diagnóstico: início fade passos={passos} musica={estado.get('musica')!r} cor_capa={estado.get('cor_capa')} cor_viva={estado.get('cor_viva')} alvo={ {k: v[0] if v else None for k, v in novo.items()} }")
                
                for i in range(1, passos + 1):
                    # Se a música mudar durante o fade, atualiza o alvo suavemente sem quebrar
                    alvo_momento = cores_alvo(estado, brilho_para(datetime.datetime.now()), zonas, perfil)
                    if alvo_momento != novo:
                        log(f"openrgb diagnóstico: alvo mudou durante fade etapa={i}/{passos} anterior={ {k: v[0] if v else None for k, v in novo.items()} } novo={ {k: v[0] if v else None for k, v in alvo_momento.items()} }")
                        novo = alvo_momento

                    cores = _misturar(inicio, novo, i / passos)
                    if _enviar_etapa(zonas, cores, escrito, i, passos, parar, log):
                        return

                    if i < passos and parar.wait(FADE_INTERVALO):
                        return
                
                atual = novo
                log("openrgb diagnóstico: fade concluído (envios SDK; sem confirmação física dos LEDs)")

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
