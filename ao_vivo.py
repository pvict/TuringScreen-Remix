"""Transmissão ao vivo para a tela: quadros 480x480 -> H.264 (ffmpeg) -> USB.

A tela decodifica H.264 sozinha. Em vez de mandar um PNG de overlay por vez
(cada um leva ~150 ms para chegar), tudo (fundo, capa, texto, anéis) vira vídeo,
e a animação sai a 60 quadros por segundo.

Três threads: quem gera os quadros (rodar), o leitor da saída do ffmpeg e o
enviador USB. Só o enviador fala com o USB; comandos extras (brilho etc.) entram
na fila dele com executar().
"""
import queue
import subprocess
import threading
import time

import usb.core
import usb.util
from PIL import Image

LADO = 480
BYTES_QUADRO = LADO * LADO * 3
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TAM_PEDACO = 65536          # maior pedaço lido do ffmpeg por vez
LIMITE_FILA = 60            # acima disso, quem gera quadros espera (não perde dados)
MAX_LOTE = 160_000          # máximo de bytes por comando 121 (a CLI usa 202752)


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
            ["ffmpeg", "-loglevel", "warning", "-stream_loop", "-1", "-i", caminho,
             "-vf", f"scale={LADO}:{LADO}", "-r", str(fps),
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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


class Codificador:
    """ffmpeg em tempo real: recebe quadros RGB, entrega H.264 (Annex B)."""

    def __init__(self, fps=30, kbps=2500, gop=15, log=None):
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
        self.saida = queue.Queue()
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        while True:
            dados = self.proc.stdout.read1(TAM_PEDACO)
            if not dados:
                break
            self.saida.put((time.monotonic(), dados))

    def enviar_quadro(self, img):
        if img.mode != "RGB":
            img = img.convert("RGB")
        self.proc.stdin.write(img.tobytes())
        self.proc.stdin.flush()

    def fechar(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


class EscritaRapida:
    """Escrita USB para o vídeo, sem a espera fixa de ~100 ms de write_to_device.

    write_to_device chama read_flush depois de cada resposta, e essa leitura espera até
    100 ms por dados que normalmente não chegam. Aqui os endpoints ficam em cache e o
    descarte de resposta extra espera só alguns milissegundos. Se não achar os endpoints,
    usa o write_to_device original."""

    FLUSH_MS = 10

    def __init__(self, dev, fallback):
        self._fallback = fallback
        self.out = self.inp = None
        try:
            intf = usb.util.find_descriptor(dev.get_active_configuration(), bInterfaceNumber=0)

            def _sentido(sentido):
                return lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == sentido

            self.out = usb.util.find_descriptor(intf, custom_match=_sentido(usb.util.ENDPOINT_OUT))
            self.inp = usb.util.find_descriptor(intf, custom_match=_sentido(usb.util.ENDPOINT_IN))
        except Exception:
            self.out = self.inp = None

    def __call__(self, dev, dados, timeout=2000):
        if self.out is None or self.inp is None:
            return self._fallback(dev, dados, timeout)
        try:
            self.out.write(dados, timeout)
        except usb.core.USBError:
            return None
        try:
            resp = bytes(self.inp.read(512, timeout))
        except usb.core.USBError:
            return None
        try:
            self.inp.read(512, self.FLUSH_MS)   # descarta resposta extra sem esperar 100 ms
        except usb.core.USBError:
            pass
        return resp


class EnviadorUSB(threading.Thread):
    """Manda o H.264 à tela (comando 121), juntando em um comando tudo o que já saiu do
    ffmpeg. Cada comando custa ~100 ms fixos (escrita + resposta da tela), então juntar
    os pedaços é o que sustenta 30 quadros por segundo."""

    def __init__(self, dev, codificador, parar, usar_delay=False, max_lote=MAX_LOTE, log=None):
        super().__init__(daemon=True)
        from turingscreencli import operations
        from turingscreencli.transport import (
            build_command_packet_header, encrypt_command_packet, write_to_device)
        self._op = operations
        self._cabecalho = build_command_packet_header
        self._cifrar = encrypt_command_packet
        self._escrever = EscritaRapida(dev, write_to_device)
        self.dev, self.cod, self.parar = dev, codificador, parar
        self.usar_delay, self.max_lote = usar_delay, max_lote
        self.log = log
        self._ultimo_log_video = 0.0
        # Sobra de bytes que não coube no lote anterior (o H.264 é um fluxo de bytes,
        # então pode ser cortado em qualquer ponto) e o instante de chegada dela.
        self._resto = b""
        self._t_resto = 0.0
        self.comandos = queue.Queue()
        self.erro = None
        self.m = {"comandos": 0, "bytes": 0, "delays": 0, "t_delay": 0.0, "t_envio": 0.0,
                  "resp_min": 255, "resp_max": 0, "falhas": 0, "lat_soma": 0.0, "lat_max": 0.0}

    def executar(self, funcao):
        """Roda funcao(dev) na thread do USB (brilho, etc.)."""
        self.comandos.put(funcao)

    def _lote(self):
        """Junta o que já saiu do ffmpeg, mas nunca devolve mais que max_lote bytes.

        O que passar do limite fica em self._resto e vai no próximo comando. Antes, o
        último pedaço lido (até 64 KB) podia estourar max_lote e o comando passava do
        que a tela aceita (a CLI usa 202752), o que corrompe o vídeo (mosaico)."""
        if self._resto:
            t_chegada, dados = self._t_resto, self._resto
            self._resto = b""
        else:
            try:
                t_chegada, dados = self.cod.saida.get(timeout=0.05)
            except queue.Empty:
                return None, None
        partes = [dados]
        total = len(dados)
        while total < self.max_lote:
            try:
                _, mais = self.cod.saida.get_nowait()
            except queue.Empty:
                break
            partes.append(mais)
            total += len(mais)
        tudo = b"".join(partes)
        if len(tudo) > self.max_lote:
            self._resto = tudo[self.max_lote:]
            self._t_resto = t_chegada
            tudo = tudo[:self.max_lote]
        return t_chegada, tudo

    def run(self):
        falhas_seguidas = 0
        try:
            while not self.parar.is_set():
                while not self.comandos.empty():
                    self.comandos.get()(self.dev)
                t_chegada, dados = self._lote()
                if dados is None:
                    continue
                pacote = self._cabecalho(121)
                pacote[8:12] = len(dados).to_bytes(4, "big")
                t0 = time.monotonic()
                resp = self._escrever(self.dev, self._cifrar(pacote) + dados)
                t1 = time.monotonic()
                self.m["t_envio"] += t1 - t0
                self.m["comandos"] += 1
                self.m["bytes"] += len(dados)
                lat = t1 - t_chegada
                status = resp[8] if resp is not None and len(resp) >= 9 else None
                fila = self.cod.saida.qsize()
                agora_log = time.monotonic()
                anomalia = status is None or status < 3 or fila >= 15 or lat >= 0.30
                # Amostragem mais próxima para correlacionar falhas visuais com o USB.
                intervalo_log = 0.5 if anomalia else 1.0
                if self.log and agora_log - self._ultimo_log_video >= intervalo_log:
                    self.log(
                        f"vídeo USB: resposta={status} lote={len(dados)} bytes "
                        f"fila={fila} latência={lat:.3f}s suspeito={anomalia}"
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
                if self.usar_delay and (resp is None or len(resp) < 9 or resp[8] <= 3):
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
        self.cod = Codificador(fps, kbps, gop, log)
        self.env = EnviadorUSB(dev, self.cod, self.parar, usar_delay, max_lote, log)
        self.quadros = 0
        self.t_gerar = 0.0
        self.t_gerar_max = 0.0
        self.atrasos = 0   # vezes em que o gerador não chegou a tempo
        self.log = log
        self.contexto = None                 # função opcional que descreve o estado (para o log)
        self.limite_lento = 1.0 / fps        # gerar um quadro além disso estoura o orçamento
        self._jan = self._nova_janela()
        self._t_jan = self._t_resumo = time.monotonic()

    @staticmethod
    def _nova_janela():
        return {"n": 0, "soma": 0.0, "max": 0.0, "lentos": 0, "env_max": 0.0, "ctx_max": ""}

    def _medir(self, dg, de):
        """Acumula o tempo de gerar/enviar cada quadro e escreve um resumo no log."""
        j = self._jan
        j["n"] += 1
        j["soma"] += dg
        j["env_max"] = max(j["env_max"], de)
        if dg > self.limite_lento:
            j["lentos"] += 1
        if dg > j["max"]:
            j["max"] = dg
            try:
                j["ctx_max"] = self.contexto() if self.contexto else ""
            except Exception:
                j["ctx_max"] = ""
        agora = time.monotonic()
        if self.log and agora - self._t_jan >= 5.0:
            if j["lentos"] or agora - self._t_resumo >= 30.0:
                self.log(
                    f"quadros: média={1000 * j['soma'] / max(j['n'], 1):.1f} ms "
                    f"máx={1000 * j['max']:.0f} ms "
                    f"lentos(>{1000 * self.limite_lento:.0f} ms)={j['lentos']}/{j['n']} "
                    f"envio_ffmpeg_máx={1000 * j['env_max']:.0f} ms [pior: {j['ctx_max']}]"
                )
                self._t_resumo = agora
            self._jan = self._nova_janela()
            self._t_jan = agora

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
                if self.cod.saida.qsize() > LIMITE_FILA:
                    time.sleep(0.005)
                    continue
                tg = time.monotonic()
                quadro = self.gerar_quadro(tg)
                dtg = time.monotonic() - tg
                te = time.monotonic()
                self.cod.enviar_quadro(quadro)
                dte = time.monotonic() - te
                self.t_gerar += dtg
                self.t_gerar_max = max(self.t_gerar_max, dtg)
                self._medir(dtg, dte)
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
        dt = max(time.monotonic() - inicio, 1e-6)
        return {"segundos": dt, "quadros": self.quadros, "fps_real": self.quadros / dt,
                "atrasos": self.atrasos, "t_gerar": self.t_gerar / max(self.quadros, 1),
                "t_gerar_max": self.t_gerar_max, **self.env.m}
