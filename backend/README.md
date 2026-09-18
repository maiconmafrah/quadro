# Quadro — backend

API pessoal em FastAPI que usa `yt-dlp` para baixar vídeos do YouTube e do
Facebook a partir de um link, e devolve o arquivo `.mp4` diretamente na
resposta. O vídeo nunca fica salvo no servidor: ele vai para uma pasta
temporária durante o download e é apagado assim que a resposta termina de
ser enviada.

> Este README documenta o deploy real, feito numa VPS Oracle Cloud rodando
> **Oracle Linux 9**, que já hospedava outra aplicação (atrás de um
> **Caddy** compartilhado). Se o seu servidor for Ubuntu/Debian puro e sem
> nada rodando ainda, os comandos de pacote mudam (`apt` em vez de `dnf`) e
> você pode preferir Nginx + Certbot em vez de Caddy — a lógica é a mesma.

## 1. Dependências de sistema (Oracle Linux 9)

```bash
sudo dnf install -y python3-pip
```

**ffmpeg** não vem nos repositórios oficiais do Oracle Linux/RHEL (por causa
de licenciamento de codecs). O jeito mais simples e confiável é um build
estático:

```bash
curl -sL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz
cd /tmp && tar xf ffmpeg.tar.xz
sudo cp ffmpeg-*-amd64-static/ffmpeg ffmpeg-*-amd64-static/ffprobe /usr/local/bin/
sudo chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
ffmpeg -version
```

## 2. Instalação do backend

```bash
sudo mkdir -p /opt/quadro/backend
sudo chown -R $USER:$USER /opt/quadro
# copie main.py e requirements.txt para /opt/quadro/backend (scp, git, etc.)

cd /opt/quadro/backend
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

> **yt-dlp precisa ser atualizado com frequência** — o YouTube e o Facebook
> mudam seus sites o tempo todo e quebram versões antigas:
> `./venv/bin/pip install --upgrade yt-dlp`. Vale automatizar isso (cron
> semanal, por exemplo).

## 3. Configuração (`.env`)

Copie `.env.example` para `.env` dentro de `/opt/quadro/backend` e ajuste:

- `ACCESS_KEY` — chave longa e aleatória (gere com `openssl rand -hex 24`).
- `FRONTEND_ORIGIN` — o endereço exato do seu GitHub Pages (ex:
  `https://seu-usuario.github.io`).

```bash
chmod 600 /opt/quadro/backend/.env
```

## 4. Serviço systemd

Use [`quadro.service`](quadro.service) como base (ajuste `User=` se não for
`opc`):

```bash
sudo cp quadro.service /etc/systemd/system/quadro.service
sudo systemctl daemon-reload
sudo systemctl enable --now quadro.service
sudo systemctl status quadro.service
journalctl -u quadro.service -f
```

O serviço escuta só em `127.0.0.1:8000` — não é exposto direto à internet,
só o proxy reverso (Caddy/Nginx) fala com ele.

## 5. Expondo com HTTPS

### Se o servidor já roda Caddy para outra aplicação (nosso caso)

Sem domínio próprio, usamos um endereço gratuito do
[sslip.io](https://sslip.io) que já resolve para o IP do servidor (ex:
`147-15-107-15.sslip.io` → `147.15.107.15`), e o Caddy emite HTTPS
automaticamente para ele — não precisa de Certbot.

Como o mesmo domínio já atende outro app, a API do quadro fica num caminho
próprio (`/quadro-api/*`) para não colidir com as rotas existentes. Veja
[`Caddyfile`](Caddyfile) — o bloco relevante, adicionado a um `Caddyfile` já
existente:

```caddyfile
handle_path /quadro-api/* {
    reverse_proxy 127.0.0.1:8000
}
```

Esse bloco entra **dentro** do site block do domínio, antes de qualquer
`handle` "pega-tudo". Depois de editar:

```bash
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy   # reload, nunca restart — não derruba o outro app
```

Nesse cenário o frontend deve apontar para
`https://147-15-107-15.sslip.io/quadro-api` (com o prefixo) em vez do
domínio puro.

### Se for uma VPS dedicada, só para o quadro

Instale Nginx + Certbot normalmente (`dnf install nginx certbot
python3-certbot-nginx` no Oracle Linux, ou `apt install nginx certbot
python3-certbot-nginx` no Ubuntu) e configure um proxy reverso comum para
`127.0.0.1:8000` com `client_max_body_size` e timeouts generosos (o download
de um vídeo grande demora), depois `certbot --nginx -d seu-dominio.com`.
Nesse caso o frontend aponta direto para `https://seu-dominio.com`, sem
prefixo.

## 6. Limites de tamanho/duração

Configuráveis via `.env`:

- `MAX_DURATION_SECONDS` — rejeita vídeos mais longos que isso (padrão: 3600 = 1h)
- `MAX_FILESIZE_MB` — rejeita arquivos maiores que isso (padrão: 500 MB)
- `EXTRACT_TIMEOUT_SECONDS` — tempo máximo para ler metadados do vídeo (padrão: 30s)
- `DOWNLOAD_TIMEOUT_SECONDS` — tempo máximo para o download completo (padrão: 600s)

O backend primeiro consulta os metadados do vídeo (duração e tamanho
aproximado) e só inicia o download de fato se estiver dentro dos limites.

## 7. Limitação conhecida: YouTube pode bloquear downloads do servidor

O YouTube vem bloqueando agressivamente downloads feitos a partir de IPs de
VPS/datacenter, pedindo "confirme que você não é um robô". Isso **não é um
bug do backend** — testamos sistematicamente todos os "clientes" que o
yt-dlp consegue simular (web, android, ios, tv, mweb...) e todos batem no
mesmo bloqueio quando o IP do servidor está sinalizado. O Facebook, por
outro lado, funciona normalmente sem autenticação.

Quando isso acontece, a API responde com o erro `youtube_blocked` (422) e
uma mensagem explicando a situação — o app não trava, só informa que o
YouTube está indisponível no momento.

Se quiser tentar contornar no futuro, as opções reais são:

1. **Cookies de uma conta logada** (`cookiefile` nas opções do yt-dlp) — o
   servidor passa a se autenticar como você no YouTube. Funciona bem, mas
   as credenciais da sessão ficam no servidor e expiram periodicamente.
2. **Proxy residencial/móvel** — troca o IP "de datacenter" por um IP
   "normal", sem precisar de conta nenhuma, mas é um serviço pago.

## 8. Formato de erros

Toda resposta de erro é um JSON no formato:

```json
{ "error": "codigo_do_erro", "message": "Mensagem legível em português." }
```

Códigos possíveis: `unauthorized` (401), `invalid_url` (400),
`extraction_failed` / `youtube_blocked` (422),
`video_too_long` / `file_too_large` (413), `download_failed` (502),
`timeout` (504), `internal_error` (500).
