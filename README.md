<h1 align="center">🎵 Turing Vinyl</h1>

<p align="center">
  Uma experiência musical animada para a Turing Smart Screen: capas, playlists e LEDs sincronizados com o que está tocando.
</p>

<p align="center">
  <a href="https://github.com/pvict/TuringScreen-Remix/stargazers"><img src="https://img.shields.io/github/stars/pvict/TuringScreen-Remix?color=7956D8&style=for-the-badge" alt="Estrelas no GitHub"></a>
  <a href="https://github.com/pvict/TuringScreen-Remix/network/members"><img src="https://img.shields.io/github/forks/pvict/TuringScreen-Remix?color=7956D8&style=for-the-badge" alt="Forks no GitHub"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-7956D8?logo=python&logoColor=white&style=for-the-badge" alt="Python 3.9 ou superior">
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-7956D8?logo=windows&logoColor=white&style=for-the-badge" alt="Windows 10 ou 11">
  <img src="https://img.shields.io/badge/Animação-60%20FPS-7956D8?style=for-the-badge" alt="Animação a 60 FPS">
  <img src="https://img.shields.io/badge/Licença-não%20declarada-lightgrey?style=for-the-badge" alt="Licença não declarada">
</p>

<p align="center">
  <a href="#-recursos">Recursos</a> ·
  <a href="#-instalação">Instalação</a> ·
  <a href="#-configuração">Configuração</a> ·
  <a href="#-execução">Execução</a> ·
  <a href="#-ajuda">Ajuda</a>
</p>

---

## :sparkles: Sobre

O **Turing Vinyl** transforma uma tela USB Turing Smart Screen em um display animado para música. Ele acompanha a reprodução do Windows, mostra a capa do álbum e o progresso da faixa, destaca a playlist atual e combina a experiência com os LEDs controlados pelo OpenRGB.

> Projeto pessoal em evolução, feito para uma configuração específica de hardware. A comunicação USB, o OpenRGB e a integração com o Spotify dependem do equipamento, dos drivers e das versões instaladas.

## :camera: Projeto em funcionamento

<p align="center">
  <img src="docs/images/turing-vinyl-setup.png" alt="Turing Vinyl exibindo a capa de True Colors no display, com os LEDs do computador sincronizados" width="720">
</p>

<p align="center"><em>A tela exibe a faixa atual enquanto a iluminação do computador acompanha a experiência.</em></p>

## :star2: Recursos

- Animações transmitidas à tela com taxa alvo de **60 quadros por segundo**.
- Capa do álbum, informações da faixa e arco de progresso da música.
- Transições animadas entre músicas e capas.
- Nome e capa da playlist quando ela muda e em exibições periódicas durante a reprodução.
- Fundos em vídeo para os estados de reprodução e pausa/ociosidade.
- Brilho da tela ajustado conforme a programação configurada no script.
- LEDs sincronizados com a cor da capa do álbum; em ociosidade, é usado o perfil escolhido no OpenRGB.
- Registro de execução em `tela.log`.

## :hammer_and_wrench: Como funciona

| Arquivo | Responsabilidade |
| --- | --- |
| `tela_completa.py` | Coordena a tela, a mídia do Windows, o volume, o Spotify e o OpenRGB. É o ponto de entrada. |
| `ao_vivo.py` | Compõe os elementos visuais e codifica o fluxo H.264 em tempo real com FFmpeg. |
| `animacao_capa.py` | Controla as transições das capas. |
| `spotify_playlist.py` | Consulta o contexto de reprodução e os dados da playlist pela API do Spotify. |
| `leds_openrgb.py` | Controla as zonas RGB selecionadas e aplica as cores do álbum ou o perfil de ociosidade. |

O Spotify é consultado a cada cinco segundos. Quando o contexto muda, o nome e a arte da playlist são atualizados; a tela mostra a mensagem e a capa por alguns segundos, e repete a exibição periodicamente. Os LEDs continuam seguindo a cor da capa do álbum.

## :computer: Requisitos

