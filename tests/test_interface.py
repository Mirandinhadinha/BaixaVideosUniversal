"""Verifica interação real com Tk, fila de eventos e encerramento do trabalhador."""
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comunicacao import DownloadCancelado
from interface import Aplicativo


class InterfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            janela = tk.Tk()
            janela.destroy()
        except tk.TclError:
            raise unittest.SkipTest("Ambiente sem tela para os testes da interface")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.janela = tk.Tk()
        self.app = Aplicativo(self.janela, pasta=self.tmp.name)
        self.janela.update()

    def tearDown(self):
        self.app.cancelamento.set()
        if self.app.trabalhador:
            self.app.trabalhador.join(timeout=3)
        try:
            if self.janela.winfo_exists():
                self.app._destruir()
        except tk.TclError:
            pass
        self.tmp.cleanup()

    def esperar(self, condicao, limite=4):
        fim = time.monotonic() + limite
        while not condicao() and time.monotonic() < fim:
            self.janela.update()
            time.sleep(0.01)
        self.assertTrue(condicao())

    def test_link_invalido_nao_inicia_trabalho(self):
        self.app.link.set("isto não é um link")
        self.app.botao.invoke()
        self.assertFalse(self.app.ocupado)
        self.assertIsNone(self.app.trabalhador)
        self.assertEqual(self.app.status.get(), "Confira o link informado.")

    def test_janela_responde_e_impede_download_duplicado(self):
        iniciou, liberar = threading.Event(), threading.Event()
        chamadas = []

        def tarefa(link, *, informar, pasta, cancelamento):
            chamadas.append(link)
            iniciou.set()
            informar("Baixando o vídeo…")
            liberar.wait(timeout=2)
            arquivo = pasta / "episodio.mp4"
            arquivo.write_bytes(b"arquivo de teste")
            return arquivo

        self.app.tarefa = tarefa
        self.app.link.set("https://site.test/episodio")
        self.app.botao.invoke()
        self.assertTrue(iniciou.wait(timeout=1))
        self.app.iniciar()
        respondeu = []
        self.janela.after(10, lambda: respondeu.append(True))
        self.esperar(lambda: bool(respondeu))
        self.assertTrue(self.app.ocupado)
        self.assertTrue(self.app.botao.instate(["disabled"]))
        liberar.set()
        self.esperar(lambda: not self.app.ocupado)
        self.assertEqual(len(chamadas), 1)
        self.assertEqual(self.app.status.get(), "Download concluído!")
        self.assertTrue(self.app.botao.instate(["!disabled"]))
        self.assertIn("episodio.mp4", self.app.log.get("1.0", "end"))

    def test_erro_na_janela_permite_tentar_novamente(self):
        def tarefa(*args, **kwargs):
            raise RuntimeError("Vídeo indisponível.")

        self.app.tarefa = tarefa
        self.app.link.set("https://site.test/erro")
        self.app.botao.invoke()
        self.esperar(lambda: not self.app.ocupado)
        self.assertEqual(self.app.status.get(), "Não foi possível concluir.")
        self.assertIn("Vídeo indisponível.", self.app.log.get("1.0", "end"))
        self.assertTrue(self.app.entrada.instate(["!disabled"]))

    def test_fechar_cancela_o_trabalho_antes_de_destruir_a_janela(self):
        terminou = threading.Event()

        def tarefa(*args, cancelamento, **kwargs):
            cancelamento.wait(timeout=2)
            terminou.set()
            raise DownloadCancelado()

        self.app.tarefa = tarefa
        self.app.link.set("https://site.test/lento")
        self.app.botao.invoke()
        self.app.fechar()
        self.assertTrue(self.app.encerrando)
        self.assertTrue(self.app.cancelamento.is_set())
        self.assertTrue(terminou.wait(timeout=1))
        self.app.trabalhador.join(timeout=1)
        fechou = False
        fim = time.monotonic() + 3
        while not fechou and time.monotonic() < fim:
            try:
                self.janela.update()
                fechou = not self.janela.winfo_exists()
            except tk.TclError:
                fechou = True
            time.sleep(0.01)
        self.assertTrue(fechou)
