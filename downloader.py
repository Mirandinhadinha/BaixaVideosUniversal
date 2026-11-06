"""Downloads completos, cookies limitados ao domínio e arquivos temporários."""
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from http.cookiejar import Cookie
from pathlib import Path

import requests
import yt_dlp
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import PASTA_DOWNLOAD, TIMEOUT_REDE
from detector import classificar_midia, mensagem_segura
from comunicacao import conferir_cancelamento, saida


class FalhaDownload(RuntimeError):
    pass


def limpar_nome(nome):
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "", str(nome))
    nome = re.sub(r"\s+", " ", nome).strip(" .")[:150].rstrip(" .") or "video"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", nome, re.I):
        nome = "_" + nome
    return nome


def obter_ffmpeg():
    caminho = shutil.which("ffmpeg")
    if caminho:
        return caminho
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def montar_headers(resultado, pagina_original):
    originais = {k.lower(): v for k, v in resultado.get("headers", {}).items()}
    headers = {"Referer": pagina_original, "Accept-Encoding": "identity"}
    for chave in ("user-agent", "referer", "origin", "accept", "accept-language"):
        if chave in originais:
            headers[chave.title()] = originais[chave]
    # Range pertence à reprodução de um trecho. Nunca reutilizá-lo no download.
    # Cookies são enviados pelo cookiejar, que respeita domínio, caminho e HTTPS.
    return headers


def adicionar_cookies(jar, cookies):
    for item in cookies:
        dominio = item.get("domain", "")
        if not dominio:
            continue
        expira = item.get("expires", -1)
        jar.set_cookie(Cookie(
            version=0, name=item["name"], value=item["value"],
            port=None, port_specified=False,
            domain=dominio, domain_specified=dominio.startswith("."),
            domain_initial_dot=dominio.startswith("."),
            path=item.get("path", "/"), path_specified=True,
            secure=item.get("secure", False),
            expires=int(expira) if expira and expira > 0 else None,
            discard=not expira or expira <= 0, comment=None, comment_url=None,
            rest={"HttpOnly": None} if item.get("httpOnly") else {}, rfc2109=False,
        ))


def caminho_livre(pasta, titulo, extensao):
    pasta = Path(pasta).expanduser().resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    base = limpar_nome(titulo)
    indice = 0
    while True:
        nome = base if indice == 0 else f"{base} ({indice})"
        caminho = pasta / (nome + extensao)
        if not caminho.exists() and not Path(str(caminho) + ".part").exists():
            return caminho
        indice += 1


def _extensao(url, content_type):
    from urllib.parse import urlsplit
    ext = Path(urlsplit(url).path).suffix.lower()
    if ext in {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".ogv", ".avi"}:
        return ext
    mime = content_type.split(";", 1)[0].lower().strip()
    return {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
            "video/x-matroska": ".mkv"}.get(mime) or mimetypes.guess_extension(mime) or ".mp4"


def _conferir_inicio(bloco):
    inicio = bloco.lstrip().lower()
    if inicio.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"[", b"#extm3u")):
        raise FalhaDownload("O servidor devolveu uma página ou manifesto em vez do arquivo de vídeo.")


def conferir_video(caminho):
    """Confirma que há pelo menos um frame decodificável, sem alterar o arquivo."""
    ffmpeg = obter_ffmpeg()
    if not ffmpeg:
        return
    try:
        resultado = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-i", str(caminho), "-map", "0:v:0",
             "-frames:v", "1", "-f", "framehash", "-"],
            capture_output=True, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired as erro:
        raise FalhaDownload("O arquivo não passou pela verificação de reprodução a tempo.") from erro
    if resultado.returncode or not re.search(rb"(?m)^\s*\d+,", resultado.stdout):
        raise FalhaDownload("O arquivo recebido não contém um vídeo reproduzível; ele não foi confirmado.")


