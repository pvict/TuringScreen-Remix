"""Janela de controle do Turing Vinyl. Execute: python interface.py.

Tkinter + Pillow, sem navegador embutido. Prévia pequena com alvo de 60 FPS.
O script original roda em um processo separado, com comandos em pipes locais.
"""
import base64
import ctypes
import io
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import font as tkfont
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from aparencia_windows import AcrilicoWindows
from fundo_usuario import ConversorFundo
from controle_interface import (PREFIXO, brilho_horario, carregar, salvar,
                                reservar_execucao, liberar_execucao)

RAIZ = Path(__file__).resolve().parent
FUNDO = "#17191d"
CARTAO = "#24272d"
BORDA = "#464b55"
TEXTO = "#f1f3f6"
SECUNDARIO = "#adb3bd"
PRATA = "#d4d8df"
MODOS = [
    ("dinamico", "Dinâmico", "Spotify + vídeo de fundo", PRATA),
    ("spotify", "Só Spotify", "O vinil continua aqui", "#c6cbd4"),
    ("video", "Só vídeo", "Seu fundo, sempre", "#bdc5d0"),
]
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UI_FPS = 60


def proximo_quadro(widget, inicio, callback):
    """Agenda no relógio: desenhar não acrescenta atraso a cada quadro."""
    agora = time.monotonic()
    indice = math.floor((agora - inicio) * UI_FPS) + 1
    espera = math.ceil((inicio + indice / UI_FPS - agora) * 1000)
    return widget.after(max(1, espera), callback)


def rgb(cor):
    return tuple(int(cor[i:i + 2], 16) for i in (1, 3, 5))


def misturar(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(rgb(a), rgb(b)))


def cor_album(cor):
    """O mesmo matiz do álbum, clareado para os controles terem contraste."""
    canais = tuple(max(0, min(255, round(c))) for c in cor[:3])
    ganho = max(1.0, 165 / max(max(canais), 1))
    acento = "#%02x%02x%02x" % tuple(min(255, round(c * ganho)) for c in canais)
    return misturar(acento, "#f8f9fb", 0.23)


def animar_hover(widget, valor):
    """Um timer curto por entrada/saída; não mantém um loop ocioso."""
    if getattr(widget, "hover_job", None):
        widget.after_cancel(widget.hover_job)
    widget.hover_alvo = bool(valor)
    origem = widget.hover_t
    inicio = time.monotonic()

    def passo():
        t = min(1.0, (time.monotonic() - inicio) / 0.15)
        s = t * t * (3 - 2 * t)
        widget.hover_t = origem + (float(valor) - origem) * s
        widget.desenhar()
        widget.hover_job = proximo_quadro(widget, inicio, passo) if t < 1 else None
    passo()


def arredondar(canvas, x1, y1, x2, y2, raio=18, **opcoes):
    pontos = [x1 + raio, y1, x2 - raio, y1, x2, y1, x2, y1 + raio,
              x2, y2 - raio, x2, y2, x2 - raio, y2, x1 + raio, y2,
              x1, y2, x1, y2 - raio, x1, y1 + raio, x1, y1]
    return canvas.create_polygon(pontos, smooth=True, splinesteps=24, **opcoes)


def registrar_fontes():
    if os.name == "nt":
        gdi = ctypes.WinDLL("gdi32")
        gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        for arquivo in (RAIZ / "assets" / "fonts").glob("*.ttf"):
            gdi.AddFontResourceExW(str(arquivo), 0x10, None)


def halo(cor, tamanho=(330, 180), intensidade=75):
    img = Image.new("RGBA", tamanho)
    d = ImageDraw.Draw(img)
    d.ellipse((50, 44, tamanho[0] - 50, tamanho[1] - 44), fill=(*rgb(cor), intensidade))
    return img.filter(ImageFilter.GaussianBlur(24))


_MASCARAS_FUNDO = None


def fundo_janela(cor=PRATA, acrilico=False):
    global _MASCARAS_FUNDO
    if _MASCARAS_FUNDO is None:
        luz = Image.new("L", (1000, 720))
        d = ImageDraw.Draw(luz)
        d.ellipse((490, -240, 1140, 360), fill=26)
        d.ellipse((-210, 450, 410, 1050), fill=15)
        brilho = Image.new("L", luz.size)
        ImageDraw.Draw(brilho).ellipse((570, 112, 943, 320), fill=42)
        _MASCARAS_FUNDO = (luz.filter(ImageFilter.GaussianBlur(65)),
                           brilho.filter(ImageFilter.GaussianBlur(43)))
    img = Image.new("RGBA", (1000, 720), "#000000" if acrilico else FUNDO)
    if not acrilico:
        luz = Image.new("RGBA", img.size, cor)
        luz.putalpha(_MASCARAS_FUNDO[0])
        img = Image.alpha_composite(img, luz)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((30, 92, 970, 322), 26,
                        fill="#000000" if acrilico else misturar(FUNDO, cor, 0.10),
                        outline=misturar(FUNDO, cor, 0.25))
    if not acrilico:
        brilho = Image.new("RGBA", img.size, cor)
        brilho.putalpha(_MASCARAS_FUNDO[1])
        img = Image.alpha_composite(img, brilho)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((30, 508, 970, 643), 24,
                        fill="#000000" if acrilico else CARTAO, outline=BORDA)
    return img


def disco_base():
    img = Image.new("RGBA", (270, 270))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 262, 262), fill="#08090c", outline="#7c8797", width=2)
    for r in range(121, 59, -2):
        cor = (25 + (r % 6), 27 + (r % 8), 31 + (r % 5), 255)
        d.ellipse((135 - r, 135 - r, 135 + r, 135 + r), outline=cor)
    reflexo = Image.new("RGBA", img.size)
    rd = ImageDraw.Draw(reflexo)
    for desloc in range(38):
        alfa = round(30 * (1 - desloc / 38))
        rd.pieslice((14, 14, 256, 256), 210 + desloc, 226 + desloc, fill=(205, 216, 232, alfa))
        rd.pieslice((14, 14, 256, 256), 30 + desloc, 46 + desloc, fill=(205, 216, 232, alfa))
    img = Image.alpha_composite(img, reflexo.filter(ImageFilter.GaussianBlur(3)))
    return img


