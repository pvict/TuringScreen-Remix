"""Transmissão ao vivo para a tela: quadros 480x480 -> H.264 (ffmpeg) -> USB.

A tela decodifica H.264 sozinha. Em vez de mandar um PNG de overlay por vez
(cada um leva ~150 ms para chegar), tudo (fundo, capa, texto, anéis) vira vídeo,
e a animação sai a 60 quadros por segundo.

Três threads: quem gera os quadros (rodar), o leitor da saída do ffmpeg e o
enviador USB. Só o enviador fala com o USB; comandos extras (brilho etc.) entram
na fila dele com executar().
"""
import queue
import datetime
import json
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from PIL import Image

LADO = 480
BYTES_QUADRO = LADO * LADO * 3
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TAM_PEDACO = 65536          # maior pedaço lido do ffmpeg por vez
LIMITE_FILA_BYTES = 64 * 1024  # ~0,1 s a 5 Mbps; o gerador espera, sem perder dados
MAX_LOTE = 160_000          # limite rígido por comando 121 (a CLI usa 202752)
USB_LIMPEZA_MS = 5         # espera extra após a resposta; a biblioteca usa 100 ms
FILA_TELA_MAX = 2          # resposta[8]: fila interna, não um código de sucesso
INTERVALO_FILA_TELA = 0.01
TIMEOUT_FILA_TELA = 2.0

# Capturas opcionais: reative se precisar investigar stutter ou mosaico.
DIAGNOSTICO_VIDEO = False
CAPTURA_ANTES = 6.0
CAPTURA_DEPOIS = 15.0
CAPTURA_MEMORIA_MAX = 6 * 1024 * 1024
CAPTURA_BYTES_MAX = 24 * 1024 * 1024
CAPTURA_SLOTS = 6
PASTA_DIAGNOSTICO = Path(__file__).resolve().parent / "diagnostico_video"


def _nals_h264(dados):
    """Posições das unidades Annex B; usado só para preparar a cópia local."""
    inicios = list(re.finditer(rb"\x00\x00(?:\x00)?\x01", dados))
    for indice, inicio in enumerate(inicios):
        if inicio.end() < len(dados):
            fim = inicios[indice + 1].start() if indice + 1 < len(inicios) else len(dados)
            yield dados[inicio.end()] & 31, inicio.start(), fim


