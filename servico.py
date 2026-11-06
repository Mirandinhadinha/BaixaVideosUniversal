"""Orquestra o download sem depender da interface gráfica."""
from pathlib import Path

from comunicacao import conferir_cancelamento
from config import NAVEGADOR, PASTA_DOWNLOAD
from detector import dominio, validar_url
from downloader import baixar_midia, tentar_ytdlp
from navegador import sessao_video


def baixar_video(link, *, pasta=PASTA_DOWNLOAD, informar=None, cancelamento=None):
    link = validar_url(link)
    pasta = Path(pasta).expanduser().resolve()
    ultima_mensagem = None

    def avisar(mensagem):
        nonlocal ultima_mensagem
        conferir_cancelamento(cancelamento)
        if mensagem != ultima_mensagem and informar:
            informar(mensagem)
        ultima_mensagem = mensagem

    def progresso():
        avisar("Baixando o vídeo…")

    avisar("Localizando o vídeo…")
    dramatip = dominio(link) == "dramatip.net" or dominio(link).endswith(".dramatip.net")
    caminho = None
    if not dramatip:
        caminho = tentar_ytdlp(link, pasta, silencioso=True, ao_progresso=progresso,
                              cancelamento=cancelamento)
    if not caminho:
        avisar("Analisando a página…")
        with sessao_video(link, headless=True, navegador=NAVEGADOR, silencioso=True,
                          cancelamento=cancelamento) as candidatos:
            progresso()
            caminho = baixar_midia(candidatos[0], link, pasta, silencioso=True,
                                   ao_progresso=progresso, cancelamento=cancelamento)
    return Path(caminho).resolve()
