"""Observa o player real, inclusive iframes, sem bloquear os eventos de rede."""
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, TimeoutError as PlaywrightTimeout, sync_playwright

from config import (HEADLESS, NAVEGADOR, PASTA_PROGRAMA, TEMPO_DETECCAO,
                    TEMPO_DETECCAO_MANUAL, TEMPO_ESTABILIZACAO, TIMEOUT_PAGINA, USER_AGENT)
from detector import classificar_midia, dominio, manifesto_com_drm, mensagem_segura, ordenar_midias
from comunicacao import conferir_cancelamento, saida


PLAY = re.compile(r"^(?:play|play video|reproduzir|reproduzir vídeo|assistir|iniciar vídeo|lecture|reproducir)$", re.I)
SELETOR_PLAY = ".vjs-big-play-button, .jw-display-icon-container, .plyr__control--overlaid, .dplayer-play-icon"
LER_VIDEOS = """() => Array.from(document.querySelectorAll('video')).map(v => {
    const r = v.getBoundingClientRect();
    return {src: v.currentSrc || v.src, visible: r.width > 30 && r.height > 30,
        width: r.width, height: r.height, paused: v.paused,
        ready: v.readyState, drm: !!v.mediaKeys};
})"""


def _proxy_ambiente():
    valor = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not valor:
        return None
    p = urlsplit(valor)
    if not p.hostname or not p.scheme:
        return None
    servidor = f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")
    proxy = {"server": servidor}
    if p.username:
        from urllib.parse import unquote
        proxy["username"] = unquote(p.username)
        proxy["password"] = unquote(p.password or "")
    bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if bypass:
        proxy["bypass"] = bypass
    return proxy


def _abrir_browser(playwright, headless, navegador, silencioso=False):
    canais = {"auto": ["chrome", "msedge", None], "chrome": ["chrome"],
              "edge": ["msedge"], "chromium": [None]}[navegador]
    for canal in canais:
        try:
            # Silencia também áudio iniciado pelo próprio site, anúncios e Web Audio.
            # Esse parâmetro afeta só a reprodução, não o áudio do arquivo baixado.
            opcoes = {"headless": headless, "args": ["--mute-audio"]}
            if canal:
                opcoes["channel"] = canal
            proxy = _proxy_ambiente()
            if proxy:
                opcoes["proxy"] = proxy
            browser = playwright.chromium.launch(**opcoes)
            saida(silencioso)("Navegador:", canal or "chromium")
            return browser
        except Error:
            continue
    raise RuntimeError(
        "Não foi possível iniciar o navegador em segundo plano. "
        "Verifique se Chrome, Edge ou Chromium está instalado."
    )


def _acionar_play(pagina, tentativas):
    for frame in pagina.frames:
        try:
            # A interação real também inicializa players que ainda não criaram <video>.
            for grupo, locator in enumerate((frame.get_by_role("button", name=PLAY), frame.locator(SELETOR_PLAY))):
                for i in range(min(locator.count(), 3)):
                    botao = locator.nth(i)
                    chave = (id(frame), grupo, i)
                    if chave in tentativas or not botao.is_visible():
                        continue
                    botao.click(timeout=1200)
                    tentativas.add(chave)
                    break
            # Mute permite iniciar o vídeo sob as regras de autoplay do navegador.
            frame.locator("video").evaluate_all("""vs => vs.forEach(v => {
                v.muted = true;
                if (v.getBoundingClientRect().width > 30 && v.paused) {
                    v.play().catch(() => {});
                }
            })""")
        except Error:
            continue  # O frame pode ter sido substituído durante o carregamento.


def _fontes_player(pagina):
    fontes = []
    drm = False
    for frame in pagina.frames:
        try:
            videos = frame.evaluate(LER_VIDEOS)
            for video in videos:
                if video["visible"]:
                    video["frame"] = frame.url
                    fontes.append(video)
                    drm = drm or video["drm"]
        except Error:
            continue
    fontes.sort(key=lambda v: (not v["paused"], v["width"] * v["height"]), reverse=True)
    return fontes, drm


