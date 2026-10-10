"""Preferências locais e comunicação entre a janela e o script da tela.

Não acessa USB/OpenRGB. Os comandos de brilho continuam na fila USB existente.
"""
import base64
import ctypes
import datetime
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

MODOS = ("dinamico", "spotify", "video")
PADRAO = {"modo": "dinamico", "brilho": None, "tela_ligada": True,
          "acrilico": True, "opacidade_fundo": 64,
          "video_ocioso": None, "video_ocioso_nome": ""}
ARQUIVO = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TuringScreen" / "interface.json"
PREFIXO = "@TURING_UI@"


def validar(dados, anterior=None):
    config = dict(anterior or PADRAO)
    if not isinstance(dados, dict):
        return config
    if dados.get("modo") in MODOS:
        config["modo"] = dados["modo"]
    if "brilho" in dados:
        valor = dados["brilho"]
        if valor is None or (type(valor) is int and 1 <= valor <= 100):
            config["brilho"] = valor
    if type(dados.get("tela_ligada")) is bool:
        config["tela_ligada"] = dados["tela_ligada"]
    if type(dados.get("acrilico")) is bool:
        config["acrilico"] = dados["acrilico"]
    if type(dados.get("opacidade_fundo")) is int:
        config["opacidade_fundo"] = min(100, max(0, dados["opacidade_fundo"]))
    if "video_ocioso" in dados:
        caminho = dados["video_ocioso"]
        if caminho is None or (isinstance(caminho, str) and 0 < len(caminho) <= 2048
                               and "\x00" not in caminho):
            config["video_ocioso"] = caminho
    if isinstance(dados.get("video_ocioso_nome"), str):
        config["video_ocioso_nome"] = dados["video_ocioso_nome"][:240]
    return config


def carregar():
    try:
        return validar(json.loads(ARQUIVO.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return dict(PADRAO)


def salvar(config):
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    temporario = ARQUIVO.with_suffix(".tmp")
    temporario.write_text(json.dumps(validar(config), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, ARQUIVO)


def brilho_horario(agora=None):
    h = (agora or datetime.datetime.now()).hour
    return 30 if 1 <= h < 7 else (60 if h >= 18 or h < 1 else 100)


class Controles:
    def __init__(self, interface=False):
        self.interface = interface
        self.config = carregar() if interface else dict(PADRAO)
        self.revisao = 0

    def aplicar(self, dados):
        novo = validar(dados, self.config)
        if novo != self.config:
            self.config = novo  # troca de referência; leitura dos quadros não usa lock
            self.revisao += 1

    def brilho(self, agora=None):
        return self.config["brilho"] if self.config["brilho"] is not None else brilho_horario(agora)

    def brilho_tela(self, agora=None):
        return self.brilho(agora) if self.config["tela_ligada"] else 0


def iniciar_ponte(controles, estado, parar, log):
    """Estado a cada 700 ms; movimento em números, até 10 vezes/s quando muda."""
    def receber():
        try:
            for linha in sys.stdin:
                if len(linha) > 4096:
                    continue
                try:
                    comando = json.loads(linha)
                    if not isinstance(comando, dict):
                        continue
                    if comando.get("acao") == "parar":
                        parar.set()
                        return
                    if comando.get("acao") == "configurar":
                        controles.aplicar(comando.get("config"))
                        estado["modo_exibicao"] = controles.config["modo"]
                except (ValueError, TypeError):
                    continue
        finally:
            # Fechar a janela ou perder o pipe encerra o script sem matar o USB.
            parar.set()

    def publicar():
        ultima_capa = object()
        ultimo_visual = None
        proximo_estado = 0.0
        while not parar.is_set():
            snap = dict(estado)
            midia = snap if snap.get("musica") else (snap.get("ultima_midia") or {})
            visual = snap.get("visual_disco")
            capa = visual.get("capa") if visual and visual.get("visivel") else midia.get("capa")
            agora = time.monotonic()
            pacote = {}
            if agora >= proximo_estado or capa is not ultima_capa:
                proximo_estado = agora + 0.7
                pacote.update(
                    config=controles.config, brilho_atual=controles.brilho(),
                    conectada=snap.get("tela_conectada", False),
                    musica=midia.get("musica"), tocando=snap.get("tocando", False),
                    cor=midia.get("cor_viva"), erro=snap.get("erro_interface", ""),
                    fundo_ocioso_ativo=snap.get("fundo_ocioso_ativo"),
                    erro_fundo=snap.get("erro_fundo", ""),
                )
                if capa is not ultima_capa:
                    ultima_capa = capa
                    pacote["capa"] = None
                    if capa is not None:
                        buffer = io.BytesIO()
                        capa.resize((160, 160)).save(buffer, format="PNG")
                        pacote["capa"] = base64.b64encode(buffer.getvalue()).decode("ascii")
            if visual:
                chave = tuple(visual[k] for k in (
                    "angulo", "velocidade", "angulo_disco", "velocidade_disco", "escala", "visivel"))
                if chave != ultimo_visual or pacote:
                    ultimo_visual = chave
                    pacote["visual"] = {k: visual[k] for k in (
                        "angulo", "velocidade", "angulo_disco", "velocidade_disco",
                        "escala", "visivel", "instante")}
            if not pacote:
                parar.wait(0.1)
                continue
            try:
                sys.stdout.write(PREFIXO + json.dumps(pacote, ensure_ascii=True) + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                parar.set()
                return
            parar.wait(0.1)

    threading.Thread(target=receber, name="InterfaceComandos", daemon=True).start()
    threading.Thread(target=publicar, name="InterfaceEstado", daemon=True).start()


def reservar_execucao(nome="Local\\TuringVinylTela"):
    """Evita duas novas instâncias controlando a mesma tela/controladora."""
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, nome)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        raise RuntimeError("Turing Vinyl já está em execução. Encerre a outra instância primeiro.")
    return handle


def liberar_execucao(handle):
    if handle:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)
