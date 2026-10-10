"""Animação de troca de capa de álbum.

Gera, para cada instante da transição, a capa (círculo RGBA do mesmo tamanho da
capa normal) e a cor do halo. Funciona pelo relógio, não por número de quadros:
com mais quadros por segundo fica mais fluida; com poucos continua durando o
mesmo tempo.

Estilos:
  "giro"      a capa gira como uma moeda: a antiga some de lado, a nova abre
  "fade_zoom" a antiga encolhe e some enquanto a nova cresce e aparece
"""
import math
import time

from PIL import Image

DURACAO = 0.9      # segundos
ESTILO = "giro"    # "giro" ou "fade_zoom"

PAUSA_ESCALA = 0.70
PAUSA_FADE_ARCO = 0.35
PAUSA_ZOOM = 1.30
PAUSA_GLOW_TAU = 0.50
RETORNO_GLOW_TAU = 0.32
RETORNO_ARCO = 0.90


def _curva_lottie():
    """Tabela da curva de encolhimento do Live icon.lottie: (0.65, 0, 1, 1).

    Resolve o Bezier uma vez na abertura; cada quadro usa só uma interpolação.
    """
    valores = []
    for indice in range(257):
        x = indice / 256
        baixo, alto = 0.0, 1.0
        for _ in range(20):
            t = (baixo + alto) / 2
            bx = 3 * (1 - t) ** 2 * t * 0.65 + 3 * (1 - t) * t ** 2 + t ** 3
            if bx < x:
                baixo = t
            else:
                alto = t
        t = (baixo + alto) / 2
        valores.append(3 * (1 - t) * t ** 2 + t ** 3)
    valores[0], valores[-1] = 0.0, 1.0
    return tuple(valores)


_ZOOM_LOTTIE = _curva_lottie()


class PausaSuave:
    """Primeiro apaga o arco, depois encolhe a capa; play percorre a volta.

    Uma pausa/play no meio da animação parte da posição atual, sem saltos.
    O relógio é o mesmo dos quadros H.264, não o tempo do polling do Spotify.
    """
    def __init__(self):
        self.tempo = 0.0
        self.velocidade = 0.0
        self.glow = 1.0
        self.preenchimento = 1.0
        self._retorno_t = None
        self._retorno_origem = 1.0
        self._retorno_alpha = 1.0
        self._arco_alpha = 1.0
        self._fade_t = 0.0
        self._fade_origem = 1.0
        self._pausado = False
        self._curva_capa = 0.0
        self._zoom_retorno_origem = 0.0
        self._glow_retorno_origem = 1.0

    def quadro(self, pausado, dt):
        if pausado != self._pausado:
            self._pausado = pausado
            if pausado:
                self._retorno_t = None
                self._fade_t = 0.0
                self._fade_origem = self._arco_alpha
            else:
                # Em uma pausa completa, o arco volta percorrendo de 0% até a
                # posição atual. Se interromper o fade, parte do valor visível.
                self._retorno_origem = 0.0 if self._arco_alpha < 0.001 else self.preenchimento
                self.preenchimento = self._retorno_origem
                self._retorno_alpha = self._arco_alpha
                self._retorno_t = 0.0
                # O glow percorre o crescimento restante da capa, inclusive se
                # o usuário retomar antes de terminar o encolhimento.
                self._zoom_retorno_origem = self._curva_capa
                self._glow_retorno_origem = self.glow
        total = PAUSA_FADE_ARCO + PAUSA_ZOOM
        alvo = 1.0 if pausado else -1.0
        # Inverter play/pausa durante o zoom também amortece a mudança de direção.
        self.velocidade += (alvo - self.velocidade) * -math.expm1(-dt / 0.055)
        self.tempo = min(total, max(0.0, self.tempo + self.velocidade * dt))
        if self.tempo == 0.0 or self.tempo == total:
            self.velocidade = 0.0
        if pausado:
            self._fade_t = min(PAUSA_FADE_ARCO, self._fade_t + dt)
            self._arco_alpha = self._fade_origem * (1.0 - _suave(self._fade_t / PAUSA_FADE_ARCO))
        if not pausado and self._retorno_t is not None:
            self._retorno_t += dt
            u_arco = min(1.0, self._retorno_t / RETORNO_ARCO)
            suave = u_arco ** 3 * (u_arco * (6 * u_arco - 15) + 10)
            self.preenchimento = self._retorno_origem + (1.0 - self._retorno_origem) * suave
            self._arco_alpha = self._retorno_alpha + (1.0 - self._retorno_alpha) * _suave(
                min(1.0, self._retorno_t / 0.20))
            if u_arco >= 1.0:
                self._retorno_t = None
        if not pausado:
            if self._retorno_t is None:
                self._arco_alpha = 1.0
        u = min(1.0, max(0.0, (self.tempo - PAUSA_FADE_ARCO) / PAUSA_ZOOM)) * 256
        indice = min(255, int(u))
        curva = _ZOOM_LOTTIE[indice] + (_ZOOM_LOTTIE[indice + 1] - _ZOOM_LOTTIE[indice]) * (u - indice)
        # Amortece a chegada aos extremos da referência, sem um corte na velocidade.
        curva = curva * curva * (3.0 - 2.0 * curva)
        self._curva_capa = curva
        if pausado:
            self.glow *= math.exp(-dt / PAUSA_GLOW_TAU)
            if self.glow < 0.001:
                self.glow = 0.0
        elif self._zoom_retorno_origem > 0.0:
            crescimento = min(1.0, max(0.0, 1.0 - curva / self._zoom_retorno_origem))
            self.glow = self._glow_retorno_origem + (1.0 - self._glow_retorno_origem) * crescimento
        else:
            # Uma pausa curta pode apagar parte do glow antes de encolher a
            # capa. Neste caso, ela já está inteira e a luz volta suavemente.
            self.glow += (1.0 - self.glow) * -math.expm1(-dt / RETORNO_GLOW_TAU)
            if 1.0 - self.glow < 0.001:
                self.glow = 1.0
        if curva > 0.0:
            # O renderizador arredonda o alfa para 8 bits. Evita chegar a 255
            # por arredondamento antes de a capa completar seu crescimento.
            self.glow = min(self.glow, 254 / 255)
        return 1.0 - (1.0 - PAUSA_ESCALA) * curva, self._arco_alpha

    def resetar(self):
        self.__init__()