def _diagnostico(pasta, midias, falhas, drm, url_pagina):
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / ("diagnostico_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
    dados = {"site": dominio(url_pagina), "drm_sinalizado": drm,
             "midias": [{"tipo": m["tipo"], "servidor": dominio(m["url"]),
                         "origem": m.get("origem"), "status": m.get("status"),
                         "manifesto_principal": m.get("master", False), "drm": m.get("drm", False)} for m in midias],
             "falhas": falhas}
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Diagnóstico:", caminho)


@contextmanager
def sessao_video(url_pagina, *, headless=HEADLESS, navegador=NAVEGADOR,
                 tempo=TEMPO_DETECCAO, tempo_manual=TEMPO_DETECCAO_MANUAL,
                 diagnostico=False, pasta_diagnostico=None, escolher=False,
                 silencioso=False, cancelamento=None):
    emitir = saida(silencioso)
    conferir_cancelamento(cancelamento)
    midias = {}
    falhas = []
    drm = False
    with sync_playwright() as p:
        browser = _abrir_browser(p, headless, navegador, silencioso)
        try:
            opcoes = {"viewport": {"width": 1280, "height": 850}, "locale": "pt-BR"}
            if USER_AGENT:
                opcoes["user_agent"] = USER_AGENT
            contexto = browser.new_context(**opcoes)
            pagina = contexto.new_page()
            pagina.set_default_timeout(2500)

            def detectar(response):
                try:
                    mime = response.headers.get("content-type", "")
                    tipo = classificar_midia(response.url, mime)
                    if not tipo:
                        return
                    if response.status not in {200, 206}:
                        falhas.append({"servidor": dominio(response.url), "status": response.status})
                        return
                    if midias.get(response.url, {}).get("origem") == "rede":
                        return
                    manifesto = ""
                    if tipo in {"m3u8", "mpd"}:
                        tamanho = int(response.headers.get("content-length", 0))
                        if tamanho < 2_000_000:
                            manifesto = response.text()
                    request = response.request
                    try:
                        frame = request.frame.url
                    except Error:
                        frame = ""
                    host = dominio(response.url)
                    item = {"url": response.url, "tipo": tipo,
                            "headers": request.all_headers(), "origem": "rede", "frame": frame,
                            "content_type": mime, "status": response.status,
                            "master": "#EXT-X-STREAM-INF" in manifesto,
                            "drm": manifesto_com_drm(manifesto, tipo),
                            "anuncio": any(host == d or host.endswith("." + d) for d in
                                           ("doubleclick.net", "googlesyndication.com", "imasdk.googleapis.com"))}
                    midias[response.url] = item
                    emitir(f"Fonte detectada: {tipo.upper()} ({host})")
                except Error:
                    pass
                except (ValueError, TypeError):
                    pass

            # BrowserContext observa a página e seus frames desde antes da navegação.
            contexto.on("response", detectar)
            emitir("Abrindo a página e procurando o player...")
            try:
                pagina.goto(url_pagina, wait_until="domcontentloaded", timeout=TIMEOUT_PAGINA)
            except PlaywrightTimeout:
                emitir("A página ainda está carregando; a detecção continuará.")
            except Error as erro:
                raise RuntimeError("Não consegui abrir a página: " + mensagem_segura(erro)) from erro

            inicio = time.monotonic()
            limite = inicio + tempo
            prazo_final = limite + (0 if headless else tempo_manual)
            avisou_manual = False
            tentativas = set()
            ultimo_play = 0.0
            primeira_fonte = None
            fontes_ativas = []
            while time.monotonic() < prazo_final:
                conferir_cancelamento(cancelamento)
                if pagina.is_closed():
                    raise RuntimeError("A janela do navegador foi fechada antes da detecção.")
                agora = time.monotonic()
                if agora - ultimo_play >= 3:
                    _acionar_play(pagina, tentativas)
                    ultimo_play = agora
                fontes, drm_player = _fontes_player(pagina)
                drm = drm or drm_player
                fontes_ativas = [v["src"] for v in fontes if v["src"]]
                for video in fontes:
                    src = video["src"]
                    tipo = classificar_midia(src)
                    if tipo and src not in midias:
                        midias[src] = {"url": src, "tipo": tipo, "headers": {},
                                       "origem": "player", "frame": video["frame"]}
                validas = ordenar_midias(list(midias.values()), fontes_ativas)
                if validas:
                    primeira_fonte = primeira_fonte or time.monotonic()
                    if time.monotonic() - primeira_fonte >= TEMPO_ESTABILIZACAO:
                        break
                if agora >= limite and not avisou_manual:
                    emitir(f"Dê PLAY no episódio nessa janela. Continuarei procurando por até {tempo_manual}s.")
                    avisou_manual = True
                # Essencial: esta espera mantém o despacho de eventos do Playwright.
                pagina.wait_for_timeout(250)

            titulo = pagina.title() or "video"
            candidatos = ordenar_midias(list(midias.values()), fontes_ativas)
            drm = drm or any(m.get("drm") for m in midias.values())
            if diagnostico:
                _diagnostico(pasta_diagnostico or PASTA_PROGRAMA / "diagnosticos",
                             list(midias.values()), falhas, drm, url_pagina)
            if not candidatos:
                if drm:
                    raise RuntimeError("O player sinalizou DRM. Não há uma fonte compatível disponível.")
                raise RuntimeError(
                    "Nenhum vídeo acessível foi encontrado nesse link. "
                    "Confira o endereço e se o episódio está disponível."
                )
            if escolher and len(candidatos) > 1:
                for indice, item in enumerate(candidatos, 1):
                    print(f"{indice}. {item['tipo'].upper()} - {dominio(item['url'])}"
                          + (" (fonte do player)" if item["url"] in fontes_ativas else ""))
                entrada = input("Escolha a fonte [1]: ").strip()
                try:
                    indice = int(entrada or "1") - 1
                    if not 0 <= indice < len(candidatos):
                        raise ValueError
                except ValueError as erro:
                    raise ValueError("Número de fonte inválido.") from erro
                candidatos = [candidatos[indice]]
            # Cookies permanecem apenas na memória e mantêm seu escopo de domínio.
            cookies = contexto.cookies()
            user_agent = pagina.evaluate("navigator.userAgent")
            for item in candidatos:
                item["titulo"] = titulo
                item["cookies"] = cookies
                item["headers"].setdefault("user-agent", user_agent)
                item["headers"].setdefault("referer", item.get("frame") or url_pagina)
            # O navegador fica aberto durante o download, preservando a sessão.
            conferir_cancelamento(cancelamento)
            yield candidatos
        finally:
            browser.close()
