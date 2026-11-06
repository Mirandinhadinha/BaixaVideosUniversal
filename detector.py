"""Classificação de fontes completas: vídeo progressivo, HLS e DASH."""
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


EXTENSOES_VIDEO = {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".ogv", ".avi"}
SEGMENTO = re.compile(
    r"(?:^|[/_.-])(?:init(?:[_.-]|$)|(?:seg(?:ment)?|chunk|frag(?:ment)?)[_-]?\d)", re.I
)


def validar_url(url):
    url = url.strip().strip("\"'<>")
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Cole um link completo começando com https:// ou http://.")
    if any(c.isspace() for c in url) or any(ord(c) < 32 for c in url):
        raise ValueError("O link contém espaços ou caracteres inválidos.")
    _ = p.port  # Valida também uma eventual porta.
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def classificar_midia(url, content_type=""):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"}:
        return None  # blob: é uma referência interna, não um arquivo remoto.
    path = p.path.lower()
    mime = content_type.lower().split(";", 1)[0].strip()
    if mime.startswith(("image/", "text/html")) or mime in {"application/json", "text/json"}:
        return None
    if path.endswith((".ts", ".m4s", ".aac", ".vtt")) or mime == "video/mp2t":
        return None
    if path.endswith(".m3u8") or "mpegurl" in mime:
        return "m3u8"
    if path.endswith(".mpd") or mime == "application/dash+xml":
        return "mpd"
    if SEGMENTO.search(path):
        return None
    if mime.startswith("video/"):
        return "video"
    if any(path.endswith(ext) for ext in EXTENSOES_VIDEO):
        return "video"
    query = parse_qs(p.query)
    if any(v.lower() in {"video/mp4", "video_mp4", "video/webm"}
           for k in ("mime", "mime_type") for v in query.get(k, [])):
        return "video"
    return None


def eh_video(url, content_type=""):
    return classificar_midia(url, content_type) == "video"


def eh_m3u8(url, content_type=""):
    return classificar_midia(url, content_type) == "m3u8"


def manifesto_com_drm(texto, tipo):
    if tipo == "mpd":
        return bool(re.search(r"<(?:\w+:)?ContentProtection\b", texto, re.I))
    return bool(re.search(
        r"#EXT-X-(?:SESSION-)?KEY:[^\r\n]*(?:METHOD=SAMPLE-AES|"
        r"KEYFORMAT=\"(?!identity\")[^\"]+\")", texto, re.I
    ))


def ordenar_midias(midias, fontes_ativas=()):
    """Prioriza o src do player, depois manifestos; empate favorece a primeira fonte.

    O último recurso pode ser o pré-carregamento do episódio seguinte.
    Segmentos nunca entram nesta lista.
    """
    ativas = set(fontes_ativas)

    def pontos(item):
        valor = {"video": 30, "m3u8": 60, "mpd": 60}.get(item["tipo"], 0)
        if item.get("master"):
            valor += 15
        if item["url"] in ativas:
            valor += 1000
        if item.get("origem") == "rede":
            valor += 20
        if item.get("anuncio"):
            valor -= 10000
        return valor

    return sorted((m for m in midias if not m.get("drm") and not m.get("anuncio")),
                  key=pontos, reverse=True)


def dominio(url):
    return urlsplit(url).hostname or "desconhecido"


def mensagem_segura(erro):
    # Erros de bibliotecas às vezes incluem links assinados e cookies.
    texto = re.sub(r"https?://[^\s<>\"']+", lambda m: dominio(m.group(0)) + "/[link omitido]", str(erro))
    return re.sub(r"(?im)(authorization|cookie|set-cookie)\s*:[^\r\n]*", r"\1: [omitido]", texto)[:1200]
