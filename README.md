# WhatsApp → Alexa

Avisa na Alexa quando **contatos específicos** te mandam mensagem no WhatsApp ou quando alguém **te marca (@) num grupo**.

Usa a [WuzAPI](https://github.com/asternic/wuzapi), uma API de WhatsApp open source que roda localmente, com o seu próprio número conectado como "aparelho conectado". Junto vem uma pequena **ponte** em Python, sem dependências externas no servidor, que recebe as mensagens, aplica as suas regras e dispara o aviso. Tudo é configurado por uma interface web em `http://127.0.0.1:8090`.

## Como avisa

Dá para ligar mais de uma forma ao mesmo tempo:

| Saída | O que a Alexa fala | Requisitos |
|---|---|---|
| **Voz pelo PC** | "Fulano te mandou mensagem no WhatsApp" (diz o nome e o grupo) | Echo pareado no PC via Bluetooth, usado como alto-falante |
| **Rotina via TRIGGERcmd** | Frase fixa da rotina, por exemplo "Chegou mensagem importante no WhatsApp" | [TRIGGERcmd](https://www.triggercmd.com) + rotina da Alexa "Quando: Casa inteligente → comando" (no máximo 1 por minuto no plano grátis) |
| **Voice Monkey** | Texto livre, com a voz da própria Alexa | Conta no [Voice Monkey](https://voicemonkey.io) (200 avisos por mês no plano grátis) |
| **Webhook** | — | Qualquer URL (use `{texto}` na URL, ou receba um POST em JSON) |

## Recursos

- Lista de contatos monitorados, com nome falado e frase própria. Números brasileiros funcionam com ou sem o 9 e com ou sem o 55.
- Menções em grupos, inclusive respostas a mensagens suas, com filtro por grupo.
- Intervalo mínimo entre avisos da mesma conversa e horário de silêncio.
- Opção de ler o texto da mensagem em voz alta.
- "Atividade recente" com um botão **+ monitorar** para quem te mandou mensagem.
- Conexão por QR code ou por código de pareamento, direto pela interface.
- A ponte inicia o `wuzapi.exe` sozinha e reinicia ele se cair. Não precisa de Docker.

## Instalação (Windows)

Requisitos: Python 3.10+, Git, e Go **ou** Docker (só para compilar a WuzAPI). O `ffmpeg` no PATH é opcional e serve para reforçar o volume da voz.

```powershell
git clone https://github.com/Victorlcastro/whatsapp-alexa.git
cd whatsapp-alexa
.\configurar.ps1          # cria o .env com tokens aleatórios e instala edge-tts/pyttsx3
.\compilar-wuzapi.ps1     # baixa e compila a WuzAPI em bin\wuzapi.exe
python ponte\server.py    # sobe a WuzAPI e a ponte
```

Depois:

1. Abra **http://127.0.0.1:8090** e conecte o WhatsApp. No celular: Aparelhos conectados → Conectar aparelho. Use o QR ou o código.
2. Adicione os contatos e escolha os grupos.
3. Ligue as saídas que quiser e use os botões **Testar**.
4. Para iniciar com o Windows, rode `.\instalar-autostart.ps1`.

### Rotina da Alexa com TRIGGERcmd

1. No TRIGGERcmd Agent, crie um comando, por exemplo **"Aviso Zap"**, com `ground` igual a `foreground`. Pode ser algo inofensivo, como `rundll32.exe user32.dll,MessageBeep`.
2. No app Alexa, crie uma rotina: **Quando** → Casa inteligente → *aviso zap*; **Alexa vai** → Alexa diz → a sua frase; escolha o Echo como dispositivo.
3. Na interface, ative **"Alexa via TRIGGERcmd"**, informe o nome do computador e do comando, e clique em **Testar**.

A ponte lê o token do TRIGGERcmd em `%USERPROFILE%\.TRIGGERcmdData\token.tkn`.

## Segurança e privacidade

- A WuzAPI e a ponte escutam **apenas em 127.0.0.1**, ou seja, não ficam expostas na rede.
- `.env`, `ponte/config.json` (seus contatos e grupos), `data/` (sessão do WhatsApp) e os logs **não são versionados**. O `.gitignore` funciona como lista branca.
- A voz do PC usa o edge-tts: o texto do aviso é enviado à Microsoft para virar áudio. Sem internet, ele usa a voz local do Windows.
- Conectar o WhatsApp por APIs não oficiais vai contra os termos de uso do WhatsApp. Use por sua conta e risco, de preferência só para receber, como aqui.

## Arquitetura

```
WhatsApp ──► wuzapi.exe (127.0.0.1:8080) ──webhook──► ponte/server.py (127.0.0.1:8090)
                                                        ├─► voz no PC (falar_aviso.py → Echo via Bluetooth)
                                                        ├─► TRIGGERcmd → rotina da Alexa
                                                        ├─► Voice Monkey → anúncio na Alexa
                                                        └─► webhook genérico
```

## Licença

MIT. A WuzAPI tem a própria licença (MIT); veja o repositório original.