def baixar_direto(resultado, pagina_original, pasta=PASTA_DOWNLOAD, *,
                  silencioso=False, ao_progresso=None, cancelamento=None):
    emitir = saida(silencioso)
    conferir_cancelamento(cancelamento)
    headers = montar_headers(resultado, pagina_original)
    sessao = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    sessao.mount("https://", HTTPAdapter(max_retries=retry))
    sessao.mount("http://", HTTPAdapter(max_retries=retry))
    adicionar_cookies(sessao.cookies, resultado.get("cookies", []))
    caminho = temporario = None
    baixado = 0
    total = None
    ultimo_progresso = 0.0
    try:
        # Alguns CDNs entregam apenas uma faixa por resposta, mesmo sem Range.
        # Continuamos as faixas até o tamanho total; nunca aceitamos só o trecho.
        while True:
            conferir_cancelamento(cancelamento)
            envio = dict(headers)
            if baixado:
                envio["Range"] = f"bytes={baixado}-"
            with sessao.get(resultado["url"], headers=envio, stream=True, timeout=TIMEOUT_REDE) as resposta:
                resposta.raise_for_status()
                mime = resposta.headers.get("Content-Type", "")
                if classificar_midia(resposta.url, mime) != "video":
                    raise FalhaDownload("A resposta do servidor não é um vídeo progressivo válido.")
                if caminho is None:
                    caminho = caminho_livre(pasta, resultado.get("titulo", "video"), _extensao(resposta.url, mime))
                    temporario = Path(str(caminho) + ".part")
                comprimento = resposta.headers.get("Content-Length")
                esperado_resposta = int(comprimento) if comprimento else None
                if resposta.status_code == 206:
                    faixa = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", resposta.headers.get("Content-Range", ""))
                    if not faixa:
                        raise FalhaDownload("Resposta parcial sem o tamanho total do vídeo.")
                    inicio, fim, tamanho = map(int, faixa.groups())
                    if inicio != baixado or fim < inicio or fim >= tamanho or total not in (None, tamanho):
                        raise FalhaDownload("O servidor devolveu faixas inconsistentes; arquivo descartado.")
                    total = tamanho
                    esperado_resposta = fim - inicio + 1
                elif resposta.status_code == 200 and baixado == 0:
                    total = esperado_resposta
                else:
                    raise FalhaDownload("O servidor não permitiu continuar o vídeo por faixas.")
                recebido = 0
                with temporario.open("ab" if baixado else "wb") as arquivo:
                    for bloco in resposta.iter_content(chunk_size=256 * 1024):
                        conferir_cancelamento(cancelamento)
                        if not bloco:
                            continue
                        if ao_progresso:
                            ao_progresso()
                        if baixado == 0:
                            _conferir_inicio(bloco)
                        arquivo.write(bloco)
                        recebido += len(bloco)
                        baixado += len(bloco)
                        if time.monotonic() - ultimo_progresso >= 0.5:
                            progresso = f"{baixado / total * 100:5.1f}% | " if total else ""
                            emitir(f"\rBaixando: {progresso}{baixado / 1048576:.1f} MB", end="", flush=True)
                            ultimo_progresso = time.monotonic()
                if not recebido or esperado_resposta is not None and recebido != esperado_resposta:
                    raise FalhaDownload("O vídeo chegou incompleto; o download não foi confirmado.")
                if total is None or baixado == total:
                    break
                if baixado > total:
                    raise FalhaDownload("O tamanho recebido não corresponde ao vídeo.")
        conferir_cancelamento(cancelamento)
        conferir_video(temporario)
        conferir_cancelamento(cancelamento)
        temporario.replace(caminho)
        emitir()
        return caminho
    except BaseException:
        if temporario:
            temporario.unlink(missing_ok=True)
        raise
    finally:
        sessao.close()


class LoggerSeguro:
    def __init__(self, silencioso=False):
        self.emitir = saida(silencioso)

    def debug(self, mensagem):
        pass

    def warning(self, mensagem):
        self.emitir("Aviso:", mensagem_segura(mensagem))

    def error(self, mensagem):
        pass  # A exceção é exibida uma única vez pelo chamador.