class CapturaVideo:
    """Copia H.264 antes do USB. Disco e remux ficam em uma thread separada."""

    def __init__(self, fps, log):
        self.fps, self.log = fps, log
        self._lock = threading.Lock()
        self._historico = deque()
        self._bytes_historico = 0
        self._ativo = None
        self._prefixo = bytearray()
        self._parametros = {}
        self._contador = 0
        self._tarefas = queue.Queue(maxsize=2)
        self._worker = threading.Thread(target=self._gravar, daemon=True)
        self._worker.start()

    def marcar(self, evento):
        agora = time.monotonic()
        registro = {"evento": evento, "horario": datetime.datetime.now().isoformat(timespec="milliseconds"),
                    "monotonic": agora}
        with self._lock:
            if self._ativo is None:
                self._ativo = {"partes": list(self._historico), "eventos": [],
                               "bytes": self._bytes_historico, "ate": agora + CAPTURA_DEPOIS}
            self._ativo["eventos"].append(registro)
            self._ativo["ate"] = agora + CAPTURA_DEPOIS
        if self.log:
            self.log(f"diagnóstico vídeo: evento={evento}; captura antes do USB em andamento")

    def observar(self, instante, dados):
        # Guarda SPS/PPS iniciais para que a amostra seja reproduzível mesmo
        # se o codificador não repetir esses parâmetros a cada quadro-chave.
        if len(self._parametros) < 2 and len(self._prefixo) < 131072:
            self._prefixo.extend(dados)
            prefixo = bytes(self._prefixo)
            for tipo, inicio, fim in _nals_h264(prefixo):
                if tipo in (7, 8) and fim < len(prefixo):
                    self._parametros[tipo] = prefixo[inicio:fim]
            if len(self._parametros) == 2:
                self._prefixo.clear()
        trabalho = None
        with self._lock:
            self._historico.append((instante, dados))
            self._bytes_historico += len(dados)
            while self._historico and (instante - self._historico[0][0] > CAPTURA_ANTES
                                       or self._bytes_historico > CAPTURA_MEMORIA_MAX):
                self._bytes_historico -= len(self._historico.popleft()[1])
            if self._ativo is not None:
                self._ativo["partes"].append((instante, dados))
                self._ativo["bytes"] += len(dados)
                if instante >= self._ativo["ate"] or self._ativo["bytes"] >= CAPTURA_BYTES_MAX:
                    trabalho, self._ativo = self._ativo, None
        if trabalho is not None:
            self._enfileirar(trabalho)

    def _enfileirar(self, trabalho):
        trabalho["parametros"] = dict(self._parametros)
        try:
            self._tarefas.put_nowait(trabalho)
        except queue.Full:
            if self.log:
                self.log("diagnóstico vídeo: gravação ocupada; amostra descartada, fluxo USB preservado")

    def _gravar(self):
        while True:
            trabalho = self._tarefas.get()
            if trabalho is None:
                return
            try:
                dados = b"".join(parte for _t, parte in trabalho["partes"])
                tamanho_original = len(dados)
                # read1 pode terminar no meio de uma NAL. Retira a última
                # unidade para não criar um defeito artificial no fim do arquivo.
                ultima_nal = None
                for _tipo, inicio, _fim in _nals_h264(dados):
                    ultima_nal = inicio
                if ultima_nal is not None:
                    dados = dados[:ultima_nal]
                # Começa num IDR completo e prefixa parâmetros do próprio fluxo.
                # O corte só afeta o arquivo de diagnóstico, nunca o envio USB.
                primeiro_idr = None
                parametros = trabalho["parametros"]
                for tipo, inicio, fim in _nals_h264(dados):
                    if tipo in (7, 8):
                        parametros[tipo] = dados[inicio:fim]
                    elif tipo == 5:
                        primeiro_idr = inicio
                        break
                if primeiro_idr is None or len(parametros) < 2:
                    raise ValueError("amostra sem quadro IDR/SPS/PPS suficientes")
                self._contador += 1
                slot = (self._contador - 1) % CAPTURA_SLOTS + 1
                PASTA_DIAGNOSTICO.mkdir(exist_ok=True)
                caminho = PASTA_DIAGNOSTICO / f"captura_{slot:02d}.h264"
                mp4 = caminho.with_suffix(".mp4")
                with caminho.open("wb") as arquivo:
                    arquivo.write(parametros[7])
                    arquivo.write(parametros[8])
                    arquivo.write(dados[primeiro_idr:])
                meta = {"fps": self.fps, "eventos": trabalho["eventos"],
                        "inicio_bytes_monotonic": trabalho["partes"][0][0],
                        "fim_bytes_monotonic": trabalho["partes"][-1][0],
                        "bytes_antes_do_corte": tamanho_original, "bytes_descartados_ate_idr": primeiro_idr,
                        "bytes_descartados_no_fim": tamanho_original - len(dados),
                        "origem": "saída do FFmpeg antes do USB; horários são de coleta, não PTS",
                        "mp4_pronto": False}
                caminho.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                resultado = subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-fflags", "+genpts", "-r", str(self.fps),
                     "-f", "h264", "-i", str(caminho), "-an", "-c:v", "copy",
                     "-movflags", "+faststart", "-y", str(mp4)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, creationflags=SEM_JANELA, timeout=30)
                meta["mp4_pronto"] = resultado.returncode == 0
                if resultado.returncode:
                    meta["erro_remux"] = resultado.stderr.decode("utf-8", errors="replace")[-2000:]
                caminho.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                if self.log:
                    self.log(f"diagnóstico vídeo: captura salva em {mp4 if meta['mp4_pronto'] else caminho}; eventos={trabalho['eventos']}")
            except Exception as exc:
                if self.log:
                    self.log(f"diagnóstico vídeo: erro salvando amostra: {exc}")

    def fechar(self):
        with self._lock:
            trabalho, self._ativo = self._ativo, None
        if trabalho is not None and trabalho["partes"]:
            self._enfileirar(trabalho)
        try:
            self._tarefas.put(None, timeout=1)
        except queue.Full:
            return
        self._worker.join(timeout=5)


