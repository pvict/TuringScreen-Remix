"""Obtém a capa da playlist atual pela Spotify Web API usando OAuth PKCE."""
import base64
import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


REDIRECT_URI = "http://127.0.0.1:8765/callback"
ESCOPO = "user-read-currently-playing user-read-playback-state playlist-read-private playlist-read-collaborative"
ARQUIVO_CLIENT_ID = Path(__file__).with_name("spotify_client_id.txt")
ARQUIVO_TOKEN = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TuringScreen" / "spotify_token.bin"
INTERVALO_POLL = 5.0


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _proteger_dados(dados, proteger):
    entrada_buffer = ctypes.create_string_buffer(dados)
    entrada = _Blob(len(dados), ctypes.cast(entrada_buffer, ctypes.POINTER(ctypes.c_byte)))
    saida = _Blob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if proteger:
        funcao = crypt32.CryptProtectData
        funcao.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(_Blob)]
    else:
        funcao = crypt32.CryptUnprotectData
        funcao.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(_Blob)]
    funcao.restype = wintypes.BOOL
    ok = funcao(ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(saida))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(saida.pbData, ctypes.c_void_p))


def _ler_json_http(url, dados=None, cabecalhos=None, timeout=10):
    req = urllib.request.Request(url, data=dados, headers=cabecalhos or {})
    with urllib.request.urlopen(req, timeout=timeout) as resposta:
        if resposta.status == 204:
            return None
        bruto = resposta.read()
        return json.loads(bruto.decode("utf-8")) if bruto else None


