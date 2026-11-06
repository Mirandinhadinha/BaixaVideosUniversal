"""Ponto de entrada para execução no Python ou na IDE."""


def main():
    import tkinter as tk
    from interface import Aplicativo

    janela = tk.Tk()
    Aplicativo(janela)
    janela.mainloop()


if __name__ == "__main__":
    main()
