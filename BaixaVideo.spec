# Gere no próprio Windows: python -m PyInstaller --clean --noconfirm BaixaVideo.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

dados, binarios, imports = [], [], []
for pacote in ("playwright", "yt_dlp", "imageio_ffmpeg", "certifi"):
    d, b, h = collect_all(pacote, include_py_files=False)
    dados.extend(d)
    binarios.extend(b)
    imports.extend(h)

raiz = Path(SPECPATH)
analise = Analysis(
    [str(raiz / "main.pyw")], pathex=[str(raiz)], binaries=binarios,
    datas=dados, hiddenimports=imports, hookspath=[], runtime_hooks=[],
    excludes=["pytest"], noarchive=False,
)
codigo = PYZ(analise.pure)
executavel = EXE(
    codigo, analise.scripts, analise.binaries, analise.datas,
    name="BaixaVideo", debug=False, strip=False, upx=False, console=False,
)