def baixar_ytdlp(url, pasta=PASTA_DOWNLOAD, resultado=None, pagina_original=None, *,
                 silencioso=False, ao_progresso=None, cancelamento=None):
    emitir = saida(silencioso)
    conferir_cancelamento(cancelamento)
    pasta = Path(pasta).expanduser().resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    ffmpeg = obter_ffmpeg()
    opcoes = {
        "format": "bv*+ba/b" if ffmpeg else "b",
        "noplaylist": True, "playlist_items": "1",
        "quiet": silencioso, "logger": LoggerSeguro(silencioso),
        "windowsfilenames": True, "overwrites": False,
        "retries": 3, "fragment_retries": 3,
        "skip_unavailable_fragments": False,
        "socket_timeout": 30, "concurrent_fragment_downloads": 2,
        "outtmpl": str(pasta / "%(title).120B [%(id).40B].%(ext)s"),
    }
    if ffmpeg:
        opcoes.update(ffmpeg_location=ffmpeg, merge_output_format="mp4/mkv")
    if os.environ.get("SSL_CERT_FILE"):
        # Respeita uma CA explicitamente configurada no ambiente, mantendo TLS ativo.
        opcoes["compat_opts"] = {"no-certifi"}
    if resultado:
        if resultado["tipo"] in {"m3u8", "mpd"} and not ffmpeg:
            raise FalhaDownload("Não foi possível preparar o vídeo. O componente FFmpeg está ausente.")
        base = caminho_livre(pasta, resultado.get("titulo", "video"), ".mp4").stem
        # Evita colisões também com outros contêineres produzidos pelo yt-dlp.
        indice = 1
        while any(p.name.startswith(base + ".") for p in pasta.iterdir()):
            base = limpar_nome(resultado.get("titulo", "video")) + f" ({indice})"
            indice += 1
        opcoes["outtmpl"] = str(pasta / (base.replace("%", "%%") + ".%(ext)s"))
        opcoes["http_headers"] = montar_headers(resultado, pagina_original)
    arquivos_finalizados = []
    ultimo_progresso = 0.0

    def progresso(evento):
        nonlocal ultimo_progresso
        conferir_cancelamento(cancelamento)
        if evento.get("status") == "downloading" and ao_progresso:
            ao_progresso()
        if evento.get("status") == "downloading" and time.monotonic() - ultimo_progresso >= 0.7:
            baixado = evento.get("downloaded_bytes", 0)
            total = evento.get("total_bytes") or evento.get("total_bytes_estimate")
            percentual = f"{baixado / total * 100:5.1f}% | " if total else ""
            emitir(f"\rBaixando: {percentual}{baixado / 1048576:.1f} MB", end="", flush=True)
            ultimo_progresso = time.monotonic()
        elif evento.get("status") == "finished":
            emitir("\nArquivo recebido. Verificando o vídeo...")

    def registrar(evento):
        if evento.get("status") == "finished":
            info = evento.get("info_dict", {})
            arquivo = info.get("filepath") or evento.get("filename")
            if arquivo:
                arquivos_finalizados.append(Path(arquivo))

    opcoes["postprocessor_hooks"] = [registrar]
    opcoes["progress_hooks"] = [progresso]
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        if resultado:
            adicionar_cookies(ydl.cookiejar, resultado.get("cookies", []))
        info = ydl.extract_info(url, download=True)
        conferir_cancelamento(cancelamento)
        if not info:
            raise FalhaDownload("O extrator não retornou um vídeo.")
        if info.get("entries"):
            entradas = [e for e in info["entries"] if e]
            if not entradas:
                raise FalhaDownload("Nenhum vídeo disponível nesta página.")
            info = entradas[0]
        candidatos = arquivos_finalizados[:]
        if info.get("filepath"):
            candidatos.append(Path(info["filepath"]))
        candidatos += [Path(d["filepath"]) for d in info.get("requested_downloads", []) if d.get("filepath")]
        candidatos.append(Path(ydl.prepare_filename(info)))
        if resultado:
            candidatos += [p for p in pasta.iterdir() if p.name.startswith(base + ".")
                           and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}]
        for caminho in reversed(candidatos):
            if caminho.is_file() and caminho.stat().st_size > 0 and caminho.suffix not in {".part", ".ytdl"}:
                conferir_video(caminho)
                conferir_cancelamento(cancelamento)
                return caminho.resolve()
    raise FalhaDownload("Não foi possível confirmar um arquivo finalizado.")


def baixar_midia(resultado, pagina_original, pasta=PASTA_DOWNLOAD, *,
                 silencioso=False, ao_progresso=None, cancelamento=None):
    if resultado.get("drm"):
        raise FalhaDownload("O player sinalizou proteção DRM; essa fonte não é compatível.")
    if resultado["tipo"] == "video":
        return baixar_direto(resultado, pagina_original, pasta, silencioso=silencioso,
                             ao_progresso=ao_progresso, cancelamento=cancelamento)
    return baixar_ytdlp(resultado["url"], pasta, resultado, pagina_original,
                       silencioso=silencioso, ao_progresso=ao_progresso, cancelamento=cancelamento)


def tentar_ytdlp(url, pasta=PASTA_DOWNLOAD, *, silencioso=False, ao_progresso=None, cancelamento=None):
    emitir = saida(silencioso)
    emitir("Tentando o extrator direto...")
    try:
        return baixar_ytdlp(url, pasta, silencioso=silencioso, ao_progresso=ao_progresso,
                           cancelamento=cancelamento)
    except (yt_dlp.utils.DownloadError, FalhaDownload, requests.RequestException) as erro:
        conferir_cancelamento(cancelamento)
        emitir("O extrator direto não concluiu:", mensagem_segura(erro))
        return None