class Botao(tk.Canvas):
    def __init__(self, master, texto, comando, largura=150, altura=40,
                 primario=False, fundo=FUNDO):
        super().__init__(master, width=largura, height=altura, bg=fundo,
                         highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self.texto, self.comando, self.primario = texto, comando, primario
        self.cor = PRATA
        self.ativo, self.hover = True, False
        self.hover_t, self.hover_job = 0.0, None
        self.fonte = master.f_sans if hasattr(master, "f_sans") else ("DM Sans", 11)
        self.bind("<Button-1>", lambda e: self.acionar())
        self.bind("<Return>", lambda e: self.acionar())
        self.bind("<space>", lambda e: self.acionar())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<FocusIn>", lambda e: self.desenhar())
        self.bind("<FocusOut>", lambda e: self.desenhar())
        self.desenhar()

    def _hover(self, valor):
        self.hover = valor
        animar_hover(self, valor)

    def acionar(self):
        if self.ativo:
            self.focus_set()
            self.comando()
        return "break"

    def atualizar(self, texto=None, ativo=None, cor=None):
        if texto is not None:
            self.texto = texto
        if ativo is not None:
            self.ativo = ativo
        if cor is not None:
            self.cor = cor
        self.desenhar()

    def desenhar(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        preenchimento = (misturar(self.cor, "#ffffff", self.hover_t * 0.12) if self.primario
                         else misturar("#30343b", self.cor, self.hover_t * 0.12))
        if not self.ativo:
            preenchimento = "#292d34"
        borda = self.cor if self.focus_get() is self else (
            preenchimento if self.primario else misturar(BORDA, self.cor, self.hover_t * 0.5))
        arredondar(self, 1, 1, w - 1, h - 1, 13, fill=preenchimento, outline=borda)
        self.create_text(w / 2, h / 2 - 1 - self.hover_t, text=self.texto, font=self.fonte,
                         fill=("#191c22" if self.primario else TEXTO) if self.ativo else SECUNDARIO)


class Seletor(tk.Canvas):
    def __init__(self, master, selecionado, comando, fonte, fonte_pequena):
        super().__init__(master, width=940, height=115, bg=FUNDO,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.comando, self.fonte, self.fonte_pequena = comando, fonte, fonte_pequena
        self.indice = next(i for i, m in enumerate(MODOS) if m[0] == selecionado)
        self.x = self.indice * 306 + 12
        self.cor = MODOS[self.indice][3]
        self.mascara_halo = halo("#ffffff", (340, 124), 90).getchannel("A")
        self.halo_atual = Image.new("RGBA", self.mascara_halo.size, self.cor)
        self.halo_atual.putalpha(self.mascara_halo)
        self.hover_indice, self.hover_t, self.hover_job = None, 0.0, None
        self.hover_alvo = False
        self.job = None
        # Preserva a forma do símbolo; aplica a mesma cor dos demais ícones.
        # A máscara só é lida ao abrir; a imagem é refeita apenas se a cor mudar.
        with Image.open(RAIZ / "assets" / "icons" / "spotify-white.png") as imagem:
            self.mascara_spotify = imagem.convert("RGBA").resize(
                (24, 24), Image.Resampling.LANCZOS).getchannel("A")
        self.cor_spotify, self.foto_spotify = None, None
        self.onda = tuple((24 * i / 40, 55 - 8 * math.sin(2 * math.pi * i / 40))
                          for i in range(41))
        self.bind("<Button-1>", self.clicar)
        self.bind("<Motion>", self.mover_mouse)
        self.bind("<Leave>", lambda e: animar_hover(self, False))
        self.bind("<Left>", lambda e: self.escolher(max(0, self.indice - 1)))
        self.bind("<Right>", lambda e: self.escolher(min(2, self.indice + 1)))
        self.bind("<Home>", lambda e: self.escolher(0))
        self.bind("<End>", lambda e: self.escolher(2))
        self.bind("<FocusIn>", lambda e: self.desenhar())
        self.bind("<FocusOut>", lambda e: self.desenhar())
        self.desenhar()

    def clicar(self, e):
        self.focus_set()
        self.escolher(max(0, min(2, int((e.x - 10) / 306))))

    def mover_mouse(self, e):
        indice = max(0, min(2, int((e.x - 10) / 306)))
        if indice != self.hover_indice or not self.hover_alvo:
            self.hover_indice = indice
            self.hover_t = 0.0
            animar_hover(self, True)

    def atualizar_cor(self, cor):
        if cor != self.cor:
            self.cor = cor
            self.halo_atual = Image.new("RGBA", self.mascara_halo.size, cor)
            self.halo_atual.putalpha(self.mascara_halo)
        self.desenhar()

    def escolher(self, indice):
        if indice == self.indice:
            return "break"
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None
        origem_x = self.x
        self.indice = indice
        inicio, destino = time.monotonic(), indice * 306 + 12
        self.comando(MODOS[indice][0])

        def passo():
            t = min(1.0, (time.monotonic() - inicio) / 0.32)
            s = t * t * (3 - 2 * t)
            self.x = origem_x + (destino - origem_x) * s
            self.desenhar()
            self.job = proximo_quadro(self, inicio, passo) if t < 1 else None
        passo()
        return "break"

    def desenhar(self):
        self.delete("all")
        base = "#000000" if self["bg"] == "#000000" else "#23262c"
        arredondar(self, 0, 11, 940, 104, 22, fill=base, outline=BORDA)
        self.foto_halo = ImageTk.PhotoImage(self.halo_atual)
        self.create_image(self.x + 150, 58, image=self.foto_halo)
        sobre_selecao = self.hover_t if self.hover_indice == self.indice else 0.0
        preenchimento = misturar("#343941", self.cor, 0.13 + sobre_selecao * 0.06)
        arredondar(self, self.x, 21, self.x + 304, 94, 17,
                   fill=preenchimento, outline=misturar("#4a505a", self.cor, 0.45))
        for i, (_, titulo, descricao, cor) in enumerate(MODOS):
            x = 42 + i * 306
            selecionado = i == self.indice
            sobre = self.hover_t if self.hover_indice == i else 0.0
            if sobre and not selecionado:
                arredondar(self, i * 306 + 12, 23, i * 306 + 314, 92, 17,
                           fill=misturar("#23262c", self.cor, 0.065 * sobre),
                           outline=misturar("#23262c", self.cor, 0.28 * sobre))
            icone = self.cor if selecionado else misturar("#969da8", self.cor, sobre * 0.7)
            if i == 0:
                pontos = tuple(coordenada for dx, y in self.onda for coordenada in (x + dx, y))
                self.create_line(*pontos, fill=icone, width=2, capstyle=tk.ROUND,
                                 joinstyle=tk.ROUND)
            elif i == 1:
                if icone != self.cor_spotify:
                    imagem = Image.new("RGBA", (24, 24), icone)
                    imagem.putalpha(self.mascara_spotify)
                    self.foto_spotify = ImageTk.PhotoImage(imagem)
                    self.cor_spotify = icone
                self.create_image(x + 12, 55, image=self.foto_spotify)
            else:
                arredondar(self, x - 1, 47, x + 24, 65, 4, fill="", outline=icone, width=2)
                self.create_line(x + 7, 70, x + 16, 70, fill=icone, width=2)
            self.create_text(x + 38, 44, text=titulo, anchor="nw", font=self.fonte,
                             fill=TEXTO if selecionado else "#c7cbd2")
            self.create_text(x + 38, 67, text=descricao, anchor="nw", font=self.fonte_pequena,
                             fill=SECUNDARIO)
        if self.focus_get() is self:
            arredondar(self, 2, 13, 938, 102, 22, fill="", outline=misturar(BORDA, self.cor, 0.60))


class Slider(tk.Canvas):
    def __init__(self, master, valor, comando, largura=620, fundo=CARTAO):
        super().__init__(master, width=largura, height=46, bg=fundo,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        # Reserva o raio máximo do halo (19 px), com folga nas duas pontas.
        self.margem = 24
        self.percurso = largura - 2 * self.margem
        self.valor, self.comando = valor, comando
        self.cor, self.job = PRATA, None
        self.hover_t, self.hover_job = 0.0, None
        self.bind("<Enter>", lambda e: animar_hover(self, True))
        self.bind("<Leave>", lambda e: animar_hover(self, False))
        self.bind("<Button-1>", self.arrastar)
        self.bind("<B1-Motion>", self.arrastar)
        self.bind("<ButtonRelease-1>", self.soltar)
        self.bind("<Left>", lambda e: self.tecla(-1))
        self.bind("<Right>", lambda e: self.tecla(1))
        self.bind("<Prior>", lambda e: self.tecla(10))
        self.bind("<Next>", lambda e: self.tecla(-10))
        self.bind("<Home>", lambda e: self.tecla(-100))
        self.bind("<End>", lambda e: self.tecla(100))
        self.bind("<FocusIn>", lambda e: self.desenhar())
        self.bind("<FocusOut>", lambda e: self.desenhar())
        self.desenhar()

    def arrastar(self, e):
        if self["state"] == "disabled":
            return "break"
        self.focus_set()
        self.valor = round(max(0, min(100, (e.x - self.margem) / self.percurso * 100)))
        self.desenhar()
        self.comando(self.valor, False)

    def soltar(self, e):
        if self["state"] != "disabled":
            self.comando(self.valor, True)

    def tecla(self, incremento):
        if self["state"] == "disabled":
            return "break"
        self.valor = max(0, min(100, self.valor + incremento))
        self.desenhar()
        self.comando(self.valor, False)
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(250, lambda: self.comando(self.valor, True))
        return "break"

    def atualizar(self, valor=None, cor=None):
        if valor is not None:
            self.valor = valor
        if cor:
            self.cor = cor
        self.desenhar()

    def desenhar(self):
        self.delete("all")
        x = self.margem + self.valor / 100 * self.percurso
        self.create_line(self.margem, 23, self.margem + self.percurso, 23,
                         fill="#484e59", width=6, capstyle=tk.ROUND)
        if self.valor:
            self.create_line(self.margem, 23, x, 23, fill=self.cor, width=6, capstyle=tk.ROUND)
        for raio, cor in ((16 + self.hover_t * 3, misturar(self["bg"], self.cor, 0.08 + 0.06 * self.hover_t)),
                          (12 + self.hover_t * 2, misturar(self["bg"], self.cor, 0.18)),
                          (8 + self.hover_t, self.cor)):
            self.create_oval(x - raio, 23 - raio, x + raio, 23 + raio, fill=cor, outline="")
        self.create_oval(x - 3, 20, x + 3, 26, fill="#fbfcff", outline="")


class Aparencia(tk.Canvas):
    """Painel local: ajustar o vidro não envia comandos à tela ou ao OpenRGB."""
    def __init__(self, master):
        super().__init__(master, width=356, height=242, bg=CARTAO,
                         highlightthickness=0, bd=0)
        self.janela = master
        self.f_sans = master.f_sans
        arredondar(self, 1, 1, 355, 241, 18, fill=CARTAO, outline=BORDA)
        self.create_text(22, 22, text="APARÊNCIA", anchor="nw", font=master.f_pequena,
                         fill=SECUNDARIO)
        self.fechar = Botao(self, "Fechar", master.alternar_aparencia,
                            largura=65, altura=28, fundo=CARTAO)
        self.create_window(270, 13, window=self.fechar, anchor="nw")
        self.toggle = Botao(self, "", master.alternar_acrilico, largura=312,
                            altura=36, fundo=CARTAO)
        self.create_window(22, 55, window=self.toggle, anchor="nw")
        self.create_text(24, 109, text="Opacidade do fundo", anchor="nw",
                         font=master.f_sans, fill=TEXTO)
        self.valor = self.create_text(332, 109, anchor="ne", font=master.f_sans, fill=TEXTO)
        self.slider = Slider(self, master.config_usuario["opacidade_fundo"],
                             master.mudar_opacidade, largura=336)
        self.create_window(10, 128, window=self.slider, anchor="nw")
        self.transparencia = self.create_text(24, 183, anchor="nw", font=master.f_pequena,
                                               fill=SECUNDARIO)
        self.nota = self.create_text(24, 207, anchor="nw", font=master.f_pequena,
                                    fill=SECUNDARIO, width=309)

    def atualizar(self):
        janela = self.janela
        config = janela.config_usuario
        self.toggle.atualizar(texto="Acrílico · ligado" if config["acrilico"] else "Acrílico · desligado",
                              cor=janela.cor_modo)
        self.fechar.atualizar(cor=janela.cor_modo)
        self.slider.atualizar(config["opacidade_fundo"], janela.cor_modo)
        self.itemconfigure(self.valor, text=f'{config["opacidade_fundo"]}%')
        self.itemconfigure(self.transparencia, text=f'Transparência: {100 - config["opacidade_fundo"]}%')
        self.itemconfigure(self.nota, text=janela.acrilico.mensagem)
        ajustavel = config["acrilico"] and janela.acrilico.ajustavel
        # Evita apresentar como funcional um ajuste que o Windows não aceitou.
        self.slider.configure(state="normal" if ajustavel else "disabled",
                              takefocus=ajustavel, cursor="hand2" if ajustavel else "arrow")


class PainelFundo(tk.Canvas):
    def __init__(self, master):
        super().__init__(master, width=452, height=248, bg=CARTAO,
                         highlightthickness=0, bd=0)
        self.janela, self.f_sans = master, master.f_sans
        arredondar(self, 1, 1, 451, 247, 18, fill=CARTAO, outline=BORDA)
        self.create_text(22, 22, text="VÍDEO DE FUNDO", anchor="nw", font=master.f_pequena,
                         fill=SECUNDARIO)
        self.fechar = Botao(self, "Fechar", master.alternar_painel_fundo,
                            largura=65, altura=28, fundo=CARTAO)
        self.create_window(365, 13, window=self.fechar, anchor="nw")
        self.nome = self.create_text(22, 57, anchor="nw", font=master.f_faixa, fill=TEXTO)
        self.create_text(22, 86, text="Recorte central para preencher a tela.", anchor="nw",
                         font=master.f_pequena, fill=SECUNDARIO)
        self.escolher = Botao(self, "Escolher vídeo", master.escolher_fundo,
                              largura=238, altura=37, primario=True, fundo=CARTAO)
        self.create_window(22, 113, window=self.escolher, anchor="nw")
        self.padrao = Botao(self, "Restaurar padrão", master.acao_fundo,
                            largura=162, altura=37, fundo=CARTAO)
        self.create_window(268, 113, window=self.padrao, anchor="nw")
        self.percentual = self.create_text(428, 159, anchor="ne", font=master.f_pequena,
                                           fill=SECUNDARIO)
        self.barra = self.create_line(25, 183, 428, 183, fill="#484e59", width=5,
                                      capstyle=tk.ROUND)
        self.preenchimento = self.create_line(25, 183, 25, 183, width=5, capstyle=tk.ROUND)
        self.status = self.create_text(24, 202, anchor="nw", font=master.f_pequena,
                                       fill=SECUNDARIO, width=405)

    def atualizar(self):
        janela = self.janela
        ocupado = janela.fundo_convertendo
        nome = (janela.fundo_nome_em_preparo if ocupado else
                janela.config_usuario["video_ocioso_nome"] or "Fundo padrão")
        self.itemconfigure(self.nome, text=janela.encurtar(nome, 404, janela.f_faixa))
        self.escolher.atualizar(texto="Preparando…" if ocupado else "Escolher vídeo",
                                ativo=not ocupado, cor=janela.cor_modo)
        self.padrao.atualizar(texto="Cancelar" if ocupado else "Restaurar padrão",
                              ativo=ocupado or janela.config_usuario["video_ocioso"] is not None,
                              cor=janela.cor_modo)
        self.fechar.atualizar(cor=janela.cor_modo)
        progresso = janela.fundo_progresso
        valor = 0 if progresso is None else progresso
        self.coords(self.preenchimento, 25, 183, 25 + 403 * valor / 100, 183)
        self.itemconfigure(self.preenchimento, fill=janela.cor_modo,
                            state="normal" if valor > 0 else "hidden")
        self.itemconfigure(self.percentual, text=f"{valor}%" if ocupado and progresso is not None else "")
        texto, cor = janela.fundo_status
        self.itemconfigure(self.status, text=texto if len(texto) <= 140 else texto[:137] + "…", fill=cor)


class Motor:
    """A janela nunca acessa o hardware; acompanha somente o processo que iniciou."""
    def __init__(self):
        self.proc = None
        self.eventos = queue.Queue(maxsize=24)
        self.erros = deque(maxlen=12)
        self.lock = threading.Lock()
        self._config_pendente = None
        self.iniciando = False

    def evento(self, tipo, dados):
        try:
            self.eventos.put_nowait((tipo, dados))
        except queue.Full:
            try:
                self.eventos.get_nowait()
            except queue.Empty:
                pass
            self.eventos.put_nowait((tipo, dados))

    def iniciar(self, config):
        if self.iniciando or (self.proc and self.proc.poll() is None):
            return
        self.iniciando = True
        with self.lock:
            self._config_pendente = dict(config)

        def executar():
            try:
                # Detecta também scripts anteriores que ainda não usam o mutex.
                if os.name == "nt":
                    consulta = ("Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" "
                                "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
                    resultado = subprocess.run(["powershell", "-NoProfile", "-Command", consulta],
                                               capture_output=True, text=True, timeout=12,
                                               creationflags=SEM_JANELA)
                    if resultado.returncode != 0:
                        raise RuntimeError("Não foi possível conferir se a tela já está em uso.")
                    processos = json.loads(resultado.stdout or "[]")
                    if isinstance(processos, dict):
                        processos = [processos]
                    if any("tela_completa.py" in (p.get("CommandLine") or "").lower() for p in (processos or [])):
                        raise RuntimeError("O script já está aberto no terminal. Encerre-o com Ctrl+C antes de iniciar aqui.")
                self.erros.clear()
                ambiente = dict(os.environ, PYTHONIOENCODING="utf-8")
                python = Path(sys.executable)
                if python.name.lower() == "pythonw.exe":
                    python = python.with_name("python.exe")
                self.proc = subprocess.Popen(
                    [str(python), "-u", str(RAIZ / "tela_completa.py"), "--interface"],
                    cwd=RAIZ, env=ambiente, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    creationflags=SEM_JANELA)
                self.enviar({"acao": "configurar"})
                self.evento("iniciado", None)
                for linha in self.proc.stdout:
                    pos = linha.find(PREFIXO)
                    if pos >= 0:
                        try:
                            self.evento("estado", json.loads(linha[pos + len(PREFIXO):]))
                        except ValueError:
                            pass
                    elif any(palavra in linha.lower() for palavra in ("error", "erro", "exception", "traceback")):
                        self.erros.append(linha.strip())
                codigo = self.proc.wait()
                self.evento("parado", "\n".join(self.erros) if codigo else "")
            except Exception as exc:
                self.evento("erro", str(exc))
            finally:
                self.iniciando = False
        threading.Thread(target=executar, name="JanelaMotor", daemon=True).start()

    def enviar(self, dados):
        with self.lock:
            if dados.get("acao") == "configurar":
                if isinstance(dados.get("config"), dict):
                    self._config_pendente = dict(dados["config"])
                dados = {"acao": "configurar", "config": self._config_pendente}
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.proc.stdin.write(json.dumps(dados, ensure_ascii=True) + "\n")
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass

    def parar(self):
        self.enviar({"acao": "parar"})


class Janela(tk.Tk):
    def __init__(self):
        registrar_fontes()
        super().__init__()
        self.title("Turing Vinyl")
        self.geometry("1000x720")
        self.resizable(False, False)
        self.configure(bg=FUNDO)
        self.img_icone_header = None
        self.carregar_icone()
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.config_usuario = carregar()
        self.acrilico = AcrilicoWindows(self)
        self._vidro_desenhado = False
        self.aparencia_job = None
        self.aparencia_aberta = False
        self.conversor_fundo = ConversorFundo()
        self.fundo_painel_aberto, self.fundo_convertendo = False, False
        self.fundo_nome_em_preparo = ""
        self.fundo_progresso, self.fundo_pendente = 0, False
        self.fundo_status = ("Escolha um vídeo. A preparação acontece aqui no app.", SECUNDARIO)
        self.motor = Motor()
        self.encerrando, self.iniciando, self.rodando = False, False, False
        self.config_job = None
        self.midia, self.capa = None, None
        self.capa_preview = None
        self.capa_base = disco_base()
        self.cor_modo = next(m[3] for m in MODOS if m[0] == self.config_usuario["modo"])
        self.cor_destino, self.tema_job = self.cor_modo, None
        self.disco_job = None
        self._visual = {}
        self._angulo_gui, self._escala_gui = 0.0, 1.0
        self._angulo_externo = 0.0
        self._tempo_preview = time.monotonic()
        self._relogio_preview = self._tempo_preview
        self._preview_chave = None
        familias = set(tkfont.families(self))
        serif = next((f for f in familias if f.startswith("Fraunces")), "Georgia")
        sans = "DM Sans" if "DM Sans" in familias else "Segoe UI"
        self.f_sans = tkfont.Font(family=sans, size=-14)
        self.f_pequena = tkfont.Font(family=sans, size=-12)
        self.f_modo = tkfont.Font(family=sans, size=-17, weight="bold")
        self.f_titulo = tkfont.Font(family=serif, size=-35, weight="bold")
        self.f_logo = tkfont.Font(family=serif, size=-27, weight="bold")
        self.f_faixa = tkfont.Font(family=sans, size=-17, weight="bold")
        self.f_valor = tkfont.Font(family=sans, size=-29, weight="bold")
        self.canvas = tk.Canvas(self, width=1000, height=720, bg=FUNDO, bd=0, highlightthickness=0)
        self.canvas.pack()
        self.fundo_atual = fundo_janela(self.cor_modo)
        self.img_fundo = ImageTk.PhotoImage(self.fundo_atual)
        self.fundo_item = self.canvas.create_image(0, 0, image=self.img_fundo, anchor="nw")
        self.desenhar()
        self.after(50, self.eventos)
        self.after(100, self.aplicar_aparencia)
        self.bind("<Escape>", lambda e: self.fechar_paineis())

    def texto(self, x, y, texto, fonte=None, cor=TEXTO, **opcoes):
        return self.canvas.create_text(x, y, text=texto, font=fonte or self.f_sans,
                                       fill=cor, anchor="nw", **opcoes)

    def carregar_icone(self):
        """PNG no cabeçalho e ícone de várias resoluções na janela do Windows."""
        try:
            with Image.open(RAIZ / "assets" / "icons" / "turing-vinyl.png") as arquivo:
                imagem = arquivo.convert("RGBA")
                self.img_icone_app = ImageTk.PhotoImage(
                    imagem.resize((256, 256), Image.Resampling.LANCZOS))
                self.img_icone_header = ImageTk.PhotoImage(
                    imagem.resize((40, 40), Image.Resampling.LANCZOS))
            self.iconphoto(True, self.img_icone_app)
            if os.name == "nt":
                self.iconbitmap(str(RAIZ / "assets" / "icons" / "turing-vinyl.ico"))
        except (OSError, tk.TclError):
            # O controle da tela continua disponível se o recurso não for copiado.
            pass

    def desenhar(self):
        c = self.canvas
        if self.img_icone_header is not None:
            c.create_image(47, 43, image=self.img_icone_header)
        else:
            c.create_oval(33, 29, 61, 57, fill="#30343b", outline="#a4adba")
            c.create_oval(42, 38, 52, 48, fill=PRATA, outline="")
        self.texto(73, 22, "Turing Vinyl", self.f_logo)
        self.texto(74, 55, "MÚSICA, LUZ E MOVIMENTO", self.f_pequena, SECUNDARIO)
        self.fundo_botao = Botao(self, "Vídeo de fundo", self.alternar_painel_fundo,
                                 largura=146, altura=32)
        c.create_window(496, 28, window=self.fundo_botao, anchor="nw")
        self.aparencia_botao = Botao(self, "Aparência", self.alternar_aparencia,
                                     largura=94, altura=32)
        c.create_window(654, 28, window=self.aparencia_botao, anchor="nw")
        self.status_ponto = c.create_oval(771, 41, 778, 48, fill="#89929f", outline="")
        self.status_texto = self.texto(790, 35, "Pronto para conectar", cor=SECUNDARIO)
        self.hero_rotulo = self.texto(61, 116, "UM OUTRO JEITO DE OUVIR", self.f_pequena, self.cor_modo)
        self.texto(59, 147, "Sua música,\nno seu ritmo.", self.f_titulo)
        self.faixa_texto = self.texto(63, 252, "Escolha um modo para começar.", self.f_faixa)
        self.artista_texto = self.texto(63, 279, "A tela acompanha. Você aproveita.", cor="#c6cbd2")
        self.disco_item = c.create_image(775, 207)
        self.atualizar_disco()
        self.texto(34, 347, "MODO DE EXIBIÇÃO", self.f_pequena, SECUNDARIO)
        self.modo_dica = self.texto(970, 347, "Mude o clima, sem interromper a música.",
                                   self.f_pequena, SECUNDARIO)
        c.itemconfigure(self.modo_dica, anchor="ne")
        self.seletor = Seletor(self, self.config_usuario["modo"], self.mudar_modo, self.f_modo, self.f_pequena)
        c.create_window(30, 375, window=self.seletor, anchor="nw")
        self.texto(59, 530, "Brilho da tela", self.f_modo)
        self.valor_texto = self.texto(683, 523, "", self.f_valor)
        self.auto = Botao(self, "Automático", self.automatico, largura=112, altura=32, fundo=CARTAO)
        c.create_window(536, 522, window=self.auto, anchor="nw")
        self.slider = Slider(self, self.valor_brilho(), self.mudar_brilho)
        c.create_window(48, 558, window=self.slider, anchor="nw")
        self.brilho_dica = self.texto(63, 610, "", self.f_pequena, SECUNDARIO)
        self.power = Botao(self, "Desligar tela", self.alternar_tela, largura=162, altura=43, fundo=CARTAO)
        c.create_window(774, 553, window=self.power, anchor="nw")
        self.texto(787, 607, "Os LEDs seguem o modo.", self.f_pequena, SECUNDARIO)
        self.iniciar = Botao(self, "Iniciar exibição", self.alternar_motor, largura=194, altura=42, primario=True)
        c.create_window(774, 664, window=self.iniciar, anchor="nw")
        self.aviso = self.texto(34, 675, "Suas preferências ficam salvas neste computador.", self.f_pequena, SECUNDARIO)
        self.aparencia_painel = Aparencia(self)
        self.aparencia_item = c.create_window(610, 77, window=self.aparencia_painel,
                                              anchor="nw", state="hidden")
        self.fundo_painel = PainelFundo(self)
        self.fundo_painel_item = c.create_window(514, 77, window=self.fundo_painel,
                                                 anchor="nw", state="hidden")
        self.atualizar_controles()
        self.fundo_painel.atualizar()

    def alternar_painel_fundo(self):
        self.fundo_painel_aberto = not self.fundo_painel_aberto
        if self.fundo_painel_aberto and self.aparencia_aberta:
            self.alternar_aparencia()
        self.fundo_painel.atualizar()
        self.canvas.itemconfigure(self.fundo_painel_item,
                                   state="normal" if self.fundo_painel_aberto else "hidden")
        if self.fundo_painel_aberto:
            self.canvas.tag_raise(self.fundo_painel_item)

    def escolher_fundo(self):
        if self.fundo_convertendo or self.conversor_fundo.ocupado:
            return
        caminho = filedialog.askopenfilename(
            parent=self, title="Escolher vídeo de fundo",
            filetypes=[("Vídeos", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.wmv *.gif"),
                       ("Todos os arquivos", "*.*")])
        if not caminho or self.encerrando:
            return
        if self.conversor_fundo.iniciar(caminho):
            self.fundo_convertendo = True
            self.fundo_nome_em_preparo = Path(caminho).name
            self.fundo_progresso = 0
            self.fundo_status = ("Abrindo o vídeo…", SECUNDARIO)
            self.fundo_botao.atualizar(texto="Preparando fundo…")
            self.fundo_painel.atualizar()

    def acao_fundo(self):
        if self.fundo_convertendo:
            self.conversor_fundo.cancelar()
            self.fundo_status = ("Cancelando a preparação…", SECUNDARIO)
            self.fundo_painel.atualizar()
        else:
            self.definir_fundo(None, "")

    def definir_fundo(self, caminho, nome):
        anterior = (self.config_usuario["video_ocioso"], self.config_usuario["video_ocioso_nome"])
        self.config_usuario.update(video_ocioso=caminho, video_ocioso_nome=nome)
        try:
            salvar(self.config_usuario)
        except OSError:
            self.config_usuario.update(video_ocioso=anterior[0], video_ocioso_nome=anterior[1])
            self.fundo_status = ("Não foi possível salvar o novo fundo. O anterior foi mantido.", "#e8c18b")
            self.fundo_painel.atualizar()
            return
        self.motor.enviar({"acao": "configurar", "config": self.config_usuario})
        self.fundo_progresso = 100 if caminho else 0
        self.fundo_pendente = self.rodando or self.iniciando
        texto = ("Fundo pronto. Será usado no modo ocioso." if self.fundo_pendente else
                 "Fundo salvo. Ele será usado ao iniciar a exibição.")
        self.fundo_status = (texto, SECUNDARIO)
        self.fundo_painel.atualizar()
        self.mensagem("Novo fundo salvo." if caminho else "Fundo padrão selecionado.")

    def eventos_fundo(self):
        try:
            while True:
                tipo, dados = self.conversor_fundo.eventos.get_nowait()
                if tipo == "progresso":
                    self.fundo_progresso, texto = dados
                    self.fundo_status = (texto, SECUNDARIO)
                else:
                    self.fundo_convertendo = False
                    self.fundo_botao.atualizar(texto="Vídeo de fundo")
                    if self.conversor_fundo.cancelamento.is_set():
                        tipo = "cancelado"
                    if tipo == "pronto":
                        self.definir_fundo(dados["caminho"], dados["nome"])
                    elif tipo == "cancelado":
                        self.fundo_progresso = 0
                        self.fundo_status = ("Preparação cancelada. O fundo anterior foi mantido.", SECUNDARIO)
                    elif tipo == "erro":
                        self.fundo_progresso = 0
                        self.fundo_status = (dados, "#e8c18b")
                self.fundo_painel.atualizar()
        except queue.Empty:
            pass

    def fechar_paineis(self):
        if self.aparencia_aberta:
            self.alternar_aparencia()
        if self.fundo_painel_aberto:
            self.alternar_painel_fundo()

    def alternar_aparencia(self):
        self.aparencia_aberta = not self.aparencia_aberta
        if self.aparencia_aberta and self.fundo_painel_aberto:
            self.alternar_painel_fundo()
        self.aparencia_painel.atualizar()
        self.canvas.itemconfigure(self.aparencia_item, state="normal" if self.aparencia_aberta else "hidden")
        if self.aparencia_aberta:
            self.canvas.tag_raise(self.aparencia_item)

    def guardar_aparencia(self):
        try:
            salvar(self.config_usuario)
        except OSError:
            self.mensagem("Não foi possível salvar a aparência.", "#e8c18b")

    def alternar_acrilico(self):
        self.config_usuario["acrilico"] = not self.config_usuario["acrilico"]
        self.aplicar_aparencia()
        self.guardar_aparencia()

    def mudar_opacidade(self, valor, aplicar):
        self.config_usuario["opacidade_fundo"] = valor
        self.aparencia_painel.atualizar()
        if self.aparencia_job is None:
            self.aparencia_job = self.after(25, self.aplicar_aparencia)
        if aplicar:
            self.guardar_aparencia()

    def aplicar_aparencia(self):
        if self.encerrando:
            return
        if self.aparencia_job:
            self.after_cancel(self.aparencia_job)
            self.aparencia_job = None
        tinta = misturar("#181b20", self.cor_modo, 0.14)
        ativo = self.acrilico.aplicar(self.config_usuario["acrilico"],
                                      self.config_usuario["opacidade_fundo"], tinta)
        if self._vidro_desenhado != ativo:
            self._vidro_desenhado = ativo
            fundo = "#000000" if ativo else FUNDO
            self.configure(bg=fundo)
            self.canvas.configure(bg=fundo)
            for widget in (self.seletor, self.iniciar, self.aparencia_botao, self.fundo_botao):
                widget.configure(bg=fundo)
                widget.desenhar()
            for widget in (self.auto, self.power, self.slider):
                widget.configure(bg="#000000" if ativo else CARTAO)
                widget.desenhar()
            if self.tema_job:
                self.after_cancel(self.tema_job)
                self.tema_job = None
            self.fundo_atual = fundo_janela(self.cor_modo, ativo)
            self.img_fundo.paste(self.fundo_atual)
            self.cor_destino = self.cor_modo
            self.atualizar_tema()
        self.aparencia_painel.atualizar()

    def valor_brilho(self):
        if not self.config_usuario["tela_ligada"]:
            return 0
        return self.config_usuario["brilho"] or brilho_horario()

    def atualizar_disco(self):
        capa_visivel = self.capa_preview if self.config_usuario["modo"] != "video" else None
        lado = max(1, round(114 * self._escala_gui))
        chave = (id(capa_visivel), round(self._angulo_gui, 3),
                 round(self._angulo_externo, 3), lado,
                 self.cor_modo if capa_visivel is None else None)
        if chave == self._preview_chave:
            return
        self._preview_chave = chave
        disco = self.capa_base.rotate(-self._angulo_externo, resample=Image.Resampling.BICUBIC)
        if capa_visivel is not None:
            capa = capa_visivel.rotate(-self._angulo_gui, resample=Image.Resampling.BICUBIC)
            if lado != 114:
                capa = capa.resize((lado, lado), Image.Resampling.BICUBIC)
            disco.alpha_composite(capa, ((270 - lado) // 2, (270 - lado) // 2))
        else:
            d = ImageDraw.Draw(disco)
            d.ellipse((78, 78, 192, 192), fill=misturar("#3b424d", self.cor_modo, 0.22), outline=self.cor_modo)
            d.ellipse((116, 116, 154, 154), fill="#292f39", outline=self.cor_modo, width=2)
            d.ellipse((132, 132, 138, 138), fill=self.cor_modo)
        if hasattr(self, "img_disco"):
            self.img_disco.paste(disco)
        else:
            self.img_disco = ImageTk.PhotoImage(disco)
            self.canvas.itemconfigure(self.disco_item, image=self.img_disco)

    def cor_desejada(self):
        cor = (self.midia or {}).get("cor")
        if self.config_usuario["modo"] != "video" and cor:
            return cor_album(cor)
        return next(m[3] for m in MODOS if m[0] == self.config_usuario["modo"])

    def atualizar_tema(self):
        destino = self.cor_desejada()
        if destino == self.cor_destino:
            return
        self.cor_destino = destino
        if self.tema_job:
            self.after_cancel(self.tema_job)
        origem, fundo_origem = self.cor_modo, self.fundo_atual
        # As máscaras de blur são reutilizadas. Só prepara o novo fundo uma vez.
        fundo_destino = fundo_janela(destino, self.acrilico.ativo)
        inicio = time.monotonic()
        ultimo_acrilico = inicio - 1

        def passo():
            nonlocal ultimo_acrilico
            if self.encerrando:
                self.tema_job = None
                return
            t = 1.0 if self.state() == "iconic" else min(1.0, (time.monotonic() - inicio) / 0.65)
            s = t * t * (3 - 2 * t)
            self.cor_modo = misturar(origem, destino, s)
            self.fundo_atual = fundo_destino if t >= 1 else Image.blend(fundo_origem, fundo_destino, s)
            self.img_fundo.paste(self.fundo_atual)
            self.canvas.itemconfigure(self.hero_rotulo, fill=self.cor_modo)
            self.seletor.atualizar_cor(self.cor_modo)
            for botao in (self.auto, self.power, self.iniciar, self.aparencia_botao, self.fundo_botao):
                botao.atualizar(cor=self.cor_modo)
            self.slider.atualizar(cor=self.cor_modo)
            if self.aparencia_aberta:
                self.aparencia_painel.atualizar()
            if self.fundo_painel_aberto:
                self.fundo_painel.atualizar()
            agora = time.monotonic()
            if self.acrilico.ativo and (agora - ultimo_acrilico >= 0.05 or t >= 1):
                if not self.acrilico.aplicar(True, self.config_usuario["opacidade_fundo"],
                                             misturar("#181b20", self.cor_modo, 0.14)):
                    self.after_idle(self.aplicar_aparencia)
                ultimo_acrilico = agora
            if (self.midia or {}).get("conectada"):
                self.canvas.itemconfigure(self.status_ponto, fill=self.cor_modo)
                self.canvas.itemconfigure(self.status_texto, fill=self.cor_modo)
            if self.capa_preview is None or self.config_usuario["modo"] == "video":
                self.atualizar_disco()
            self.tema_job = proximo_quadro(self, inicio, passo) if t < 1 else None
        passo()

    def atualizar_visual(self, visual):
        self._visual = visual
        if self.disco_job is None:
            self.animar_disco()

    def animar_disco(self):
        self.disco_job = None
        if self.encerrando:
            return
        agora = time.monotonic()
        dt = min(0.1, max(0.001, agora - self._tempo_preview))
        self._tempo_preview = agora
        visual = self._visual
        visivel = (self.rodando and visual.get("visivel", False)
                   and self.config_usuario["modo"] != "video" and self.config_usuario["tela_ligada"])
        velocidade = visual.get("velocidade", 0.0) if visivel else 0.0
        velocidade_externa = visual.get("velocidade_disco", velocidade) if visivel else 0.0
        # Só números atravessam o pipe. O disco 270px é animado localmente a 60 FPS.
        # A correção da fase é amortecida para os pacotes não causarem saltos.
        if visivel:
            previsto = (self._angulo_gui + velocidade * dt) % 360
            alvo = (visual.get("angulo", 0.0)
                    + velocidade * min(0.25, max(0.0, agora - visual.get("instante", agora)))) % 360
            erro = (alvo - previsto + 180) % 360 - 180
            self._angulo_gui = (previsto + erro * -math.expm1(-dt / 0.07)) % 360
            previsto_externo = (self._angulo_externo + velocidade_externa * dt) % 360
            alvo_externo = (visual.get("angulo_disco", visual.get("angulo", 0.0))
                            + velocidade_externa * min(0.25, max(0.0, agora - visual.get("instante", agora)))) % 360
            erro_externo = (alvo_externo - previsto_externo + 180) % 360 - 180
            self._angulo_externo = (previsto_externo + erro_externo * -math.expm1(-dt / 0.07)) % 360
        else:
            erro = erro_externo = 0.0
        escala_alvo = visual.get("escala", 1.0) if visivel else self._escala_gui
        self._escala_gui += (escala_alvo - self._escala_gui) * -math.expm1(-dt / 0.06)
        escala_movendo = abs(escala_alvo - self._escala_gui) > 0.001
        if not escala_movendo:
            self._escala_gui = escala_alvo
        if abs(erro) < 0.02 and velocidade == 0 and visivel:
            self._angulo_gui = visual.get("angulo", self._angulo_gui)
        if abs(erro_externo) < 0.02 and velocidade_externa == 0 and visivel:
            self._angulo_externo = visual.get("angulo_disco", self._angulo_externo)
        minimizada = self.state() == "iconic"
        if not minimizada:
            self.atualizar_disco()
        if (abs(velocidade) > 0.001 or abs(velocidade_externa) > 0.001
                or abs(erro) > 0.02 or abs(erro_externo) > 0.02 or escala_movendo):
            self.disco_job = (self.after(250, self.animar_disco) if minimizada else
                              proximo_quadro(self, self._relogio_preview, self.animar_disco))

    def atualizar_controles(self):
        valor = self.valor_brilho()
        ligada = self.config_usuario["tela_ligada"]
        self.canvas.itemconfigure(self.valor_texto, text=f"{valor}%" if ligada else "0%")
        automatico = self.config_usuario["brilho"] is None
        self.auto.atualizar(texto="Auto · ligado" if automatico else "Automático", cor=self.cor_modo)
        self.power.atualizar(texto="Desligar tela" if ligada else "Ligar tela", cor=self.cor_modo)
        self.iniciar.atualizar(cor=self.cor_modo)
        self.slider.atualizar(valor, self.cor_modo)
        dica = ("Tela apagada. Sua iluminação continua ativa." if not ligada else
                "Brilho acompanha seu horário." if automatico else "Arraste para ajustar. 0% apaga a tela.")
        self.canvas.itemconfigure(self.brilho_dica, text=dica)
        # As informações iniciais também respeitam o modo salvo na última sessão.
        if hasattr(self, "artista_texto"):
            self.atualizar_texto_midia()

    def guardar(self):
        try:
            salvar(self.config_usuario)
        except OSError:
            self.mensagem("Não foi possível salvar as preferências.", "#e8c18b")
        self.motor.enviar({"acao": "configurar", "config": self.config_usuario})

    def mudar_modo(self, modo):
        self.config_usuario["modo"] = modo
        self.atualizar_tema()
        self.atualizar_controles()
        self.atualizar_disco()
        self.atualizar_texto_midia()
        self.guardar()

    def mudar_brilho(self, valor, aplicar):
        self.config_usuario["tela_ligada"] = valor > 0
        if valor > 0:
            self.config_usuario["brilho"] = valor
        self.atualizar_controles()
        if aplicar:
            self.guardar()  # um comando ao soltar, sem inundar USB ou OpenRGB

    def automatico(self):
        self.config_usuario["brilho"] = (self.valor_brilho() or brilho_horario()) if self.config_usuario["brilho"] is None else None
        self.atualizar_controles()
        self.guardar()

    def alternar_tela(self):
        self.config_usuario["tela_ligada"] = not self.config_usuario["tela_ligada"]
        self.atualizar_controles()
        self.guardar()

    def alternar_motor(self):
        if self.iniciando:
            return
        if self.rodando:
            self.motor.parar()
            self.iniciar.atualizar(texto="Encerrando…", ativo=False)
        else:
            self.guardar()
            self.iniciando = True
            self.iniciar.atualizar(texto="Conectando…", ativo=False)
            self.mensagem("Preparando a tela…")
            self.motor.iniciar(dict(self.config_usuario))

    def mensagem(self, texto, cor=SECUNDARIO):
        self.canvas.itemconfigure(self.aviso, text=texto[:104], fill=cor)

    def encurtar(self, texto, largura, fonte):
        if fonte.measure(texto) <= largura:
            return texto
        while texto and fonte.measure(texto + "…") > largura:
            texto = texto[:-1]
        return texto + "…"

    def eventos(self):
        if self.encerrando:
            return
        self.eventos_fundo()
        try:
            while True:
                tipo, dados = self.motor.eventos.get_nowait()
                if tipo == "iniciado":
                    self.rodando, self.iniciando = True, False
                    self.iniciar.atualizar(texto="Parar exibição", ativo=True)
                elif tipo in ("parado", "erro"):
                    self.rodando, self.iniciando = False, False
                    self._visual = {}
                    self.iniciar.atualizar(texto="Iniciar exibição", ativo=True)
                    self.canvas.itemconfigure(self.status_texto, text="Exibição parada", fill=SECUNDARIO)
                    self.canvas.itemconfigure(self.status_ponto, fill="#89929f")
                    self.mensagem(dados or "Exibição encerrada. Você pode iniciar novamente.", "#e8c18b" if dados else SECUNDARIO)
                elif tipo == "estado":
                    self.atualizar_estado(dados)
        except queue.Empty:
            pass
        self.after(50, self.eventos)

    def atualizar_estado(self, dados):
        if "visual" in dados:
            self.atualizar_visual(dados["visual"])
        if "config" not in dados:
            return  # pacote pequeno de movimento, sem repetir metadados/tema/capa
        conectada = dados.get("conectada", False)
        if not conectada:
            self._visual = {}
        self.canvas.itemconfigure(self.status_texto, text="Tela conectada" if conectada else "Aguardando a tela…",
                                  fill=self.cor_modo if conectada else SECUNDARIO)
        self.canvas.itemconfigure(self.status_ponto, fill=self.cor_modo if conectada else "#89929f")
        self.midia = dados
        if not self.fundo_convertendo:
            if dados.get("erro_fundo"):
                self.fundo_status = (dados["erro_fundo"], "#e8c18b")
                self.fundo_painel.atualizar()
            elif conectada and self.fundo_pendente and dados.get("fundo_ocioso_ativo") == self.config_usuario["video_ocioso"]:
                self.fundo_pendente = False
                self.fundo_status = ("Fundo atualizado. Ele aparece nos momentos ociosos.", SECUNDARIO)
                self.fundo_painel.atualizar()
        self.atualizar_tema()
        self.atualizar_texto_midia()
        if "capa" in dados:
            try:
                self.capa = Image.open(io.BytesIO(base64.b64decode(dados["capa"]))).convert("RGBA") if dados["capa"] else None
                self.capa_preview = self.capa.resize((114, 114), Image.Resampling.LANCZOS) if self.capa else None
                self.atualizar_disco()
            except (ValueError, OSError):
                pass
        if dados.get("erro"):
            self.mensagem(dados["erro"], "#e8c18b")
        elif conectada:
            self.mensagem("Tudo pronto. Ajuste a tela do seu jeito.")
        if self.config_usuario["brilho"] is None and self.config_usuario["tela_ligada"]:
            if self.slider.valor != self.valor_brilho():
                self.atualizar_controles()

    def atualizar_texto_midia(self):
        dados = self.midia or {}
        musica = dados.get("musica")
        if self.config_usuario["modo"] == "video":
            self.canvas.itemconfigure(self.faixa_texto, text="Seu fundo em primeiro plano.")
            self.canvas.itemconfigure(self.artista_texto, text="Só vídeo  ·  Modo ambiente")
            return
        if musica:
            titulo, artista = musica
            self.canvas.itemconfigure(self.faixa_texto, text=self.encurtar(titulo, 490, self.f_faixa))
            estado_audio = "Tocando" if dados.get("tocando") else "Em pausa"
            self.canvas.itemconfigure(self.artista_texto, text=self.encurtar(f"{artista}  ·  {estado_audio}", 490, self.f_sans))
        else:
            self.canvas.itemconfigure(self.faixa_texto, text="Escolha um modo para começar.")
            self.canvas.itemconfigure(self.artista_texto, text="A tela acompanha. Você aproveita.")

    def fechar(self):
        if self.encerrando:
            return
        self.encerrando = True
        self.conversor_fundo.cancelar()
        self.motor.parar()
        self.iniciar.atualizar(texto="Encerrando…", ativo=False)
        inicio = time.monotonic()

        def aguardar():
            proc = self.motor.proc
            if (proc is None or proc.poll() is not None) and not self.conversor_fundo.ocupado:
                self.destroy()
            elif time.monotonic() - inicio >= 10:
                # Fechar o stdin aciona o mesmo encerramento se o comando se perdeu.
                try:
                    if proc is not None:
                        proc.stdin.close()
                except (OSError, ValueError):
                    pass
                self.mensagem("Aguardando a tela encerrar…")
                self.after(500, aguardar)
            else:
                self.after(150, aguardar)
        aguardar()


def main():
    try:
        reserva = reservar_execucao("Local\\TuringVinylInterface")
    except RuntimeError:
        return
    try:
        if os.name == "nt":
            try:
                # Dá à janela sua própria identidade na barra de tarefas.
                shell = ctypes.WinDLL("shell32")
                shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
                shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
                shell.SetCurrentProcessExplicitAppUserModelID("TuringScreen.SpotifyVinyl.Interface")
            except (OSError, AttributeError):
                pass
        Janela().mainloop()
    finally:
        liberar_execucao(reserva)


if __name__ == "__main__":
    main()
