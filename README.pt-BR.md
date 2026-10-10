<h1 align="center">🎵 Turing Vinyl - With Spotify Integration</h1>

<p align="center"><a href="README.pt-BR.md">Português</a> · <a href="README.md">English</a></p>

<p align="center">
  Uma experiência musical animada para a Turing Smart Screen: capas, playlists e LEDs sincronizados com o que está tocando.
</p>

<p align="center">
  <a href="https://github.com/pvict/TuringScreen-SpotifyVinyl/stargazers"><img src="https://img.shields.io/github/stars/pvict/TuringScreen-SpotifyVinyl?color=7956D8&style=for-the-badge" alt="Estrelas no GitHub"></a>
  <a href="https://github.com/pvict/TuringScreen-SpotifyVinyl/network/members"><img src="https://img.shields.io/github/forks/pvict/TuringScreen-SpotifyVinyl?color=7956D8&style=for-the-badge" alt="Forks no GitHub"></a>
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

### Projeto relacionado e compatibilidade

O [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python) é um projeto popular e mais amplo de monitoramento do sistema e uma biblioteca Python para várias telas USB-C pequenas, incluindo modelos Turing Smart Screen. O Turing Vinyl tem outro foco: uma experiência musical para a tela. A compatibilidade é validada separadamente; o suporte de um projeto não garante suporte no outro.