class SpotifyPlaylistWatcher:
    """Consulta o contexto da reprodução e baixa a imagem só quando a playlist muda."""

    def __init__(self, ao_atualizar, log, ler_estado=None, ao_proxima=None):
        self.ao_atualizar = ao_atualizar
        self.log = log
        self.ler_estado = ler_estado
        self.ao_proxima = ao_proxima
        self._fila_consultada_para = None
        self._fila_validacao_log_id = None
        self._cache_capas_fila = {}
        self._autorizacao_ampliada_tentada = False
        self.parar = threading.Event()
        self.thread = None
        self.client_id = ""
        self.token = None
        self._ultimo_erro = None
        self._ultima_uri = None
        self._ultima_notificacao = None
        try:
            self.client_id = ARQUIVO_CLIENT_ID.read_text(encoding="ascii").strip()
        except OSError:
            pass

    def iniciar(self):
        if not self.client_id:
            self.log("Spotify playlist: Client ID não configurado; mantendo capa da faixa.")
            return
        self.thread = threading.Thread(target=self._executar, name="SpotifyPlaylist", daemon=True)
        if self.ao_proxima is not None:
            self.log("Spotify a seguir: ativo; busca a fila perto dos últimos 32s da faixa.")
        self.thread.start()

    def fechar(self):
        self.parar.set()

    def _notificar(self, uri, nome, imagem):
        chave = (uri, nome, bool(imagem))
        if chave != self._ultima_notificacao:
            self.ao_atualizar(uri, nome, imagem)
            self._ultima_notificacao = chave

    def _executar(self):
        while not self.parar.is_set():
            espera = INTERVALO_POLL
            etapa = "consulta da reprodução atual"
            try:
                token = self._obter_token()
                atual = self._api("https://api.spotify.com/v1/me/player/currently-playing", token)
                contexto = (atual or {}).get("context") or {}
                uri = contexto.get("uri") if contexto.get("type") == "playlist" else None
                if not (atual or {}).get("is_playing"):
                    uri = None

                if not uri:
                    self._ultima_uri = None
                    self._notificar(None, None, None)
                elif uri != self._ultima_uri:
                    playlist_id = self._id_playlist(uri)
                    # Não deixe a capa/nome anteriores aparecerem enquanto a nova é buscada.
                    self._notificar(None, None, None)
                    etapa = f"consulta da playlist {playlist_id}"
                    try:
                        dados = self._api(
                            "https://api.spotify.com/v1/playlists/"
                            + urllib.parse.quote(playlist_id, safe="")
                            + "?fields=name,images",
                            token,
                        )
                        nome = (dados or {}).get("name")
                        imagens = (dados or {}).get("images") or []
                        url_imagem = imagens[0].get("url") if imagens else None
                    except urllib.error.HTTPError as exc:
                        if exc.code != 404:
                            raise
                        # Mixes personalizadas (IDs 37i9...) podem não estar no
                        # endpoint de playlists, mas o oEmbed oficial fornece nome e miniatura.
                        etapa = f"fallback oEmbed da playlist {playlist_id}"
                        url_playlist = f"https://open.spotify.com/playlist/{playlist_id}"
                        url_oembed = "https://open.spotify.com/oembed?" + urllib.parse.urlencode(
                            {"url": url_playlist}
                        )
                        dados = _ler_json_http(url_oembed)
                        nome = (dados or {}).get("title")
                        url_imagem = (dados or {}).get("thumbnail_url")

                    imagem = self._baixar(url_imagem) if url_imagem else None
                    if not nome or imagem is None:
                        raise RuntimeError("Spotify não retornou nome e imagem para a playlist")
                    self._notificar(uri, nome, imagem)
                    self._ultima_uri = uri
                    self.log(f"Spotify playlist: contexto atualizado ({nome}).")
                # A fila usa o polling existente, depois de atualizar a playlist.
                self._consultar_proxima(atual, token)
                self._ultimo_erro = None
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    mensagem = f"HTTP {exc.code} na {etapa} ({exc.url})"
                else:
                    mensagem = f"{type(exc).__name__} na {etapa}: {exc}"
                if mensagem != self._ultimo_erro:
                    self.log(f"Spotify playlist: {mensagem}")
                    self._ultimo_erro = mensagem
                espera = 60.0 if self.token is None else 15.0
            self.parar.wait(espera)

    def _consultar_proxima(self, atual, token):
        """Uma consulta por reprodução, perto do final; falhas não afetam a playlist."""
        if self.ler_estado is None or self.ao_proxima is None:
            return
        snap = self.ler_estado()
        musica = snap.get("musica")
        dur = snap.get("dur", 0.0)
        if not musica or not snap.get("tocando") or dur <= 0:
            return
        pos = snap.get("pos", 0.0) + max(0.0, time.monotonic() - snap.get("t_poll", 0.0))
        restante = dur - pos
        reproducao = snap.get("reproducao_id", 0)
        if reproducao == self._fila_consultada_para:
            return

        item = (atual or {}).get("item") or {}
        if item.get("duration_ms", 0) > 0 and (atual or {}).get("progress_ms") is not None:
            restante = (item["duration_ms"] - atual["progress_ms"]) / 1000.0
        if not 15.0 < restante <= 32.0:
            return
        artistas = [a.get("name", "").casefold() for a in item.get("artists", [])]
        artista_local = (musica[1] or "").casefold()
        # A Web API pode estar alguns segundos atrás de uma troca manual.
        if (not (atual or {}).get("is_playing")
                or item.get("type") != "track"
                or (item.get("name") or "").casefold() != (musica[0] or "").casefold()
                or (artista_local and artista_local not in artistas
                    and artista_local != ", ".join(artistas))):
            if self._fila_validacao_log_id != reproducao:
                self._fila_validacao_log_id = reproducao
                self.log("Spotify a seguir: aguardando a faixa da API coincidir com a mídia do Windows.")
            return
        self._fila_consultada_para = reproducao
        try:
            if (atual or {}).get("repeat_state") == "track":
                self.log("Spotify a seguir: repetição da mesma faixa ativa; sem prévia de outra música.")
                return
            self.log(f"Spotify a seguir: consultando fila ({musica[0]}; faltam {restante:.1f}s).")
            fila = self._api("https://api.spotify.com/v1/me/player/queue", token, timeout=4)
            tocando_fila = (fila or {}).get("currently_playing") or {}
            proximas = (fila or {}).get("queue") or []
            if tocando_fila.get("uri") != item.get("uri") or not proximas:
                motivo = "faixa mudou durante a consulta" if proximas else "fila vazia"
                self.log(f"Spotify a seguir: {motivo}; sem prévia nesta reprodução.")
                return
            proxima = proximas[0]
            if proxima.get("type") != "track" or not proxima.get("name"):
                self.log("Spotify a seguir: próximo item não é uma faixa com título.")
                return
            imagens = (proxima.get("album") or {}).get("images") or []
            imagens = [img for img in imagens if img.get("url")]
            if not imagens:
                self.log("Spotify a seguir: próxima faixa sem imagem de álbum.")
                return
            # Prefere 300 px: suficiente para a capa circular de 240 px.
            imagem = min(imagens, key=lambda img: abs((img.get("width") or 300) - 300))
            url = imagem["url"]
            dados = self._cache_capas_fila.get(url)
            if dados is None:
                dados = self._baixar(url, timeout=4)
                if len(self._cache_capas_fila) >= 4:
                    self._cache_capas_fila.pop(next(iter(self._cache_capas_fila)))
                self._cache_capas_fila[url] = dados
            if not self.parar.is_set() and self.ler_estado().get("reproducao_id") == reproducao:
                self.ao_proxima(reproducao, proxima.get("uri"), proxima["name"], dados,
                                item.get("duration_ms", 0) / 1000.0)
        except Exception as exc:
            mensagem = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
            self.log(f"Spotify a seguir: consulta indisponível ({mensagem}); mantendo a faixa atual.")

    @staticmethod
    def _id_playlist(uri):
        partes = uri.split(":")
        if len(partes) >= 3 and partes[-2] == "playlist":
            return partes[-1]
        caminho = urllib.parse.urlparse(uri).path.strip("/").split("/")
        if len(caminho) >= 2 and caminho[-2] == "playlist":
            return caminho[-1]
        raise ValueError("URI de playlist do Spotify inválido")

    @staticmethod
    def _baixar(url, timeout=10):
        req = urllib.request.Request(url, headers={"User-Agent": "TuringScreen/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resposta:
            return resposta.read(8 * 1024 * 1024 + 1)[:8 * 1024 * 1024]

    def _api(self, url, token, timeout=10):
        try:
            return _ler_json_http(url, cabecalhos={"Authorization": f"Bearer {token}"}, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and self.token and self.token.get("refresh_token"):
                self.token["expires_at"] = 0
                token = self._renovar_token()
                return _ler_json_http(url, cabecalhos={"Authorization": f"Bearer {token}"}, timeout=timeout)
            raise

    def _obter_token(self):
        if self.token is None:
            self.token = self._ler_token()
        if (self.token and not set(ESCOPO.split()).issubset(set(self.token.get("scope", "").split()))
                and not self._autorizacao_ampliada_tentada):
            self._autorizacao_ampliada_tentada = True
            self.log("Spotify a seguir: precisa de autorização adicional para ler a fila; abrindo o navegador.")
            # O token anterior permanece salvo se a nova autorização for cancelada.
            return self._autorizar()
        if self.token and self.token.get("access_token") and time.time() < self.token.get("expires_at", 0) - 60:
            return self.token["access_token"]
        if self.token and self.token.get("refresh_token"):
            return self._renovar_token()
        return self._autorizar()

    def _ler_token(self):
        try:
            dados = _proteger_dados(ARQUIVO_TOKEN.read_bytes(), proteger=False)
            return json.loads(dados.decode("utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:
            self.log(f"Spotify playlist: token local inválido, será solicitada nova autorização ({exc}).")
            try:
                ARQUIVO_TOKEN.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _salvar_token(self, token):
        ARQUIVO_TOKEN.parent.mkdir(parents=True, exist_ok=True)
        protegido = _proteger_dados(json.dumps(token).encode("utf-8"), proteger=True)
        ARQUIVO_TOKEN.write_bytes(protegido)
        self.token = token

    @staticmethod
    def _desafio_pkce(verificador):
        resumo = hashlib.sha256(verificador.encode("ascii")).digest()
        return base64.urlsafe_b64encode(resumo).decode("ascii").rstrip("=")

    def _autorizar(self):
        estado = secrets.token_urlsafe(24)
        verificador = secrets.token_urlsafe(64)
        parametros = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": ESCOPO,
            "state": estado,
            "code_challenge_method": "S256",
            "code_challenge": self._desafio_pkce(verificador),
        }
        url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(parametros)
        resultado = {}

        class Retorno(BaseHTTPRequestHandler):
            def do_GET(self):
                consulta = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if consulta.get("state", [None])[0] != estado:
                    resultado["erro"] = "resposta OAuth com state inválido"
                    codigo, mensagem = 400, "Autorização inválida. Você pode fechar esta janela."
                elif consulta.get("error"):
                    resultado["erro"] = consulta["error"][0]
                    codigo, mensagem = 400, "Autorização cancelada. Você pode fechar esta janela."
                else:
                    resultado["code"] = consulta.get("code", [None])[0]
                    codigo, mensagem = 200, "Spotify conectado. Você pode fechar esta janela."
                corpo = ("<!doctype html><meta charset='utf-8'><title>Spotify</title><p>"
                         + mensagem + "</p>").encode("utf-8")
                self.send_response(codigo)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def log_message(self, _formato, *_args):
                pass

        servidor = HTTPServer(("127.0.0.1", 8765), Retorno)
        servidor.timeout = 180
        self.log("Spotify playlist: aguardando autorização no navegador...")
        webbrowser.open(url)
        try:
            servidor.handle_request()
        finally:
            servidor.server_close()
        if resultado.get("erro"):
            raise RuntimeError(f"autorização não concluída: {resultado['erro']}")
        if not resultado.get("code"):
            raise TimeoutError("tempo de autorização do Spotify esgotado")

        resposta = _ler_json_http(
            "https://accounts.spotify.com/api/token",
            dados=urllib.parse.urlencode({
                "grant_type": "authorization_code",
                "code": resultado["code"],
                "redirect_uri": REDIRECT_URI,
                "client_id": self.client_id,
                "code_verifier": verificador,
            }).encode("ascii"),
            cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resposta["expires_at"] = time.time() + resposta.get("expires_in", 3600)
        self._salvar_token(resposta)
        self.log("Spotify playlist: autorização concluída.")
        return resposta["access_token"]

    def _renovar_token(self):
        try:
            resposta = _ler_json_http(
                "https://accounts.spotify.com/api/token",
                dados=urllib.parse.urlencode({
                    "grant_type": "refresh_token",
                    "refresh_token": self.token["refresh_token"],
                    "client_id": self.client_id,
                }).encode("ascii"),
                cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 401):
                raise
            self.token = None
            try:
                ARQUIVO_TOKEN.unlink(missing_ok=True)
            except OSError:
                pass
            return self._autorizar()
        resposta["refresh_token"] = resposta.get("refresh_token", self.token["refresh_token"])
        resposta["scope"] = resposta.get("scope", self.token.get("scope", ""))
        resposta["expires_at"] = time.time() + resposta.get("expires_in", 3600)
        self._salvar_token(resposta)
        return resposta["access_token"]
