# Deck S21

O Deck S21 transforma um celular Android em um painel de botões para o MacBook.
Cada botão executa uma ação no Mac. Exemplos: tocar música, abrir um app, mutar o microfone.

O servidor roda no Mac. O celular só abre uma página web. Não existe app para instalar.

## Como funciona

1. O Mac roda o `server.py`.
2. O celular abre o endereço do Mac no Chrome.
3. Você toca em um botão.
4. O Mac executa a ação.

O celular e o Mac precisam estar na mesma rede Wi-Fi.

## Instalação no Mac

1. Abra o Terminal.
2. Confira se o Python 3 existe:
   ```bash
   python3 --version
   ```
   Se o Mac pedir para instalar as "ferramentas de linha de comando", aceite.
3. Entre na pasta do projeto:
   ```bash
   cd deck-s21
   ```
4. Inicie o servidor:
   ```bash
   python3 server.py
   ```
5. Anote o endereço e o código de 6 dígitos que o servidor mostra. Exemplo:
   - Endereço: `http://192.168.0.46:8787/`
   - Código: `074423`

## Configuração do celular

1. Abra o endereço no Chrome do S21.
2. Digite o código de 6 dígitos e toque em **Parear**.
3. Toque no botão de tela cheia (canto superior direito).
4. Gire o celular para o modo paisagem.
5. Ative **Opções do desenvolvedor → Permanecer ativo**. A tela não apaga durante a carga.
6. Ative **Proteger bateria** (limite de 85%).
7. Deixe o celular na tomada.

O pareamento acontece uma vez. O celular guarda o acesso. Nas próximas vezes, abra só `http://IP:8787/`.

O código de 6 dígitos muda toda vez que você inicia o servidor. Ele vale por 15 minutos.
Depois de 5 erros, o servidor bloqueia o pareamento. Reinicie o servidor para gerar um código novo.

## Permissões do macOS

Na primeira vez, o macOS pede permissões. Clique em **OK** em todas.

| Ação | Permissão |
|---|---|
| Música, microfone, volume, apps | **Automação** (Terminal controla Spotify, Música e Eventos do Sistema) |
| Botões de tecla (Meet, Zoom, captura) | **Acessibilidade** |

Para dar a permissão de Acessibilidade:

1. Abra **Ajustes do Sistema → Privacidade e Segurança → Acessibilidade**.
2. Ative o **Terminal**.

## Iniciar junto com o Mac

Rode este comando uma vez:

```bash
./instalar-autostart.sh
```

O servidor passa a iniciar sozinho. O endereço do celular fica no arquivo `deck.log`.

Para remover:

```bash
./instalar-autostart.sh remover
```

Rode o servidor pelo Terminal antes. Assim você aceita as permissões do macOS uma vez.

## Como editar os botões

Abra o arquivo `config.json`. Salve o arquivo e recarregue a página do celular. Não precisa reiniciar o servidor.

Estrutura de um botão:

```json
{ "label": "Chrome", "icon": "🌐", "action": { "type": "app", "name": "Google Chrome" } }
```

Campos opcionais do botão:

- `color`: cor de fundo (exemplo `"#1db954"`).
- `indicator`: faz o botão mudar com o estado do Mac. Valores: `mic_muted`, `sound_muted`, `music_playing`.
- `icon_on` e `color_on`: ícone e cor quando o indicador está ativo.
- `icon_app`: nome de um app. O botão mostra o ícone real desse app, tirado do Mac. Exemplo: `"icon_app": "Finder"`.

Botões do tipo `app` usam o ícone do app automaticamente. Se o servidor não achar o ícone, o botão mostra o emoji do campo `icon`.
O servidor guarda os ícones na pasta `.icons`. Para atualizar um ícone, apague o arquivo dele nessa pasta.
Quando um ícone falha, o Terminal do servidor mostra uma linha que começa com `[ícone]`.

No topo do arquivo, o campo `player` define o app de música: `"Spotify"` ou `"Music"`.

## Tipos de ação

| `type` | Campos | O que faz |
|---|---|---|
| `app` | `name` | Abre um app. |
| `url` | `url` | Abre uma página no navegador. |
| `folder` | `path` | Abre uma pasta no Finder. |
| `music` | `cmd`: `playpause`, `next`, `previous` | Controla o player. |
| `volume` | `delta`, `set` ou `mute: "toggle"` | Muda o volume de saída. |
| `mic` | `mode`: `toggle`, `mute`, `unmute` | Muta o microfone do sistema. |
| `keys` | `key` ou `code`, `modifiers`, `focus`, `repeat` | Envia uma tecla. `focus` abre o app antes. `repeat` repete a tecla (de 1 a 16 vezes). |
| `paste` | `text`, `focus` | Cola um texto fixo no app ativo do Mac. O texto substitui o que estava na área de transferência. |
| `shortcut` | `name` | Executa um atalho do app **Atalhos**. |
| `applescript` | `script` | Executa um AppleScript. |
| `shell` | `command` | Executa um comando no terminal. |
| `new_project` | `base`, `folders`, `open_with`, `date_prefix` | Pede um nome no Mac e cria a pasta do projeto. |

### Botão "Novo projeto"

1. Você toca no botão.
2. Uma janela aparece **na tela do Mac** e pede o nome.
3. O Mac cria a pasta `~/Projetos/AAAA-MM-DD-nome`.
4. O Mac cria as subpastas da lista `folders`.
5. O Mac abre a pasta no Finder.