- Windows 10 ou 11.
- Tela Turing Smart Screen USB compatível com o protocolo usado pelo projeto.
- Python 3.9 ou superior (Python 3.11 é uma opção adequada para começar).
- FFmpeg no `PATH`, com suporte ao codificador NVIDIA `h264_nvenc`.
- GPU NVIDIA compatível com NVENC e driver instalado.
- OpenRGB instalado e configurado para controlar a placa/controladora.
- Conta Spotify e aplicativo criado no [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
- Os vídeos de fundo esperados pelo script.

> **Sobre os 60 FPS:** esse é o alvo do fluxo, não uma garantia de que todo quadro chegará à tela. O resultado depende do computador, do FFmpeg/NVENC, da conexão USB e do próprio display.

## :electric_plug: Instalação

Abra o PowerShell na pasta do projeto e crie um ambiente virtual:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Instale as dependências do aplicativo:

```powershell
python -m pip install Pillow pyusb pycryptodome libusb-package openrgb-python pycaw comtypes winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Media.Control winrt-Windows.Storage.Streams
python -m pip install -e .\turing-smart-screen-cli-main
```

Instale o FFmpeg separadamente e confirme que o PowerShell consegue encontrá-lo:

```powershell
ffmpeg -encoders | Select-String h264_nvenc
```

Se `h264_nvenc` não aparecer, instale uma compilação do FFmpeg com suporte a NVENC e confira o driver da GPU.

## :gear: Configuração

### Spotify

1. Crie um aplicativo no [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Nas configurações do aplicativo, adicione este endereço de retorno:

   ```text
   http://127.0.0.1:8765/callback
   ```

3. Crie `spotify_client_id.txt` na pasta do projeto e coloque somente o **Client ID**, em uma linha.
4. Na primeira execução, autorize o aplicativo na janela do navegador que será aberta.

O projeto usa OAuth PKCE, portanto não precisa do Client Secret. O token de autorização é armazenado localmente, protegido pelo Windows, em `%LOCALAPPDATA%\TuringScreen\spotify_token.bin`.

### OpenRGB

1. Abra o OpenRGB e ative o servidor SDK em `127.0.0.1:6742`.
2. Se necessário para detectar ou controlar a placa, execute o OpenRGB como administrador.
3. Salve no OpenRGB o perfil de ociosidade chamado `purple rain`.
4. Em `leds_openrgb.py`, confira as configurações `DISPOSITIVOS` e `ZONAS`. O padrão procura o dispositivo ASRock e as zonas Addressable Header, PCH e IO Cover.

O projeto guarda uma cópia das cores do perfil em `perfil_ocioso.json`. Para recarregar o perfil, apague esse arquivo antes de executar ou ajuste `RELER_PERFIL` em `leds_openrgb.py`. Não deixe outro programa controlar simultaneamente as mesmas zonas RGB.

### Vídeos

Mantenha estes arquivos ao lado de `tela_completa.py`:

| Arquivo | Quando aparece |
| --- | --- |
| `video_tela.mp4` | Durante a reprodução |
| `video_fundo.mp4` | Em pausa ou ociosidade |

Os conversores produzem vídeos quadrados de 480 × 480 a 30 FPS:

- `converter.bat` gera `video_tela.mp4` a partir de `entrada.mp4`. Na versão atual, o nome de origem está fixado no próprio arquivo; coloque o vídeo com esse nome na pasta do projeto.
- `converter_fundo.bat` pede o caminho do vídeo de origem e gera `video_fundo.mp4`.

Os arquivos `.h264` presentes no repositório são mantidos junto dos recursos de vídeo. Não os remova nem os ignore sem confirmar que o fluxo atual não depende deles.

## :rocket: Execução

Com a tela conectada, o ambiente virtual ativado e o servidor SDK do OpenRGB disponível, execute:

```powershell
python tela_completa.py
```

Para encerrar, pressione `Ctrl+C` no terminal. O arquivo `tela.log` registra informações de inicialização, conexões e mensagens do FFmpeg.

## :art: Personalização

As principais opções ficam no início dos scripts:

- `tela_completa.py`: FPS alvo, dimensões, arquivos de vídeo, brilho e elementos da tela.
- `leds_openrgb.py`: dispositivo, zonas RGB, perfil de ociosidade, saturação e intervalo entre gravações.
- `animacao_capa.py`: estilo e duração das transições.
- `spotify_playlist.py`: intervalo de consulta e integração OAuth.

Faça uma cópia antes de alterar os parâmetros de desempenho ou de gravação RGB. A controladora pode reagir de forma diferente conforme a placa, o firmware e o número de zonas.

## :ambulance: Ajuda

| Problema | O que conferir |
| --- | --- |
| A tela não é encontrada | Cabo USB, alimentação, driver, compatibilidade do modelo e possíveis programas usando o dispositivo. |
| FFmpeg não é encontrado | Instalação do FFmpeg e pasta `bin` no `PATH`; abra um novo terminal depois de alterar o PATH. |
| `h264_nvenc` não aparece | Compilação do FFmpeg com NVENC e driver NVIDIA. |
| A playlist não aparece | Client ID, Redirect URI, conexão com a internet e autorização no navegador. Para autorizar de novo, remova o token local. |
| OpenRGB não conecta | Servidor SDK ativo em `127.0.0.1:6742` e ausência de outro processo controlando as mesmas zonas. |
| LEDs mostram cores incorretas | Pare o script, feche outros controladores RGB, restaure o perfil no OpenRGB e confira as mensagens em `tela.log`. |
| O vídeo engasga | Confira uso de GPU/CPU, suporte NVENC e outros programas que codifiquem vídeo; a conexão USB e a tela também influenciam. |

## :lock: Privacidade e credenciais

- Não publique `spotify_client_id.txt`, tokens, logs ou dados pessoais.
- Não adicione Client Secret ao código: a integração usa PKCE e não precisa dele.
- A integração consulta a API do Spotify para obter informações da reprodução e da playlist.
- O OpenRGB é acessado localmente pelo servidor SDK em `127.0.0.1`.

## :handshake: Contribuições

Sugestões e relatos de problemas são bem-vindos. Ao abrir uma issue, inclua os passos para reproduzir o problema e, quando ajudar, um trecho relevante do `tela.log` — removendo antes qualquer dado pessoal ou credencial.

Para propor uma alteração:

1. Faça um fork do projeto e crie uma branch para sua mudança.
2. Faça commits pequenos e descritivos.
3. Abra um pull request explicando o que mudou e como foi verificado.

## :scroll: Licença e créditos

Este repositório ainda não declara uma licença para os scripts e mídias próprios do projeto. Até que uma licença seja adicionada, não presuma que eles podem ser reutilizados ou redistribuídos. O código em `turing-smart-screen-cli-main` tem licença própria; consulte `turing-smart-screen-cli-main/LICENSE`. Verifique também os direitos das fontes, vídeos, imagens e artes incluídos.

A integração com Spotify deve respeitar os [Termos da Plataforma Spotify](https://developer.spotify.com/terms) e as [Diretrizes de Design](https://developer.spotify.com/documentation/design). O uso de capas e a apresentação de conteúdo musical podem estar sujeitos a restrições; confira as regras vigentes antes de distribuir o projeto.

<p align="center">
  Feito com 💜 por <a href="https://github.com/pvict">Paulo</a>
</p>