def _monitorar_stderr(proc, nome, log):
    """Drena o stderr do FFmpeg em thread própria e registra avisos/erros."""
    def ler():
        if proc.stderr is None:
            return
        for bruto in iter(proc.stderr.readline, b""):
            mensagem = bruto.decode("utf-8", errors="replace").strip()
            if mensagem and log:
                log(f"ffmpeg {nome}: {mensagem}")

    threading.Thread(target=ler, daemon=True).start()


class FundoDecoder:
    """Lê quadros de um vídeo em loop (ffmpeg), já em 480x480 RGB."""

    def __init__(self, caminho, fps=30, log=None, nome="fundo"):
        self.proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "warning", "-stream_loop", "-1", "-i", caminho,
             "-vf", f"scale={LADO}:{LADO}", "-r", str(fps),
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            # O stdin do script recebe os comandos da interface. Herdá-lo faz
            # o FFmpeg esperar entrada nesse pipe antes de produzir o vídeo.
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=SEM_JANELA,
        )
        _monitorar_stderr(self.proc, nome, log)

    def proximo(self):
        dados = self.proc.stdout.read(BYTES_QUADRO)
        if len(dados) < BYTES_QUADRO:
            return None
        return Image.frombytes("RGB", (LADO, LADO), dados)

    def fechar(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:
            pass


class FilaVideo(queue.Queue):
    """Conta os bytes sob o mesmo lock já usado pela fila de saída."""

    def _init(self, maxsize):
        super()._init(maxsize)
        self._bytes = 0

    def _put(self, item):
        super()._put(item)
        self._bytes += len(item[1])

    def _get(self):
        item = super()._get()
        self._bytes -= len(item[1])
        return item

    def bytes_pendentes(self):
        with self.mutex:
            return self._bytes


class Codificador:
    """ffmpeg em tempo real: recebe quadros RGB, entrega H.264 (Annex B)."""

    def __init__(self, fps=30, kbps=2500, gop=15, log=None, captura=None):
        self.captura = captura
        self.log = log
        self.proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "warning",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{LADO}x{LADO}",
             "-r", str(fps), "-i", "-",
             "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ull", "-zerolatency", "1",
             "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-bf", "0",
             "-g", str(gop), "-no-scenecut", "1",
             "-rc", "cbr", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps // 2}k",
             "-flush_packets", "1", "-f", "h264", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=SEM_JANELA,
        )
        _monitorar_stderr(self.proc, "codificador NVENC", log)
        self.saida = FilaVideo()
        self._leitor = threading.Thread(target=self._ler, daemon=True)
        self._leitor.start()

    def _ler(self):
        while True:
            dados = self.proc.stdout.read1(TAM_PEDACO)
            if not dados:
                break
            chegada = time.monotonic()
            if self.captura is not None:
                try:
                    self.captura.observar(chegada, dados)
                except Exception as exc:
                    # Uma falha no diagnóstico não deve interromper o vídeo.
                    self.captura = None
                    if self.log:
                        self.log(f"diagnóstico vídeo desativado no leitor: {exc}")
            self.saida.put((chegada, dados))

    def enviar_quadro(self, img):
        self.proc.stdin.write(img.convert("RGB").tobytes())
        self.proc.stdin.flush()

    def fechar(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()
        self._leitor.join(timeout=1)


class EnviadorUSB(threading.Thread):
    """Manda H.264 à tela (comando 121), juntando os pedaços já disponíveis.

    A limpeza de respostas extras espera só 5 ms para reduzir o acúmulo de
    quadros entre envios. A resposta principal mantém o timeout de 2 segundos.
    Acima de dois itens na fila da tela, consulta o comando 122 antes de enviar mais.
    """

    def __init__(self, dev, codificador, parar, usar_delay=False, max_lote=MAX_LOTE, log=None):
        super().__init__(daemon=True)
        import usb.core
        import usb.util
        from turingscreencli import operations
        from turingscreencli.transport import (
            build_command_packet_header, encrypt_command_packet)
        self._usb_core, self._usb_util = usb.core, usb.util
        self._op = operations
        self._cabecalho = build_command_packet_header
        self._cifrar = encrypt_command_packet
        self.dev, self.cod, self.parar = dev, codificador, parar
        self.usar_delay, self.max_lote = usar_delay, max_lote
        self.log = log
        self._ultimo_log_video = 0.0
        self._ultima_falha_captura = 0.0
        self._ultimo_log_fila_tela = 0.0
        self._pendente = None
        self.comandos = queue.Queue()
        self.erro = None
        self.m = {"comandos": 0, "bytes": 0, "delays": 0, "t_delay": 0.0, "t_envio": 0.0,
                  "resp_min": 255, "resp_max": 0, "falhas": 0, "lat_soma": 0.0, "lat_max": 0.0,
                  "fila_tela_max": 0, "consultas_fila_tela": 0, "t_fila_tela": 0.0}

    def executar(self, funcao):
        """Roda funcao(dev) na thread do USB (brilho, etc.)."""
        self.comandos.put(funcao)

    def _escrever_video(self, dados, timeout=2000):
        """Transporte local do vídeo; altera só a espera da limpeza extra."""
        util = self._usb_util
        cfg = self.dev.get_active_configuration()
        intf = util.find_descriptor(cfg, bInterfaceNumber=0)
        if intf is None:
            raise RuntimeError("USB interface 0 not found")
        ep_out = util.find_descriptor(
            intf, custom_match=lambda ep:
            util.endpoint_direction(ep.bEndpointAddress) == util.ENDPOINT_OUT)
        ep_in = util.find_descriptor(
            intf, custom_match=lambda ep:
            util.endpoint_direction(ep.bEndpointAddress) == util.ENDPOINT_IN)
        if ep_out is None or ep_in is None:
            raise RuntimeError("Unable to locate USB endpoints")
        try:
            ep_out.write(dados, timeout)
        except self._usb_core.USBError as exc:
            if self.log:
                self.log(f"vídeo USB: erro de escrita: {exc}")
            return None
        try:
            response = ep_in.read(512, timeout)
        except self._usb_core.USBError as exc:
            if self.log:
                self.log(f"vídeo USB: erro lendo resposta: {exc}")
            return None

        # Mesmo limite de cinco leituras da biblioteca. Só o timeout dessa
        # limpeza cai de 100 para 5 ms; os bytes de vídeo seguem intactos.
        for _ in range(5):
            try:
                ep_in.read(512, timeout=USB_LIMPEZA_MS)
            except self._usb_core.USBError:
                break
        return bytes(response)

    def _aguardar_fila_tela(self, profundidade):
        """Aplica o controle de fluxo do protocolo, sem descartar H.264."""
        self.m["fila_tela_max"] = max(self.m["fila_tela_max"], profundidade)
        if profundidade <= FILA_TELA_MAX:
            return
        inicio = time.monotonic()
        inicial = profundidade
        registrar = self.log and inicio - self._ultimo_log_fila_tela >= 1.0
        if registrar:
            self._ultimo_log_fila_tela = inicio
            self.log(f"vídeo USB: aguardando fila_tela={inicial} baixar para {FILA_TELA_MAX}")
        try:
            while profundidade > FILA_TELA_MAX and not self.parar.is_set():
                if time.monotonic() - inicio >= TIMEOUT_FILA_TELA:
                    raise RuntimeError(f"fila interna da tela não baixou em {TIMEOUT_FILA_TELA:.1f}s; fila_tela={profundidade}")
                if self.parar.wait(INTERVALO_FILA_TELA):
                    return
                consulta = self._cifrar(self._cabecalho(122))
                resposta = self._escrever_video(consulta)
                self.m["consultas_fila_tela"] += 1
                if resposta is None or len(resposta) < 9:
                    raise RuntimeError("tela sem resposta ao consultar a fila interna (comando 122)")
                if resposta[1] != 0xC8:
                    raise RuntimeError(f"resposta inválida ao consultar fila_tela: {resposta[:16].hex()}")
                profundidade = resposta[8]
                self.m["fila_tela_max"] = max(self.m["fila_tela_max"], profundidade)
        finally:
            espera = time.monotonic() - inicio
            self.m["t_fila_tela"] += espera
            if registrar:
                self.log(f"vídeo USB: consulta encerrada fila_tela={profundidade} espera={espera:.3f}s fila_pc={self.cod.saida.qsize()}")

    def _lote(self):
        if self._pendente is not None:
            t_chegada, dados = self._pendente
            self._pendente = None
        else:
            try:
                t_chegada, dados = self.cod.saida.get(timeout=0.05)
            except queue.Empty:
                return None, None
        if len(dados) > self.max_lote:
            self._pendente = (t_chegada, dados[self.max_lote:])
            return t_chegada, dados[:self.max_lote]
        partes = [dados]
        total = len(dados)
        while total < self.max_lote:
            try:
                t_mais, mais = self.cod.saida.get_nowait()
            except queue.Empty:
                break
            espaco = self.max_lote - total
            if len(mais) > espaco:
                partes.append(mais[:espaco])
                self._pendente = (t_mais, mais[espaco:])
                break
            partes.append(mais)
            total += len(mais)
        return t_chegada, b"".join(partes)

    def run(self):
        falhas_seguidas = 0
        try:
            if self.log:
                self.log(f"vídeo USB: limpeza extra={USB_LIMPEZA_MS}ms; timeout da resposta=2000ms; controle fila_tela máximo={FILA_TELA_MAX}; limite fila_pc={LIMITE_FILA_BYTES} bytes")
            while not self.parar.is_set():
                while not self.comandos.empty():
                    self.comandos.get()(self.dev)
                t_chegada, dados = self._lote()
                if dados is None:
                    continue
                pacote = self._cabecalho(121)
                pacote[8:12] = len(dados).to_bytes(4, "big")
                t0 = time.monotonic()
                resp = self._escrever_video(self._cifrar(pacote) + dados)
                t1 = time.monotonic()
                self.m["t_envio"] += t1 - t0
                self.m["comandos"] += 1
                self.m["bytes"] += len(dados)
                lat = t1 - t_chegada
                status = resp[8] if resp is not None and len(resp) >= 9 else None
                fila = self.cod.saida.qsize() + (self._pendente is not None)
                agora_log = time.monotonic()
                anomalia = status is None or status > FILA_TELA_MAX or fila >= 15 or lat >= 0.30
                # Amostragem mais próxima para correlacionar falhas visuais com o USB.
                intervalo_log = 0.5 if anomalia else 1.0
                falha_resposta = resp is None or len(resp) < 9
                if (falha_resposta or lat >= 1.0 or (status is not None and status >= 15)) and agora_log - self._ultima_falha_captura >= 30.0:
                    self._ultima_falha_captura = agora_log
                    if self.cod.captura is not None:
                        self.cod.captura.marcar(f"USB: fila_tela={status}, fila_pc={fila}, latência={lat:.3f}s")
                if self.log and (falha_resposta or agora_log - self._ultimo_log_video >= intervalo_log):
                    self.log(
                        f"vídeo USB: resposta={status} lote={len(dados)} bytes "
                        f"fila_pc={fila} fila_pc_bytes={self.cod.saida.bytes_pendentes()} "
                        f"fila_tela={status} latência={lat:.3f}s suspeito={anomalia} "
                        f"espera_fila={t0 - t_chegada:.3f}s envio={t1 - t0:.3f}s"
                    )
                    self._ultimo_log_video = agora_log
                self.m["lat_soma"] += lat
                self.m["lat_max"] = max(self.m["lat_max"], lat)
                if resp is None:
                    falhas_seguidas += 1
                    self.m["falhas"] += 1
                    if falhas_seguidas >= 10:
                        raise RuntimeError("tela sem resposta")
                else:
                    falhas_seguidas = 0
                    if len(resp) >= 9:
                        self.m["resp_min"] = min(self.m["resp_min"], resp[8])
                        self.m["resp_max"] = max(self.m["resp_max"], resp[8])
                        if resp[1] != 0xC8:
                            raise RuntimeError(f"resposta inválida ao enviar vídeo: {resp[:16].hex()}")
                        self._aguardar_fila_tela(resp[8])
                if self.usar_delay and (resp is None or len(resp) < 9):
                    t2 = time.monotonic()
                    self._op.delay(self.dev, 2)
                    self.m["delays"] += 1
                    self.m["t_delay"] += time.monotonic() - t2
        except Exception as exc:
            self.erro = exc


class PipelineAoVivo:
    def __init__(self, dev, gerar_quadro, fps=30, kbps=1500, gop=15, parar=None,
                 usar_delay=False, max_lote=MAX_LOTE, log=None):
        self.fps = fps
        self.parar = parar or threading.Event()
        self.gerar_quadro = gerar_quadro
        self.log = log
        self.contexto = None
        self.captura = CapturaVideo(fps, log) if DIAGNOSTICO_VIDEO else None
        self.cod = Codificador(fps, kbps, gop, log, captura=self.captura)
        self.env = EnviadorUSB(dev, self.cod, self.parar, usar_delay, max_lote, log)
        self.quadros = 0
        self.t_gerar = 0.0
        self.t_gerar_max = 0.0
        self.atrasos = 0   # vezes em que o gerador não chegou a tempo
        self._t_janela = time.monotonic()
        self._janela = self._nova_janela()
        self._ultimo_evento_lento = 0.0

    @staticmethod
    def _nova_janela():
        return {"quadros": 0, "lentos": 0, "gerar_max": 0.0, "ffmpeg_max": 0.0,
                "intervalo_max": 0.0, "fila_max": 0, "fundo_ms": 0.0,
                "animacao_ms": 0.0, "desenhar_ms": 0.0, "compor_ms": 0.0,
                "pior_contexto": {}, "transicao": False, "ultimo_quadro": None}

    def marcar_evento(self, evento):
        if self.captura is not None:
            self.captura.marcar(evento)

    def _medir(self, inicio, gerar, enviar):
        if not DIAGNOSTICO_VIDEO:
            return
        j = self._janela
        try:
            contexto = dict(self.contexto()) if self.contexto else {}
        except Exception:
            contexto = {}
        j["quadros"] += 1
        j["lentos"] += gerar + enviar > 1.0 / self.fps
        if gerar > j["gerar_max"]:
            j["gerar_max"] = gerar
            j["pior_contexto"] = contexto
        j["ffmpeg_max"] = max(j["ffmpeg_max"], enviar)
        intervalo_quadro = 0.0 if j["ultimo_quadro"] is None else inicio - j["ultimo_quadro"]
        j["intervalo_max"] = max(j["intervalo_max"], intervalo_quadro)
        j["ultimo_quadro"] = inicio
        j["fila_max"] = max(j["fila_max"], self.cod.saida.qsize())
        for etapa in ("fundo_ms", "animacao_ms", "desenhar_ms", "compor_ms"):
            j[etapa] = max(j[etapa], contexto.get(etapa, 0.0))
        j["transicao"] |= contexto.get("transicao_capa", False) or contexto.get("texto_animando", False)
        agora = time.monotonic()
        if max(gerar + enviar, intervalo_quadro) >= 0.1 and agora - self._ultimo_evento_lento >= 30.0:
            self._ultimo_evento_lento = agora
            self.marcar_evento(f"quadro atrasado: gerar={1000 * gerar:.1f}ms ffmpeg={1000 * enviar:.1f}ms intervalo={1000 * intervalo_quadro:.1f}ms")
        intervalo = 1.0 if j["transicao"] else 5.0
        if self.log and agora - self._t_janela >= intervalo:
            self.log(
                f"diagnóstico quadros: fps={j['quadros'] / (agora - self._t_janela):.1f} "
                f"lentos={j['lentos']}/{j['quadros']} gerar_máx={1000 * j['gerar_max']:.1f}ms "
                f"fundo_máx={j['fundo_ms']:.1f}ms animação_máx={j['animacao_ms']:.1f}ms "
                f"desenhar_máx={j['desenhar_ms']:.1f}ms compor_máx={j['compor_ms']:.1f}ms "
                f"ffmpeg_máx={1000 * j['ffmpeg_max']:.1f}ms intervalo_quadros_máx={1000 * j['intervalo_max']:.1f}ms "
                f"fila_máx={j['fila_max']} contexto={j['pior_contexto']}"
            )
            self._t_janela = agora
            self._janela = self._nova_janela()
            self._janela["ultimo_quadro"] = inicio

    def rodar(self, duracao=None):
        """Gera quadros no ritmo do fps até 'parar' ou 'duracao' s. Bloqueia."""
        self.env.start()
        inicio = prox = time.monotonic()
        try:
            while not self.parar.is_set():
                if duracao and time.monotonic() - inicio >= duracao:
                    break
                if self.env.erro:
                    raise self.env.erro
                # Limita a antecipação do vídeo no PC por bytes, pois read1()
                # entrega pedaços de tamanhos diferentes. A fila da tela e os
                # bytes H.264 já produzidos continuam sob o controle do USB.
                if self.cod.saida.bytes_pendentes() >= LIMITE_FILA_BYTES:
                    self.parar.wait(0.005)
                    continue
                tg = time.monotonic()
                # O H.264 apresenta cada quadro com duração de 1/fps. As animações
                # seguem esse mesmo relógio, inclusive após uma espera pela fila.
                # O tempo real (tg) continua sendo usado para medir os atrasos.
                tempo_video = inicio + self.quadros / self.fps
                quadro = self.gerar_quadro(tempo_video)
                fim_gerar = time.monotonic()
                self.cod.enviar_quadro(quadro)
                dtg = time.monotonic() - tg
                self._medir(tg, fim_gerar - tg, dtg - (fim_gerar - tg))
                self.t_gerar += dtg
                self.t_gerar_max = max(self.t_gerar_max, dtg)
                self.quadros += 1
                prox += 1.0 / self.fps
                espera = prox - time.monotonic()
                if espera > 0:
                    time.sleep(espera)
                elif espera < -0.25:
                    prox = time.monotonic()
                    self.atrasos += 1
        finally:
            self.parar.set()
            self.cod.fechar()
            self.env.join(timeout=3)
            if self.captura is not None:
                self.captura.fechar()
        dt = max(time.monotonic() - inicio, 1e-6)
        return {"segundos": dt, "quadros": self.quadros, "fps_real": self.quadros / dt,
                "atrasos": self.atrasos, "t_gerar": self.t_gerar / max(self.quadros, 1),
                "t_gerar_max": self.t_gerar_max, **self.env.m}
