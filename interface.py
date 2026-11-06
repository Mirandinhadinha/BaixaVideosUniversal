"""Tk no thread principal; rede e navegador em uma tarefa separada."""
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from comunicacao import DownloadCancelado
from config import PASTA_DOWNLOAD
from detector import mensagem_segura, validar_url


def executar_download(link, **opcoes):
    from servico import baixar_video
    return baixar_video(link, **opcoes)


def descrever_erro(erro):
    if isinstance(erro, ImportError):
        modulo = getattr(erro, "name", None) or "uma dependência"
        return (f"Falta instalar «{modulo}». Abra o programa por iniciar.bat "
                "ou rode: pip install -r requirements.txt")
    if isinstance(erro, PermissionError):
        return "Não foi possível salvar o vídeo. Verifique a permissão de gravação na pasta do programa."
    texto = mensagem_segura(erro).replace("\n", " ").strip()
    if "DRM" in texto.upper():
        return "Este vídeo usa proteção DRM e não pode ser baixado por este programa."
    if "CERTIFICATE_VERIFY_FAILED" in texto or "SSLError" in type(erro).__name__:
        return "Não foi possível estabelecer uma conexão segura com o site."
    texto = re.sub(r"^ERROR:\s*(?:\[[^\]]+\]\s*)?", "", texto)
    texto = texto.split("; please report this issue", 1)[0]
    return texto[:350] or "Não foi possível baixar o vídeo. Confira o link e tente novamente."


