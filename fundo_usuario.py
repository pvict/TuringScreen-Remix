"""Vídeo ocioso escolhido no app: conversão local e troca do decoder.

Os arquivos prontos ficam nas preferências locais. Cada importação cria uma
cópia; o original, o fundo do repositório e as versões anteriores são mantidos.
"""
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from controle_interface import ARQUIVO

PASTA_FUNDOS = ARQUIVO.parent / "fundos"
FLAGS_CONVERSAO = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                   | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
FILTRO_FUNDO = ("scale=480:480:force_original_aspect_ratio=increase,crop=480:480,"
                "eq=saturation=1.3:contrast=1.1,setsar=1")


class _Cancelado(Exception):
    pass


class ConversorFundo:
    """Um trabalho por vez, fora da thread da interface e com CPU limitada."""
    def __init__(self):
        self.eventos = queue.Queue()
        self.cancelamento = threading.Event()
        self.ocupado = False
        self._proc = None
        self._lock = threading.Lock()

    def _cancelado(self):
        if self.cancelamento.is_set():
            raise _Cancelado()

    def _abrir(self, argumentos, **opcoes):
        with self._lock:
            self._cancelado()
            self._proc = subprocess.Popen(
                argumentos, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=FLAGS_CONVERSAO, **opcoes)
            return self._proc

    def _sondar(self, caminho, ffprobe):
        proc = self._abrir([
            ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=width,height,codec_name,duration:format=duration", "-of", "json", str(caminho)])
        try:
            try:
                saida, _erro = proc.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise RuntimeError("O vídeo demorou demais para abrir. Tente outro arquivo.")
            self._cancelado()
            if proc.returncode:
                raise RuntimeError("Não foi possível abrir esse vídeo. Tente outro arquivo.")
            dados = json.loads(saida)
            videos = dados.get("streams", [])
            if not videos or not videos[0].get("width") or not videos[0].get("height"):
                raise RuntimeError("O arquivo escolhido não contém um vídeo válido.")
            video = videos[0]
            duracao = None
            for valor in (dados.get("format", {}).get("duration"), video.get("duration")):
                try:
                    numero = float(valor)
                    if math.isfinite(numero) and numero > 0:
                        duracao = numero
                        break
                except (TypeError, ValueError):
                    pass
            return video, duracao
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def iniciar(self, caminho):
        if self.ocupado:
            return False
        self.ocupado = True
        self.cancelamento.clear()
        threading.Thread(target=self._converter, args=(Path(caminho),),
                         name="ConverterFundo", daemon=True).start()
        return True

    def cancelar(self):
        self.cancelamento.set()
        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass

    def _converter(self, origem):
        temporario = None
        proc = None
        resultado = None
        try:
            self.eventos.put(("progresso", (0, "Abrindo o vídeo…")))
            ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
            if not ffmpeg or not ffprobe:
                raise RuntimeError("Instale FFmpeg com FFprobe e adicione a pasta bin ao PATH.")
            if not origem.is_file():
                raise RuntimeError("O vídeo escolhido não foi encontrado.")
            _video, duracao = self._sondar(origem, ffprobe)
            self._cancelado()
            PASTA_FUNDOS.mkdir(parents=True, exist_ok=True)
            identificador = uuid.uuid4().hex[:12]
            destino = PASTA_FUNDOS / f"{origem.stem[:70]}-{identificador}.mp4"
            temporario = PASTA_FUNDOS / f".convertendo-{identificador}.mp4"
            proc = self._abrir([
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats",
                "-progress", "pipe:1", "-filter_threads", "1", "-threads", "2",
                "-i", str(origem), "-map", "0:v:0", "-vf", FILTRO_FUNDO,
                "-r", "30", "-an", "-sn", "-dn", "-c:v", "libx264", "-threads", "2",
                "-crf", "15", "-preset", "medium", "-profile:v", "baseline", "-bf", "0",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-n", str(temporario)])
            erros = deque(maxlen=8)

            def ler_erros():
                for linha in iter(proc.stderr.readline, b""):
                    erros.append(linha.decode("utf-8", errors="replace").strip())

            leitor = threading.Thread(target=ler_erros, name="ErrosConverterFundo", daemon=True)
            leitor.start()
            ultimo_aviso = 0.0
            self.eventos.put(("progresso", (None if duracao is None else 0, "Preparando o fundo…")))
            for linha in iter(proc.stdout.readline, b""):
                self._cancelado()
                chave, _, valor = linha.decode("ascii", errors="replace").strip().partition("=")
                if chave not in ("out_time_us", "out_time_ms"):
                    continue
                agora = time.monotonic()
                if agora - ultimo_aviso < 0.25:
                    continue
                try:
                    segundos = int(valor) / 1_000_000
                except ValueError:
                    continue
                percentual = min(99, max(0, round(segundos / duracao * 100))) if duracao else None
                self.eventos.put(("progresso", (percentual, "Preparando o fundo…")))
                ultimo_aviso = agora
            codigo = proc.wait()
            leitor.join(timeout=1)
            self._cancelado()
            if codigo or not temporario.is_file() or temporario.stat().st_size == 0:
                detalhe = next((e for e in reversed(erros) if e), "")[:220]
                raise RuntimeError("Não foi possível preparar o vídeo." + (" " + detalhe if detalhe else ""))
            self.eventos.put(("progresso", (99, "Conferindo o fundo…")))
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            video, _duracao = self._sondar(temporario, ffprobe)
            if (video["width"], video["height"], video.get("codec_name")) != (480, 480, "h264"):
                raise RuntimeError("O vídeo preparado não ficou no formato esperado.")
            self._cancelado()
            os.replace(temporario, destino)
            temporario = None
            resultado = ("pronto", {"caminho": str(destino), "nome": origem.name})
        except _Cancelado:
            resultado = ("cancelado", None)
        except Exception as exc:
            resultado = ("erro", str(exc))
        finally:
            with self._lock:
                atual, self._proc = self._proc, None
            for processo in (proc, atual):
                if processo is not None and processo.poll() is None:
                    try:
                        processo.kill()
                        processo.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if processo is not None:
                    for fluxo in (processo.stdout, processo.stderr):
                        if fluxo is not None:
                            fluxo.close()
            if temporario is not None:
                try:
                    temporario.unlink(missing_ok=True)
                except OSError:
                    pass
            self.ocupado = False
            if resultado:
                self.eventos.put(resultado)


class FundoOcioso:
    """Prepara o decoder fora do vídeo ao vivo; troca num limite de quadro."""
    def __init__(self, controles, criar_decoder, padrao, fps, estado, log):
        self.controles, self.criar_decoder = controles, criar_decoder
        self.padrao, self.fps, self.estado, self.log = padrao, fps, estado, log
        self._lock = threading.Lock()
        self._encerrado = threading.Event()
        self._limpeza = queue.SimpleQueue()
        self._pronto = self._preparando = None
        estado["erro_fundo"] = ""
        pedido = controles.config.get("video_ocioso")
        if pedido and not Path(pedido).is_file():
            estado["erro_fundo"] = "O fundo salvo não foi encontrado. Usando o fundo padrão."
            pedido = None
        self._pedido = pedido
        self._atual = criar_decoder(pedido or padrao, fps, log=log, nome="fundo ocioso")
        estado["fundo_ocioso_ativo"] = pedido
        self._worker = threading.Thread(target=self._acompanhar, name="TrocarFundoOcioso", daemon=True)
        self._worker.start()

    def _drenar(self):
        while True:
            try:
                self._limpeza.get_nowait().fechar()
            except queue.Empty:
                return

    def _acompanhar(self):
        ultimo_pedido = self._pedido
        while not self._encerrado.is_set():
            self._drenar()
            pedido = self.controles.config.get("video_ocioso")
            if pedido == ultimo_pedido:
                self._encerrado.wait(0.2)
                continue
            ultimo_pedido = pedido
            if pedido == self._pedido:
                with self._lock:
                    antigo, self._pronto = self._pronto, None
                if antigo:
                    antigo[1].fechar()
                self.estado["erro_fundo"] = ""
                continue
            novo = None
            timer = None
            try:
                self.estado["erro_fundo"] = ""
                caminho = pedido or self.padrao
                if not Path(caminho).is_file():
                    raise RuntimeError("O novo vídeo de fundo não foi encontrado.")
                self.log(f"fundo ocioso: preparando {Path(caminho).name}")
                novo = self.criar_decoder(caminho, self.fps, log=self.log, nome="novo fundo ocioso")
                with self._lock:
                    self._preparando = novo
                if self._encerrado.is_set():
                    continue
                timer = threading.Timer(10, novo.fechar)
                timer.daemon = True
                timer.start()
                primeiro = novo.proximo()
                timer.cancel()
                if primeiro is None:
                    raise RuntimeError("Não foi possível abrir o novo fundo. O anterior foi mantido.")
                with self._lock:
                    self._preparando = None
                    if self._encerrado.is_set() or self.controles.config.get("video_ocioso") != pedido:
                        antigo = None
                    else:
                        antigo, self._pronto = self._pronto, (pedido, novo, primeiro)
                        novo = None
                if antigo:
                    antigo[1].fechar()
            except Exception as exc:
                if not self._encerrado.is_set() and self.controles.config.get("video_ocioso") == pedido:
                    self.estado["erro_fundo"] = str(exc)
                    self.log(f"fundo ocioso: {exc}")
            finally:
                if timer:
                    timer.cancel()
                with self._lock:
                    self._preparando = None
                if novo:
                    novo.fechar()
        self._drenar()

    def proximo(self):
        with self._lock:
            pronto, self._pronto = self._pronto, None
            if pronto and pronto[0] == self.controles.config.get("video_ocioso"):
                pedido, novo, primeiro = pronto
                self._limpeza.put(self._atual)
                self._atual, self._pedido = novo, pedido
                self.estado.update(fundo_ocioso_ativo=pedido, erro_fundo="")
                return primeiro
            if pronto:
                self._limpeza.put(pronto[1])
        return self._atual.proximo()

    def fechar(self):
        self._encerrado.set()
        with self._lock:
            pronto, self._pronto = self._pronto, None
            preparando = self._preparando
        self._atual.fechar()
        if pronto:
            pronto[1].fechar()
        if preparando:
            preparando.fechar()
        self._worker.join(timeout=2)
        self._drenar()
