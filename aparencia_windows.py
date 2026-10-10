"""Acrílico do compositor do Windows, sem alterar a opacidade do texto.

O Canvas deixa pretas as áreas de vidro para o DWM compor o fundo. A política
Accent permite ajustar a camada de cor; quando indisponível, usa o material
Desktop Acrylic oficial do Windows 11 com a opacidade definida pelo sistema.
"""
import ctypes
import os
import sys
from ctypes import wintypes


class _Margens(ctypes.Structure):
    _fields_ = [(nome, ctypes.c_int) for nome in ("esquerda", "direita", "topo", "baixo")]


class _Accent(ctypes.Structure):
    _fields_ = [(nome, wintypes.DWORD) for nome in ("estado", "flags", "cor", "animacao")]


class _Composicao(ctypes.Structure):
    _fields_ = [("atributo", ctypes.c_int), ("dados", ctypes.c_void_p),
                ("tamanho", ctypes.c_size_t)]


class AcrilicoWindows:
    def __init__(self, janela):
        self.janela = janela
        self.ativo, self.ajustavel = False, False
        self.mensagem = "Acrílico disponível no Windows 11."
        self._ultima = None
        self.user = self.dwm = None
        if os.name != "nt" or sys.getwindowsversion().build < 22000:
            return
        try:
            self.user = ctypes.WinDLL("user32", use_last_error=True)
            self.dwm = ctypes.WinDLL("dwmapi")
            self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            self.user.GetAncestor.restype = wintypes.HWND
            self.dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                     ctypes.c_void_p, wintypes.DWORD]
            self.dwm.DwmSetWindowAttribute.restype = ctypes.c_long
            self.dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND,
                                                            ctypes.POINTER(_Margens)]
            self.dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
            self.accent = getattr(self.user, "SetWindowCompositionAttribute", None)
            if self.accent:
                self.accent.argtypes = [wintypes.HWND, ctypes.POINTER(_Composicao)]
                self.accent.restype = wintypes.BOOL
        except (OSError, AttributeError):
            self.user = self.dwm = None

    def _atributo(self, hwnd, atributo, valor):
        dado = ctypes.c_int(valor)
        return self.dwm.DwmSetWindowAttribute(hwnd, atributo, ctypes.byref(dado),
                                              ctypes.sizeof(dado)) >= 0

    def _politica(self, hwnd, estado, cor=0):
        if not self.accent:
            return False
        politica = _Accent(estado, 2 if estado else 0, cor, 0)
        dados = _Composicao(19, ctypes.cast(ctypes.pointer(politica), ctypes.c_void_p),
                            ctypes.sizeof(politica))
        return bool(self.accent(hwnd, ctypes.byref(dados)))

    def aplicar(self, habilitado, opacidade, cor):
        if not self.user or not self.dwm:
            self.mensagem = "Este Windows não disponibilizou o acrílico."
            return False
        try:
            hwnd = self.user.GetAncestor(self.janela.winfo_id(), 2)
            chave = (hwnd, bool(habilitado), int(opacidade), cor)
            if chave == self._ultima:
                return self.ativo
            nova_superficie = not self._ultima or self._ultima[:2] != chave[:2]
            if nova_superficie:
                self._atributo(hwnd, 20, 1)  # barra de título escura
                self._atributo(hwnd, 33, 2)  # cantos arredondados
            if not habilitado:
                self._politica(hwnd, 0)
                self._atributo(hwnd, 38, 1)  # DWMSBT_NONE
                self.dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margens(0, 0, 0, 0)))
                self.ativo, self.ajustavel = False, False
                self.mensagem = "Fundo sólido."
            else:
                if not nova_superficie and self.ativo and not self.ajustavel:
                    self._ultima = chave
                    return self.ativo
                if nova_superficie or not self.ativo:
                    self._atributo(hwnd, 38, 1)
                    vidro = self.dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margens(-1, -1, -1, -1))) >= 0
                else:
                    vidro = True
                r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
                alfa = round(max(0, min(100, opacidade)) * 255 / 100)
                abgr = (alfa << 24) | (b << 16) | (g << 8) | r
                # Blur sem camada de cor em 0%; acrílico com tint nos demais níveis.
                self.ativo = vidro and self._politica(hwnd, 4 if alfa else 3, abgr)
                self.ajustavel = self.ativo
                if not self.ativo:
                    self._politica(hwnd, 0)
                    self.ativo = vidro and self._atributo(hwnd, 38, 3)  # DWMSBT_TRANSIENTWINDOW
                self.mensagem = ("O texto permanece nítido e opaco." if self.ajustavel else
                                 "Acrílico com opacidade do Windows." if self.ativo else
                                 "O Windows não disponibilizou o acrílico.")
                if not self.ativo:
                    self.dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margens(0, 0, 0, 0)))
            self._ultima = chave
            return self.ativo
        except (OSError, AttributeError, ValueError):
            self.ativo, self.ajustavel = False, False
            self.mensagem = "O Windows não disponibilizou o acrílico."
            return False