Para abrir a pasta em outro app, use `"open_with": "Visual Studio Code"`.

### Mutar o microfone

O botão **Microfone** muta o microfone do sistema inteiro. Funciona em qualquer app.

Os botões **Mutar no Meet** e **Mutar no Zoom** enviam o atalho de teclado do app.
Eles só funcionam quando a chamada está aberta na janela ativa.
No Meet, a aba da chamada precisa estar visível no Chrome.

## Tela de informações

A primeira página do Deck (**Info**) mostra quatro cartões:

| Cartão | O que mostra |
|---|---|
| **Agora** | Hora, data e clima. |
| **Mac** | Uso de CPU e de memória. |
| **Claude Code** | Sessões abertas e a janela de 5 horas. |
| **Agenda** | Próximos eventos de hoje e de amanhã. |

Os dados aparecem alguns segundos depois de abrir a página.

### Clima

1. Crie o arquivo `config.local.json` na pasta do Deck. Use este comando (troque a cidade):
   ```bash
   echo '{"info": {"weather": {"city": "Curitiba"}}}' > config.local.json
   ```
2. Não precisa reiniciar o servidor.

O arquivo `config.local.json` guarda os seus ajustes pessoais. O Git ignora esse arquivo.
O servidor mistura o `config.local.json` com o `config.json`. Qualquer campo do `config.json` funciona nele.

O servidor envia o nome da cidade para o serviço gratuito Open-Meteo. Não precisa de conta.
Para não enviar o nome, use `"latitude"` e `"longitude"` no lugar de `"city"`. O serviço ainda recebe as coordenadas.

### Claude Code

O servidor lê só arquivos locais da pasta `~/.claude/projects`. Ele não lê token nem senha.

- **Sessões:** o servidor lê o nome da pasta do projeto e a hora da última escrita. Uma sessão com escrita nos últimos 45 segundos aparece como "trabalhando". Sessões sem escrita há mais de 30 minutos somem da lista. Mude esse tempo em `active_minutes`.
- **Janela de 5 horas:** o servidor soma as mensagens e os tokens do bloco atual de 5 horas. Ele mostra a hora de renovação. O valor é uma **estimativa**. Ele conta só o uso do Claude Code **neste Mac**. O uso no site claude.ai e em outros computadores não entra.
- O Deck **não mostra a porcentagem do limite do plano**. Não existe forma oficial de ler essa porcentagem.

### Agenda

Na primeira vez, o servidor compila um programa pequeno em Swift (precisa das ferramentas de linha de comando da Apple). Isso leva de 10 a 30 segundos.
Depois, o macOS pede acesso ao **Calendário**. Clique em **OK**.

Se o acesso falhar, abra **Ajustes do Sistema → Privacidade e Segurança → Calendários** e ative o **Terminal**.

### Desligar um cartão

Em `info`, coloque `"enabled": false` no grupo. Exemplo: `"calendar": {"enabled": false}`.

## Páginas pessoais (atalhos e textos)

O arquivo `config.local.json` pode ter uma lista `extra_pages`. Essas páginas entram depois das páginas do `config.json`.
Use esse arquivo para dados pessoais, como o texto da sua assinatura. O Git ignora o arquivo.

O arquivo `config.local.example.json` mostra um exemplo com duas páginas: **Atalhos** e **Textos**.

### Como criar um atalho no app Atalhos

1. Abra o app **Atalhos** no Mac.
2. Clique em **+** para criar um atalho. Dê um nome, por exemplo `Modo trabalho`.
3. Adicione as ações. Exemplos:
   - **Modo trabalho:** ação "Abrir app" para cada app que você usa.
   - **Modo foco:** ação "Definir foco" com o foco "Não Perturbe".
4. No Terminal, rode `shortcuts list`. O comando mostra os nomes exatos.
5. Use o nome exato no botão: `{"type": "shortcut", "name": "Modo trabalho"}`.

### Botões de tela e brilho

A página **Mac** tem botões para bloquear a tela, apagar a tela, abrir a proteção de tela e mudar o brilho.
Os botões de bloqueio, de brilho e de texto enviam teclas. Eles precisam da permissão de **Acessibilidade**.
Se o brilho não mudar, o código da tecla pode ser diferente no seu Mac. Me avise.

## Segurança

- O servidor aceita pedidos só de aparelhos pareados. O pareamento usa o código de 6 dígitos.
- Depois do pareamento, o celular guarda um token longo. O token fica no arquivo `.token` do Mac.
- O celular nunca envia comandos. Ele envia só o número do botão. Os comandos ficam no `config.json` do Mac.
- A conexão usa HTTP simples. Use somente na rede Wi-Fi de casa.
- Não mostre o código de pareamento a outras pessoas.
- Para desfazer todos os pareamentos, apague o arquivo `.token` e reinicie o servidor.

## Problemas comuns

**O celular não abre a página.**
Confira se os dois aparelhos estão na mesma rede Wi-Fi.
Confira se o Firewall do Mac não bloqueia o Python.

**O endereço mudou.**
O roteador pode trocar o IP do Mac. No roteador, reserve um IP fixo para o Mac.

**Um botão de tecla não faz nada.**
Confira a permissão de Acessibilidade para o Terminal.

**A música não aparece no topo.**
Confira o campo `player` no `config.json`. O app escolhido precisa estar instalado e aberto.