def _suave(t):
    """easeInOutCubic: começa e termina devagar."""
    t = min(1.0, max(0.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def _mix(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def _sombrear(img, k):
    r, g, b, a = img.split()
    escurecer = lambda v: int(v * k)
    return Image.merge("RGBA", (r.point(escurecer), g.point(escurecer), b.point(escurecer), a))


def _no_centro(tam, img, largura, altura):
    canvas = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    largura = min(tam, largura)
    altura = min(tam, altura)
    if largura < 1 or altura < 1:
        return canvas
    peq = img.resize((largura, altura), Image.BILINEAR)
    canvas.alpha_composite(peq, ((tam - largura) // 2, (tam - altura) // 2))
    return canvas


def _escalar_alfa(img, escala, alfa, tam):
    lado = int(round(tam * escala))
    copia = img.copy()
    copia.putalpha(img.getchannel("A").point(lambda v: int(v * alfa)))
    return _no_centro(tam, copia, lado, lado)


def _giro(capa_ant, capa_nova, e, tam):
    ang = e * math.pi
    c = abs(math.cos(ang))
    face = capa_ant if ang < math.pi / 2 else capa_nova
    return _no_centro(tam, _sombrear(face, 0.55 + 0.45 * c), int(round(tam * c)), tam)


def _fade_zoom(capa_ant, capa_nova, e, tam):
    saida = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    if capa_ant is not None:
        saida.alpha_composite(_escalar_alfa(capa_ant, 1.0 - 0.12 * e, 1.0 - e, tam))
    saida.alpha_composite(_escalar_alfa(capa_nova, 0.88 + 0.12 * e, e, tam))
    return saida


class TransicaoCapa:
    """Uma troca de capa. Chame quadro() a cada quadro de vídeo."""

    def __init__(self, capa_ant, cor_ant, capa_nova, cor_nova,
                 inicio=None, duracao=DURACAO, estilo=ESTILO):
        self.capa_ant, self.cor_ant = capa_ant, cor_ant
        self.capa_nova, self.cor_nova = capa_nova, cor_nova
        self.inicio = time.monotonic() if inicio is None else inicio
        self.duracao = duracao
        # sem capa anterior (primeira música), só entra com fade e zoom
        self.estilo = estilo if capa_ant is not None else "fade_zoom"

    def quadro(self, agora=None):
        """Devolve (capa RGBA, cor do halo, passou_da_metade, terminou)."""
        agora = time.monotonic() if agora is None else agora
        t = min(1.0, max(0.0, (agora - self.inicio) / self.duracao))
        e = _suave(t)
        tam = self.capa_nova.width
        if self.estilo == "giro":
            capa = _giro(self.capa_ant, self.capa_nova, e, tam)
        else:
            capa = _fade_zoom(self.capa_ant, self.capa_nova, e, tam)
        cor = _mix(self.cor_ant, self.cor_nova, e) if self.cor_ant else self.cor_nova
        return capa, cor, e >= 0.5, t >= 1.0
