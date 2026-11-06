# Baixa Vídeo — interface 3.0

Programa desktop em Python com um campo para o link, botão **Seguir**, indicador
simples de atividade e mensagens curtas. O navegador trabalha oculto e sem som.
A faixa de áudio do vídeo baixado é preservada.

## Instalação rápida

No Windows, basta dar dois cliques em **iniciar.bat**: na primeira vez ele cria o
ambiente, instala as dependências e abre o programa. Manualmente:

```text
git clone https://github.com/Wilker94/baixa_video_universal.git
cd baixa_video_universal
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

(Ajuste a URL do `git clone` para o nome real do seu repositório.)

## Iniciar

Abra a pasta na sua IDE e execute **main.py**. No Windows, **main.pyw** também
abre a interface sem criar uma janela de terminal.

1. Cole o endereço no campo **Link do vídeo**.
2. Clique em **Seguir** ou pressione Enter.
3. Aguarde a mensagem **Download concluído!**.
4. Use **Abrir pasta** para acessar o arquivo.

O botão fica desabilitado durante o download para impedir cliques duplicados.
A interface continua respondendo enquanto o vídeo é localizado e baixado.
Erros aparecem na própria janela, permitindo corrigir o link e tentar novamente.
Ao fechar durante um download, o programa solicita o cancelamento e aguarda a
liberação das conexões antes de encerrar. Isso pode levar alguns segundos.

Os vídeos são salvos em `videos_baixados`, ao lado do código-fonte ou do
executável. A pasta precisa permitir gravação. Arquivos existentes não são
sobrescritos.

## Preparar o ambiente de desenvolvimento

Use Python 3.10 ou superior, com Tkinter. As distribuições oficiais do Python
para Windows incluem suporte a Tcl/Tk quando esse componente é selecionado na
instalação. No Linux, Tkinter pode exigir o pacote `python3-tk` da distribuição.

No terminal do mesmo ambiente Python selecionado na IDE:

```text
python -m pip install -r requirements.txt
python main.py
```

O programa tenta usar Chrome, Edge e depois o Chromium do Playwright. Se
Chrome e Edge não estiverem instalados, prepare o Chromium com:

```text
python -m playwright install chromium
```

O `iniciar.bat` é o único script de instalação; a interface em si nunca instala nada.

## Gerar executável no Windows

O arquivo **BaixaVideo.spec** prepara o empacotamento com PyInstaller, inclui
os recursos das bibliotecas e configura o aplicativo sem janela de console.
Execute no Windows, pelo terminal do seu ambiente Python:

```text
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm BaixaVideo.spec
```

O resultado fica em **dist/BaixaVideo.exe**. O FFmpeg fornecido por
`imageio-ffmpeg` é incluído. Os testes e os vídeos antigos não são incorporados
no executável. O código usa a pasta do `.exe` como destino, evitando salvar
arquivos na pasta temporária de extração do empacotador.

Por padrão, o executável usa Chrome/Edge do computador ou um Chromium já
instalado pelo Playwright. Para incluir também o Chromium no executável,
antes de gerar o pacote execute no PowerShell:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH="0"
python -m playwright install chromium
python -m PyInstaller --clean --noconfirm BaixaVideo.spec
```

Essa alternativa gera um executável maior. O navegador incluído também opera
oculto e sem reprodução de som. Gere e valide o `.exe` no Windows antes de
usá-lo em outro computador; este pacote entrega o código e a configuração de
empacotamento, sem incluir um executável Windows previamente compilado.

## Arquivos do projeto

| Arquivo | Função |
| --- | --- |
| `main.py` / `main.pyw` | Iniciam a janela |
| `interface.py` | Campo do link, botão, mensagens e comunicação com o trabalhador |
| `servico.py` | Coordena as tentativas de localizar e baixar o vídeo |
| `navegador.py` | Detecta o player no navegador oculto, incluindo iframes |
| `downloader.py` | Baixa MP4/WebM, HLS e DASH, preservando áudio |
| `detector.py` | Classifica e seleciona as fontes |
| `comunicacao.py` | Saída silenciosa e sinalização de cancelamento |
| `config.py` | Caminhos e parâmetros do navegador |
| `BaixaVideo.spec` | Configuração para gerar o executável sem console |

As bibliotecas do navegador são criadas no próprio trabalhador. Os controles
da janela só são atualizados no thread principal por uma fila de eventos.
Os detalhes de tráfego, cookies e links assinados não aparecem no log da interface.

## Testes

```text
python -m unittest discover -s tests -v
```

Os testes de download usam um servidor local e vídeos sintéticos temporários.
Os testes da interface exigem uma sessão gráfica. No Linux, podem ser executados
com um servidor X virtual. Casos que dependem de componentes indisponíveis são
marcados como ignorados pelo unittest.

## Compatibilidade

A captura do episódio 12 do Dramatip foi verificada nas versões anteriores.
Esta versão mantém esse mecanismo e adiciona a interface. A disponibilidade
de um vídeo depende do site e do acesso ao conteúdo. Fontes com proteção DRM
não são compatíveis.

Referências para desenvolvimento e empacotamento:

- https://docs.python.org/3/library/tkinter.html
- https://playwright.dev/python/docs/library#pyinstaller
- https://pyinstaller.org/en/stable/spec-files.html

## Aviso legal

Use a ferramenta apenas para baixar conteúdo que você tem direito de salvar
(próprio, de domínio público ou com permissão). Respeite os termos de uso dos
sites e as leis de direitos autorais do seu país. O autor não se responsabiliza
pelo uso indevido. Projeto sob licença MIT (arquivo `LICENSE`).