Este repositório usa a biblioteca separada [Turing Smart Screen CLI](https://github.com/phstudy/turing-smart-screen-cli), de Study Hsueh, para a comunicação com a tela. A licença MIT dela está incluída em `turing-smart-screen-cli-main/LICENSE`. O Turing Vinyl é um projeto comunitário não oficial e não tem vínculo com Turing, XuanFang ou os fabricantes.

## :camera: Projeto em funcionamento

<p align="center">
  <img src="docs/images/turing-vinyl-setup.png" alt="Turing Vinyl exibindo a capa de True Colors no display, com os LEDs do computador sincronizados" width="720">
</p>

<p align="center"><em>A tela exibe a faixa atual enquanto a iluminação do computador acompanha a experiência.</em></p>

<p align="center"><strong>▶ Demonstração completa do projeto (24 segundos)</strong></p>

https://github.com/user-attachments/assets/f876053e-42a8-4e0f-b7f9-6a048da14437

### O que acontece no vídeo

1. Ao pausar a música, o vinil desacelera.
2. O vídeo de fundo muda com uma transição RGB suave.
3. Ao retomar a reprodução, o vinil acelera novamente.
4. Uma animação apresenta a playlist à qual a música pertence.

## :star2: Recursos

- Interface gráfica com seletor animado de modos, brilho manual/automático e botão para apagar a tela.
- Animações transmitidas à tela com taxa alvo de **60 quadros por segundo**.
- Capa do álbum, informações da faixa e arco de progresso da música.
- Transições animadas entre músicas e capas.
- O vinil desacelera ao pausar e acelera quando a reprodução recomeça.
- Uma transição RGB suave acompanha a troca para o vídeo de fundo da pausa.
- Nome e capa da playlist quando ela muda e em exibições periódicas durante a reprodução.
- Uma animação apresenta a playlist que está tocando.
- Fundos em vídeo para os estados de reprodução e pausa/ociosidade.
- Brilho da tela ajustado conforme a programação configurada no script.
- LEDs sincronizados com a cor da capa do álbum; em ociosidade, é usado o perfil escolhido no OpenRGB.
- Registro de execução em `tela.log`.

## :hammer_and_wrench: Como funciona

| Arquivo | Responsabilidade |
| --- | --- |
| `tela_completa.py` | Coordena a tela, a mídia do Windows, o volume, o Spotify e o OpenRGB. É o ponto de entrada. |
| `interface.py` / `iniciar_interface.pyw` | Janela de controle; abra o `.pyw` com dois cliques ou execute `python interface.py`. |
| `controle_interface.py` | Guarda as preferências locais e conecta a janela ao processo da tela. |
| `fundo_usuario.py` | Prepara o vídeo ocioso escolhido no app e troca o decoder durante a exibição. |
| `ao_vivo.py` | Compõe os elementos visuais e codifica o fluxo H.264 em tempo real com FFmpeg. |
| `animacao_capa.py` | Controla as transições das capas. |
| `spotify_playlist.py` | Consulta o contexto de reprodução e os dados da playlist pela API do Spotify. |
| `leds_openrgb.py` | Controla as zonas RGB selecionadas e aplica as cores do álbum ou o perfil de ociosidade. |

O Spotify é consultado a cada cinco segundos. Quando o contexto muda, o nome e a arte da playlist são atualizados; a tela mostra a mensagem e a capa por cinco segundos, e repete a exibição periodicamente. Os LEDs continuam seguindo a cor da capa do álbum.

A capa do álbum normalmente vem da mídia do Windows. O script recebe avisos de atualização da imagem na mesma faixa e faz algumas releituras após a troca de música, permitindo substituir uma miniatura temporária sem avançar a faixa. Com a API do Spotify configurada, a consulta de reprodução já existente também confirma a imagem do álbum: título, artista e os dados de álbum disponíveis precisam coincidir antes de aceitar o resultado. As imagens são baixadas uma vez por solicitação e reutilizadas em memória; imagens visualmente equivalentes mantêm a capa e as cores RGB existentes. Atualizações e recuperações aparecem em `tela.log`. Isso não grava vídeos nem salva capas em disco.

### Interface gráfica

Encerre qualquer execução anterior de `tela_completa.py` com **Ctrl+C**. Abra `iniciar_interface.pyw` com dois cliques ou execute:

```powershell
python interface.py
```

Escolha um modo e clique em **Iniciar exibição**. A janela fica disponível para ajustar os controles durante a reprodução.

| Modo | Com música | Durante a pausa |
| --- | --- | --- |
| Dinâmico | Vinil, capa, informações e animações do Spotify. | O disco e a capa desaceleram suavemente como no modo Só Spotify antes da transição para o vídeo de fundo e perfil RGB ocioso. Ao retomar, a capa usa o mesmo giro da troca de música. |
| Só Spotify | A mesma experiência com o vinil. | O vinil desacelera; o glow e o preenchimento do arco desaparecem, e a capa encolhe suavemente até 70%. Ao retomar, a capa volta; o glow atinge a intensidade máxima quando ela chega ao tamanho completo, enquanto o arco cresce de zero até o progresso atual. Os LEDs mantêm a cor do álbum. |
| Só vídeo | Apenas o vídeo de fundo. | Vídeo de fundo e perfil RGB ocioso. |

- O slider mostra o valor durante o arraste e aplica o brilho ao soltar, evitando uma sequência de atualizações na controladora RGB.
- **Automático** mantém a programação de brilho por horário. Arrastar o slider ativa o ajuste manual.
- **Desligar tela** apaga o backlight e mostra um quadro preto; os LEDs continuam seguindo o modo selecionado. O USB permanece conectado.
- As preferências ficam em `%LOCALAPPDATA%\TuringScreen\interface.json`.
- Fechar a janela encerra a execução que ela iniciou. A janela impede uma segunda execução simultânea.
- A base da interface usa grafite e prata suave. As cores dos controles, dos ícones e do glow acompanham a capa do álbum, com transição suave. Botões, modos e slider têm resposta animada ao mouse.
- O ícone do aplicativo é um toca-discos prateado com centro azul acinzentado, disponível em PNG e ICO em `assets/icons` e usado na janela e no cabeçalho.
- O vinil externo e a capinha da interface acompanham o movimento da tela, com alvo de 60 FPS e agendamento pelo relógio. A capinha também acompanha o encolhimento durante a pausa.
- **Aparência** permite ligar/desligar o acrílico do Windows 11 e ajustar a opacidade do fundo. Menor opacidade revela mais das janelas atrás do app. Textos e controles permanecem opacos. O Windows compõe o blur; esse ajuste não envia comandos à tela ou aos LEDs. Se o acrílico ajustável estiver indisponível, o painel informa o material controlado pelo Windows ou usa o fundo sólido.
- **Vídeo de fundo → Escolher vídeo** permite importar o fundo ocioso. O app faz a preparação em segundo plano, mostra o progresso e permite cancelar. **Restaurar padrão** volta ao `video_fundo.mp4` do projeto.
- A interface usa Tkinter e Pillow, sem navegador embutido. As máscaras do glow são preparadas na abertura; a capa parada é reutilizada. A sincronização usa números enviados pelo próprio script, sem consultas extras ao Spotify.
- As fontes **Fraunces** e **DM Sans**, do Google Fonts, estão incluídas em `assets/fonts`, com suas licenças SIL Open Font License. A janela funciona sem baixar fontes na inicialização.

A execução direta com `python tela_completa.py` continua disponível, com o comportamento dinâmico e o brilho por horário originais.

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

Pela interface, use **Vídeo de fundo → Escolher vídeo**. A importação aplica a preparação do conversor de fundo: 480 × 480, recorte central, 30 FPS, H.264 sem áudio e os ajustes de saturação/contraste existentes. Requer FFmpeg e FFprobe no `PATH`, com o codificador `libx264`.

A conversão usa prioridade reduzida no Windows, duas threads de codificação e uma thread de filtros. Os arquivos prontos ficam em `%LOCALAPPDATA%\TuringScreen\fundos`; o vídeo de origem, o fundo padrão e as importações anteriores são preservados. A preferência fica salva para a próxima abertura.

Durante a exibição, o script abre o novo fundo em uma thread separada e confirma que consegue ler o primeiro quadro antes de trocar o decoder ocioso. O fundo novo aparece na próxima leitura do vídeo ocioso, sem reiniciar o script. Em **Só Spotify**, o vinil continua sendo o fundo desse modo. Se a importação falhar ou for cancelada, o fundo anterior permanece selecionado.

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
| Aparece o logo do Spotify no lugar da capa | O Windows pode ter fornecido uma miniatura temporária do player. Os avisos de atualização e a API configurada recuperam a capa automaticamente; procure `capa: imagem atualizada pelo Windows` ou `capa: recuperada pela API do Spotify` em `tela.log`. A recuperação pela API depende de conexão e metadados coincidentes. |
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
