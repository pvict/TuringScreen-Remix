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
