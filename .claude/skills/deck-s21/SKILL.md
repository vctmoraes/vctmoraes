---
name: deck-s21
description: Use ao trabalhar no Deck S21, o painel de botões que transforma um celular Android em controle do MacBook (pasta deck-s21). Cobre editar config.json e config.local.json, criar botões e ações, cartões de informação (clima, Claude Code, agenda, música, lembretes), ícones, fundo, testar sem o Mac e depurar erros de AppleScript, permissões e pareamento.
---

# Deck S21

O Deck S21 é um servidor em Python (só biblioteca padrão) que roda no Mac. O celular abre a página no Chrome e toca nos botões. O servidor executa a ação no Mac.

## Arquivos

| Arquivo | Função |
|---|---|
| `deck-s21/server.py` | Servidor HTTP, ações, cartões de informação, ícones, verificação (`--check`). |
| `deck-s21/web/index.html` | Página única (HTML, CSS e JS). Sem biblioteca externa. |
| `deck-s21/web/icons/` | Ícones de linha Lucide (licença ISC). |
| `deck-s21/config.json` | Botões, páginas e visual. É público. |
| `deck-s21/config.local.json` | Ajustes pessoais. O Git ignora. Mistura com o `config.json`. |
| `deck-s21/config.local.example.json` | Exemplo do arquivo pessoal. |
| `deck-s21/tests/smoke.py` | Teste automático sem Mac. |
| `deck-s21/README.md` | Guia do usuário: tipos de ação, cartões, aparência. |

## Regras do projeto

1. **O repositório é público.** Nunca grave dados pessoais no Git: cidade, textos, nomes de atalhos, fotos, e-mail. Eles ficam no `config.local.json`. O arquivo aceita `extra_pages` (páginas extras) e mistura dicionários (`info`, `ui`).
2. **Não versione** `.token`, `.state.json`, `.cache/`, `.icons/`, `backgrounds/`, `deck.log`, `config.local.json`.
3. **Só biblioteca padrão do Python 3.9.** O Python do macOS (ferramentas de linha de comando) é o 3.9. Não use `match`, nem `X | Y` em tipos.
4. **Não leia o token de login do Claude.** Decisão do projeto: os termos do Claude Code limitam esse token a produtos da Anthropic. A janela de 5 horas vem dos arquivos locais em `~/.claude/projects`.
5. **Segurança das rotas:** o celular envia só o id do botão. Os comandos ficam no Mac. Rotas de arquivo validam o nome (sem `/` e sem `..`). Rotas de ícone de app pedem o app pelo nome e só aceitam nomes que estão na configuração.
6. Desenvolva na branch indicada pela sessão. Não abra pull request sem pedido.

## Como falar com o usuário

- Responda em português do Brasil, com frases curtas (skill `estilo-ste100-ptbr`).
- Cada comando vai em um bloco `bash` separado. Passos numerados.
- O usuário não edita JSON à mão com facilidade. Entregue um comando pronto (`cat > arquivo <<'EOF'`) ou um comando `python3 -c`.
- O servidor ocupa a aba do Terminal. Mande rodar outros comandos em uma **nova aba** (`Cmd + T`).
- Diga quando precisa **reiniciar o servidor**: só quando `server.py` muda. Mudança em `config.json`, `config.local.json` ou `web/` vale ao recarregar a página.
- Entrega padrão: commit e push na branch, depois `git pull` no Mac. Quem usa o início automático roda `./instalar-autostart.sh` de novo para reiniciar.

## Como testar sem o Mac

1. `python3 tests/smoke.py` — sobe o servidor com comandos falsos do macOS, pareia, confere rotas, cartões, ícones e executa todos os botões. Deve terminar com `N de N testes passaram`.
2. `python3 server.py --check` — confere a configuração: tipo de ação, campos, ícones, fundo, cartões. Mostra `ERRO` e `AVISO` com a página e o botão.
3. Para ver a tela: Playwright (já instalado) com janela de **873×394** (S21 na horizontal). Confira também a sobra de rolagem de `#grid`.
4. Ao mudar o `server.py`, rode os dois testes. Ao mudar a tela, tire uma captura.

## O que só o Mac confirma

O teste sem Mac **não prova**: o AppleScript compilar, as permissões do macOS (Automação, Acessibilidade, Calendários, Lembretes), o compilador Swift e os ícones reais dos apps. Diga isso ao usuário e peça o teste no Mac.

Onde o erro aparece:
- Em botões: mensagem vermelha no celular.
- Em cartões de informação: texto vermelho no cartão e uma linha `[info] ...` no Terminal do servidor.
- Em ícones: linha `[ícone] ...` no Terminal.

Peça ao usuário o **texto exato** do erro.

## Como acrescentar

- **Botão:** uma linha no `config.json`: `{"label", "symbol", "icon", "action": {...}}`. Rode `--check`.
- **Tipo de ação novo:** função `act_xxx(a, cfg)` em `server.py`, entrada em `ACTIONS`, campos em `REQUIRED_FIELDS`/`check_action`, linha na tabela do README e teste no `smoke.py`.
- **Cartão novo:** `Section(nome, ttl, função)` em `SECTIONS` (`server.py`); em `web/index.html` acrescente `CARD_TITLES`, `CARD_SECTIONS` e um `draw(...)` em `drawInfo`; acrescente o nome em `INFO_CARDS` (`server.py`). A lista existe nos dois arquivos: mantenha igual.
- **Ícone de linha:** baixe o SVG do Lucide para `web/icons/` e use `"symbol": "nome"`.
- **Fundo pronto:** `BG_PRESETS` existe em `server.py` e em `web/index.html`. Mantenha igual.

## Armadilhas já encontradas

1. **Cache por id.** O id do botão é `página-posição` e muda quando a lista muda. Nunca use o id como chave de cache. O ícone de app é pedido pelo **nome do app** (`/api/icon?app=`).
2. **CSS `background-image` não aceita cor.** Um fundo que termina em cor (`#0b1020`) invalida a regra. Use `background:`.
3. **AppleScript:** a variável `st` deu erro de sintaxe. Use nomes longos (`stateText`), compare `player state is playing` e arredonde fora do bloco `tell`. Não esconda erros: devolva `error<TAB>mensagem` e mostre na tela.
4. **Saída do Python no launchd** é bufferizada. O plist usa `python3 -u`.
5. **IP do Mac:** `ipconfig getifaddr en0`. O truque do socket mostra o IP da VPN (`10.x`).
6. **`pbcopy`** precisa de `LANG=en_US.UTF-8`, ou os acentos quebram sob o launchd.
7. **EventKit (agenda, lembretes):** o servidor compila um programa Swift uma vez em `.cache/` com `swiftc`. A permissão é do Calendário/Lembretes.
8. **Bateria do celular:** `navigator.getBattery` só existe em páginas seguras (https). O Deck usa http, então a linha pode não aparecer.
9. **Capa do álbum:** primeiro o app Música; depois a busca do iTunes (envia artista e álbum à Apple; `online_art: false` desliga).
10. **Pareamento:** código de 6 dígitos, 15 minutos, 5 tentativas. O token longo fica no `.token` do Mac e no navegador do celular.
