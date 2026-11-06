"""Regressões de seleção, sessão e integridade com mídia sintética local."""
import contextlib
import hashlib
import http.server
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from playwright.sync_api import Error, sync_playwright

from detector import classificar_midia, manifesto_com_drm, ordenar_midias, validar_url
from downloader import (FalhaDownload, adicionar_cookies, baixar_midia, limpar_nome,
                        montar_headers, obter_ffmpeg)
from navegador import sessao_video
from comunicacao import DownloadCancelado
from servico import baixar_video


class RegrasTest(unittest.TestCase):
    def test_segmentos_e_erros_nao_sao_videos_completos(self):
        for url, mime in [("https://cdn.test/x.ts", "video/mp2t"),
                          ("https://cdn.test/init.mp4", "video/mp4"),
                          ("https://cdn.test/chunk-12.mp4", "video/mp4"),
                          ("https://cdn.test/x.m4s", "video/mp4"),
                          ("https://cdn.test/x.mp4", "text/html"),
                          ("https://cdn.test/imagem.jpg?fake=.mp4", "image/jpeg"),
                          ("blob:https://site.test/id", "video/mp4")]:
            with self.subTest(url=url):
                self.assertIsNone(classificar_midia(url, mime))

    def test_manifesta_sem_extensao_e_assinatura_sao_reconhecidos(self):
        self.assertEqual(classificar_midia("https://cdn.test/play?token=ABC", "application/vnd.apple.mpegurl"), "m3u8")
        self.assertEqual(classificar_midia("https://cdn.test/play?token=ABC", "application/dash+xml"), "mpd")
        self.assertEqual(classificar_midia("https://cdn.test/video.MP4?token=ABC"), "video")

    def test_player_atual_vence_precarregamento_e_anuncio(self):
        primeiro = {"url": "https://cdn.test/current.mp4", "tipo": "video", "origem": "rede"}
        proximo = {"url": "https://cdn.test/next.mp4", "tipo": "video", "origem": "rede"}
        anuncio = {"url": "https://ad.test/ad.m3u8", "tipo": "m3u8", "master": True, "anuncio": True}
        self.assertIs(ordenar_midias([primeiro, proximo, anuncio], [primeiro["url"]])[0], primeiro)
        self.assertIs(ordenar_midias([primeiro, proximo])[0], primeiro)

    def test_drm_e_diferente_de_hls_aes128(self):
        self.assertTrue(manifesto_com_drm('#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://x"', "m3u8"))
        self.assertFalse(manifesto_com_drm('#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.test/key"', "m3u8"))
        self.assertTrue(manifesto_com_drm('<MPD><ContentProtection schemeIdUri="x"/></MPD>', "mpd"))

    def test_link_e_nome_validos_no_windows(self):
        self.assertEqual(validar_url("https://site.test/ep?x=1#episodes"), "https://site.test/ep?x=1")
        for url in ["file:///tmp/a", "javascript:alert(1)", "https://user:password@site.test", "https://site.test/a b"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validar_url(url)
        self.assertEqual(limpar_nome("CON"), "_CON")
        self.assertEqual(limpar_nome("a:b/c?* ."), "abc")

    def test_headers_sem_range_cookie_e_sem_duplicacao_de_referer(self):
        h = montar_headers({"headers": {"range": "bytes=100-199", "cookie": "private=secret",
                                         "referer": "https://player.test/frame", "user-agent": "Browser"}},
                           "https://pagina.test")
        self.assertNotIn("range", {k.lower() for k in h})
        self.assertNotIn("cookie", {k.lower() for k in h})
        self.assertEqual(h["Referer"], "https://player.test/frame")
        self.assertEqual(sum(k.lower() == "referer" for k in h), 1)

    def test_cookies_respeitam_dominio_caminho_e_https(self):
        with requests.Session() as sessao:
            adicionar_cookies(sessao.cookies, [dict(name="sessao", value="segredo", domain="cdn.test", path="/media", secure=True)])
            for url, esperado in [("https://cdn.test/media/a.mp4", True),
                                  ("https://other.test/media/a.mp4", False),
                                  ("https://cdn.test/outro/a.mp4", False),
                                  ("http://cdn.test/media/a.mp4", False)]:
                with self.subTest(url=url):
                    req = sessao.prepare_request(requests.Request("GET", url))
                    self.assertEqual("sessao=segredo" in req.headers.get("Cookie", ""), esperado)


class IntegracaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ffmpeg = obter_ffmpeg()
        if not ffmpeg:
            raise unittest.SkipTest("FFmpeg não está instalado")
        cls.temp = tempfile.TemporaryDirectory()
        cls.raiz = Path(cls.temp.name)
        cls.clip = cls.raiz / "clip.mp4"
        comando = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
                   "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100", "-t", "2",
                   "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-movflags", "+faststart", str(cls.clip)]
        subprocess.run(comando, check=True, capture_output=True, timeout=30)
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(cls.clip), "-c", "copy",
                        "-hls_time", "1", "-hls_playlist_type", "vod", str(cls.raiz / "playlist.m3u8")],
                       check=True, capture_output=True, timeout=30)
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(cls.clip), "-map", "0:v", "-map", "0:a",
                        "-c", "copy", "-seg_duration", "1", "-f", "dash", "manifest.mpd"],
                       check=True, capture_output=True, timeout=30, cwd=cls.raiz)
        cls.bytes_clip = cls.clip.read_bytes()
        cls.faixas = []
        raiz, dados, faixas = cls.raiz, cls.bytes_clip, cls.faixas

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_HEAD(self):
                self.responder(True)

            def do_GET(self):
                self.responder(False)

            def responder(self, head):
                path = urlsplit(self.path).path
                corpo, mime, status, extras = b"", "text/html", 200, {}
                if path == "/watch":
                    corpo = b'<title>Teste episodio 12</title><iframe src="/frame" width="500" height="400"></iframe>'
                elif path == "/frame":
                    corpo = b'''<button aria-label="Reproduzir" onclick="start(this)">Reproduzir</button>
                    <script>function start(b) { b.remove(); setTimeout(() => {
                        const v=document.createElement('video'); v.width=320; v.height=200; v.muted=true;
                        v.src='/current.mp4?token=preservar'; document.body.appendChild(v); v.play().catch(()=>{});
                        setTimeout(()=>fetch('/next.mp4'), 600);
                    }, 800); }</script>'''
                    extras["Set-Cookie"] = "play_session=ok; Path=/; HttpOnly"
                elif path in {"/current.mp4", "/next.mp4", "/normal.mp4"}:
                    if path == "/current.mp4" and ("play_session=ok" not in self.headers.get("Cookie", "")
                            or not self.headers.get("Referer", "").endswith("/frame")):
                        status = 403
                        corpo = b"Sessao ou referer ausente"
                    else:
                        corpo, mime = dados, "video/mp4"
                elif path in {"/limited.mp4", "/bad-range.mp4"}:
                    inicio = int(re.search(r"bytes=(\d+)-", self.headers.get("Range", "bytes=0-")).group(1))
                    fim = min(inicio + 4095, len(dados) - 1)
                    corpo, mime, status = dados[inicio:fim + 1], "video/mp4", 206
                    extras["Content-Range"] = f"bytes {inicio if path == '/limited.mp4' else 10}-{fim}/{len(dados)}"
                    faixas.append(inicio)
                elif path == "/fake.mp4":
                    corpo, mime = b"<!DOCTYPE html><html>erro</html>", "video/mp4"
                elif path == "/incompleto.mp4":
                    corpo, mime = dados[:500], "video/mp4"
                elif path == "/hls/63002060":
                    # O Dramatip usa HLS sem .m3u8 na URL: só o MIME revela o tipo.
                    texto = (raiz / "playlist.m3u8").read_text()
                    corpo = "\n".join("/" + linha if linha and not linha.startswith("#") else linha
                                      for linha in texto.splitlines()).encode()
                    mime = "application/vnd.apple.mpegurl"
                else:
                    arquivo = (raiz / path.lstrip("/")).resolve()
                    if arquivo.parent != raiz or not arquivo.is_file():
                        status, corpo = 404, b"Not found"
                    else:
                        corpo = arquivo.read_bytes()
                        mime = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t",
                                ".mpd": "application/dash+xml", ".m4s": "application/octet-stream"}.get(arquivo.suffix, "application/octet-stream")
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(corpo)))
                for k, v in extras.items():
                    self.send_header(k, v)
                self.end_headers()
                if not head:
                    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                        self.wfile.write(corpo)

        cls.servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.url = f"http://127.0.0.1:{cls.servidor.server_port}"
        cls.thread = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def setUp(self):
        self.destino = tempfile.TemporaryDirectory()
        self.pasta = Path(self.destino.name)
        self.addCleanup(self.destino.cleanup)

    def fonte(self, caminho, tipo="video"):
        return {"url": self.url + caminho, "tipo": tipo, "titulo": "Episodio 12",
                "headers": {"range": "bytes=100-199"}, "cookies": []}

    def test_arquivo_completo_sem_sobrescrever_o_anterior(self):
        a = baixar_midia(self.fonte("/normal.mp4"), self.url, self.pasta)
        b = baixar_midia(self.fonte("/normal.mp4"), self.url, self.pasta)
        self.assertNotEqual(a, b)
        self.assertEqual(a.read_bytes(), self.bytes_clip)
        self.assertEqual(b.read_bytes(), self.bytes_clip)

    def test_servico_informa_poucos_estados_sem_escrever_no_console(self):
        mensagens, console = [], io.StringIO()
        with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
            caminho = baixar_video(self.url + "/normal.mp4", pasta=self.pasta,
                                    informar=mensagens.append)
        self.assertEqual(caminho.read_bytes(), self.bytes_clip)
        self.assertEqual(console.getvalue(), "")
        self.assertLessEqual(len(mensagens), 3)
        self.assertIn("Baixando o vídeo…", mensagens)

    def test_cancelamento_descarta_arquivo_progressivo_incompleto(self):
        cancelar = threading.Event()
        with self.assertRaises(DownloadCancelado):
            baixar_midia(self.fonte("/normal.mp4"), self.url, self.pasta,
                         silencioso=True, cancelamento=cancelar, ao_progresso=cancelar.set)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_reune_faixas_em_vez_de_salvar_trecho(self):
        self.faixas.clear()
        caminho = baixar_midia(self.fonte("/limited.mp4"), self.url, self.pasta)
        self.assertGreater(len(self.faixas), 1)
        self.assertEqual(hashlib.sha256(caminho.read_bytes()).digest(), hashlib.sha256(self.bytes_clip).digest())

    def test_html_faixa_errada_e_mp4_truncado_nao_viram_sucesso(self):
        for path in ["/fake.mp4", "/bad-range.mp4", "/incompleto.mp4"]:
            with self.subTest(path=path), self.assertRaises(FalhaDownload):
                baixar_midia(self.fonte(path), self.url, self.pasta)
        self.assertEqual(list(self.pasta.iterdir()), [])

    def test_hls_com_video_e_audio(self):
        caminho = baixar_midia(self.fonte("/playlist.m3u8", "m3u8"), self.url, self.pasta)
        self.assertTrue(caminho.is_file())
        self._conferir_faixas(caminho)

    def test_hls_sem_extensao_como_no_dramatip(self):
        caminho = baixar_midia(self.fonte("/hls/63002060", "m3u8"), self.url, self.pasta)
        self.assertTrue(caminho.is_file())
        self._conferir_faixas(caminho)

    def test_dash_mescla_video_e_audio(self):
        caminho = baixar_midia(self.fonte("/manifest.mpd", "mpd"), self.url, self.pasta)
        self.assertTrue(caminho.is_file())
        self._conferir_faixas(caminho)

    def _conferir_faixas(self, caminho):
        if shutil.which("ffprobe"):
            result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(caminho)],
                                    check=True, capture_output=True, text=True, timeout=15)
            tipos = {s["codec_type"] for s in json.loads(result.stdout)["streams"]}
            self.assertEqual(tipos, {"video", "audio"})

    def test_iframe_play_tardio_cookies_e_preload_do_proximo(self):
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(headless=True)
                b.close()
            except Error:
                self.skipTest("Chromium não está instalado")
        with patch("navegador._proxy_ambiente", return_value=None):
            with sessao_video(self.url + "/watch", navegador="chromium", tempo=15,
                              diagnostico=True, pasta_diagnostico=self.pasta / "diagnostico") as fontes:
                self.assertIn("/current.mp4?token=preservar", fontes[0]["url"])
                self.assertTrue(any("/next.mp4" in f["url"] for f in fontes))
                self.assertTrue(any(c["name"] == "play_session" for c in fontes[0]["cookies"]))
                caminho = baixar_midia(fontes[0], self.url + "/watch", self.pasta)
                self.assertEqual(caminho.read_bytes(), self.bytes_clip)
        diagnostico = next((self.pasta / "diagnostico").glob("*.json")).read_text()
        for segredo in ["play_session", "preservar", "Cookie", "current.mp4"]:
            self.assertNotIn(segredo, diagnostico)


if __name__ == "__main__":
    unittest.main()
