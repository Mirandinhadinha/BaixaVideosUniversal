"""Configurações editáveis. Os caminhos independem da pasta do terminal."""
from pathlib import Path
import sys

# Um executável pode extrair seus recursos em uma pasta temporária.
# Os downloads continuam ao lado do .exe, fora dessa pasta temporária.
PASTA_PROGRAMA = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                  else Path(__file__).resolve().parent)
PASTA_DOWNLOAD = PASTA_PROGRAMA / "videos_baixados"
# Executa sem abrir uma janela. O navegador também é iniciado sem saída de áudio.
HEADLESS = True
TIMEOUT_PAGINA = 60_000
TEMPO_DETECCAO = 35
TEMPO_DETECCAO_MANUAL = 90
TEMPO_ESTABILIZACAO = 4
TIMEOUT_REDE = (15, 60)

# "auto" tenta Chrome, Edge e depois o Chromium do Playwright.
NAVEGADOR = "auto"

# None usa a identificação real do navegador instalado.
USER_AGENT = None