class Aplicativo:
    def __init__(self, janela, *, tarefa=executar_download, pasta=PASTA_DOWNLOAD):
        self.janela, self.tarefa = janela, tarefa
        self.pasta = Path(pasta).expanduser().resolve()
        self.eventos = queue.Queue()
        self.cancelamento = threading.Event()
        self.ocupado = self.encerrando = False
        self.trabalhador = None
        self.ultima_mensagem = None
        self._poll = None
        janela.title("Baixa Vídeo")
        janela.geometry("740x420")
        janela.minsize(620, 390)
        janela.configure(background="#f3f5f9")
        janela.protocol("WM_DELETE_WINDOW", self.fechar)
        janela.option_add("*Font", ("Segoe UI", 10))
        janela.columnconfigure(0, weight=1)
        janela.rowconfigure(0, weight=1)
        estilo = ttk.Style(janela)
        estilo.theme_use("clam")
        estilo.configure("Link.TEntry", padding=11, fieldbackground="white",
                         bordercolor="#cbd5e1", lightcolor="#cbd5e1", darkcolor="#cbd5e1")
        estilo.map("Link.TEntry", bordercolor=[("focus", "#2563eb")])
        estilo.configure("Seguir.TButton", font=("Segoe UI", 10, "bold"), padding=(18, 11),
                         background="#2563eb", foreground="white", borderwidth=0)
        estilo.map("Seguir.TButton", background=[("disabled", "#cbd5e1"), ("active", "#1d4ed8")],
                   foreground=[("disabled", "#64748b")])
        estilo.configure("Progresso.Horizontal.TProgressbar", background="#2563eb",
                         troughcolor="#e8edf5", borderwidth=0, thickness=4)
        estilo.configure("Pasta.TButton", background="#f3f5f9", foreground="#2563eb",
                         borderwidth=0, padding=(0, 5))
        painel = tk.Frame(janela, background="white", padx=26, pady=22)
        painel.grid(row=0, column=0, sticky="nsew", padx=18, pady=(18, 10))
        painel.columnconfigure(0, weight=1)
        painel.rowconfigure(5, weight=1)
        tk.Label(painel, text="Baixar vídeo", font=("Segoe UI", 22, "bold"),
                 foreground="#172033", background="white").grid(row=0, column=0, sticky="w")
        tk.Label(painel, text="Cole o link e deixe o resto com o programa.",
                 foreground="#64748b", background="white").grid(row=1, column=0, sticky="w", pady=(3, 20))
        formulario = tk.Frame(painel, background="white")
        formulario.grid(row=2, column=0, sticky="ew")
        formulario.columnconfigure(0, weight=1)
        tk.Label(formulario, text="LINK DO VÍDEO", font=("Segoe UI", 9, "bold"),
                 foreground="#64748b", background="white").grid(row=0, column=0, sticky="w", pady=(0, 7))
        self.link = tk.StringVar(janela)
        self.entrada = ttk.Entry(formulario, textvariable=self.link, style="Link.TEntry")
        self.entrada.grid(row=1, column=0, sticky="ew")
        self.entrada.bind("<Return>", self.iniciar)
        self.botao = ttk.Button(formulario, text="Seguir", command=self.iniciar,
                                style="Seguir.TButton", cursor="hand2", width=10)
        self.botao.grid(row=1, column=1, padx=(10, 0), sticky="ns")
        self.progresso = ttk.Progressbar(painel, mode="indeterminate",
                                        style="Progresso.Horizontal.TProgressbar")
        self.progresso.grid(row=3, column=0, sticky="ew", pady=(15, 10))
        self.status = tk.StringVar(janela, "Pronto para baixar.")
        self.rotulo_status = tk.Label(painel, textvariable=self.status, background="white",
                                      foreground="#475569", anchor="w", font=("Segoe UI", 10, "bold"))
        self.rotulo_status.grid(row=4, column=0, sticky="ew", pady=(0, 9))
        self.log = tk.Text(painel, height=4, wrap="word", state="disabled", relief="flat",
                           background="#f6f8fc", foreground="#475569", padx=12, pady=9,
                           font=("Segoe UI", 9), highlightthickness=1, highlightbackground="#e6ebf2")
        self.log.grid(row=5, column=0, sticky="nsew")
        self.log.tag_configure("erro", foreground="#b91c1c")
        self.log.tag_configure("sucesso", foreground="#15803d")
        self._registrar("Aguardando um link.")
        rodape = tk.Frame(janela, background="#f3f5f9")
        rodape.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 12))
        rodape.columnconfigure(0, weight=1)
        self.destino = tk.StringVar(janela, "Vídeos salvos na pasta videos_baixados.")
        tk.Label(rodape, textvariable=self.destino, background="#f3f5f9", foreground="#64748b",
                 font=("Segoe UI", 9), anchor="w", justify="left", wraplength=530).grid(row=0, column=0, sticky="w")
        self.abrir = ttk.Button(rodape, text="Abrir pasta", command=self.abrir_pasta,
                                style="Pasta.TButton", cursor="hand2")
        self.abrir.grid(row=0, column=1, padx=(10, 0), sticky="e")
        if not self.pasta.is_dir():
            self.abrir.state(["disabled"])
        self.entrada.focus_set()
        self._agendar()

    def _registrar(self, mensagem, tag=""):
        if mensagem == self.ultima_mensagem:
            return
        self.ultima_mensagem = mensagem
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M}  {mensagem}\n", tag)
        if int(self.log.index("end-1c").split(".")[0]) > 30:
            self.log.delete("1.0", "2.0")
        self.log.configure(state="disabled")
        self.log.see("end")

    def _mostrar_status(self, mensagem, cor="#475569"):
        self.status.set(mensagem)
        self.rotulo_status.configure(foreground=cor)

    def iniciar(self, _evento=None):
        if self.ocupado or self.encerrando:
            return "break"
        try:
            link = validar_url(self.link.get())
        except ValueError as erro:
            self._mostrar_status("Confira o link informado.", "#b91c1c")
            self._registrar(descrever_erro(erro), "erro")
            self.entrada.focus_set()
            return "break"
        self.ocupado = True
        self.cancelamento.clear()
        self.entrada.state(["disabled"])
        self.botao.configure(text="Aguarde…")
        self.botao.state(["disabled"])
        self.progresso.start(12)
        self._mostrar_status("Localizando o vídeo…")
        self._registrar("Localizando o vídeo…")
        self.trabalhador = threading.Thread(target=self._executar, args=(link,), daemon=False)
        self.trabalhador.start()
        return "break"

    def _executar(self, link):
        try:
            caminho = self.tarefa(link, pasta=self.pasta, cancelamento=self.cancelamento,
                                 informar=lambda mensagem: self.eventos.put(("status", mensagem)))
            self.eventos.put(("concluido", Path(caminho)))
        except Exception as erro:
            if isinstance(erro, DownloadCancelado) or self.cancelamento.is_set():
                self.eventos.put(("cancelado", None))
            else:
                self.eventos.put(("erro", descrever_erro(erro)))

    def _agendar(self):
        self._poll = self.janela.after(80, self._ler_eventos)

    def _ler_eventos(self):
        self._poll = None
        while True:
            try:
                tipo, valor = self.eventos.get_nowait()
            except queue.Empty:
                break
            if tipo == "status":
                if not self.encerrando:
                    self._mostrar_status(valor)
                    self._registrar(valor)
                continue
            self.ocupado = False
            self.progresso.stop()
            self.progresso.configure(value=0)
            if self.encerrando:
                self._destruir()
                return
            self.botao.configure(text="Seguir")
            self.botao.state(["!disabled"])
            self.entrada.state(["!disabled"])
            if tipo == "concluido":
                self.pasta = valor.parent
                self._mostrar_status("Download concluído!", "#15803d")
                self._registrar(f"Download concluído: {valor.name}", "sucesso")
                self.destino.set(f"Salvo em: {valor.parent}")
                self.abrir.state(["!disabled"])
            elif tipo == "erro":
                self._mostrar_status("Não foi possível concluir.", "#b91c1c")
                self._registrar(valor, "erro")
            else:
                self._mostrar_status("Download cancelado.")
            self.entrada.focus_set()
        self._agendar()

    def abrir_pasta(self):
        try:
            if sys.platform == "win32":
                os.startfile(str(self.pasta))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.pasta)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self._registrar(f"Abra esta pasta pelo explorador de arquivos: {self.pasta}", "erro")

    def fechar(self):
        if self.ocupado:
            self.encerrando = True
            self.cancelamento.set()
            self._mostrar_status("Encerrando o download…")
            self._registrar("Encerrando o download…")
        else:
            self._destruir()

    def _destruir(self):
        if self._poll is not None:
            self.janela.after_cancel(self._poll)
            self._poll = None
        self.janela.destroy()
