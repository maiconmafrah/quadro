# quadro

Ferramenta pessoal para baixar vídeos do YouTube e do Facebook a partir de um
link colado. Backend em Python (FastAPI + `yt-dlp`) hospedado em uma VPS
própria, e frontend estático hospedado no GitHub Pages.

## Arquitetura

```
[ navegador ]
     │  cola o link + chave de acesso
     ▼
[ frontend estático — GitHub Pages ]
     │  POST /api/download  { url, access_key }
     ▼
[ backend FastAPI — VPS Oracle Cloud, atrás do Caddy ]
     │  usa yt-dlp + ffmpeg para baixar e converter para mp4
     ▼
 arquivo .mp4 devolvido direto na resposta,
 apagado do servidor logo em seguida
```

- **`backend/`** — API em FastAPI. Único endpoint, `POST /api/download`,
  protegido por uma chave de acesso simples. Veja
  [`backend/README.md`](backend/README.md) para o deploy completo (o que já
  está em produção, incluindo a convivência com outro app no mesmo Caddy).
- **`frontend/`** — HTML/CSS/JS puro (sem frameworks), pronto para publicar
  no GitHub Pages. Um único formulário com estados visuais (aguardando →
  processando → concluído/erro) e alternância de tema claro/escuro.
- **`mockup/`** — o design canvas usado para desenhar a interface antes de
  implementar (não faz parte da aplicação em si).

## Status atual

O backend **já está rodando** em produção:
`https://147-15-107-15.sslip.io/quadro-api` (rodando como serviço systemd
`quadro.service`, atrás do Caddy que também serve outra aplicação sua nesse
mesmo servidor — por isso o prefixo `/quadro-api`). O `frontend/app.js` já
aponta para esse endereço.

**Falta só uma coisa para funcionar de ponta a ponta:** depois de publicar o
frontend no GitHub Pages (passo 2 abaixo), me diga a URL final para eu
atualizar o `FRONTEND_ORIGIN` no `.env` do servidor — sem isso o CORS
bloqueia as requisições do navegador.

⚠️ **YouTube**: o servidor está sendo bloqueado pelo próprio YouTube
("confirme que não é um robô"), uma restrição deles contra IPs de
datacenter — não um bug daqui. O app mostra um erro claro quando isso
acontece. **Facebook funciona normalmente.** Detalhes e alternativas em
[`backend/README.md`](backend/README.md#7-limitação-conhecida-youtube-pode-bloquear-downloads-do-servidor).

## 1. Backend (já configurado)

O backend já está instalado e rodando. A chave de acesso (`ACCESS_KEY`)
gerada para essa instância foi te passada separadamente no chat — guarde-a
num lugar seguro (gerenciador de senhas), pois não fica salva em nenhum
arquivo deste repositório. Para reinstalar do zero em outro servidor, siga
[`backend/README.md`](backend/README.md).

## 2. Publicar o frontend no GitHub Pages

Este repositório inteiro (backend, frontend, mockup) fica junto num só
lugar, mas só a pasta `frontend/` vai para o ar. Isso é feito por um
workflow do GitHub Actions ([`.github/workflows/pages.yml`](.github/workflows/pages.yml))
que publica automaticamente o conteúdo de `frontend/` toda vez que há um
push na branch `main`.

```bash
git add .
git commit -m "Publica o quadro"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git
git push -u origin main
```

Depois, em **Settings → Pages** do repositório, em "Build and deployment →
Source", selecione **GitHub Actions** (só precisa fazer isso uma vez). O
endereço final será algo como `https://seu-usuario.github.io/seu-repo/`.

A chave de acesso não fica em nenhum arquivo publicado — ela é digitada por
você no navegador e, se marcar a opção, fica salva só no `localStorage` do
seu próprio navegador.

## Uso responsável

Esta ferramenta é para **uso pessoal**. Ao usá-la:

- Baixe apenas vídeos que você tem o direito de baixar (conteúdo próprio,
  de domínio público, ou para o qual você tem autorização explícita).
- Respeite os direitos autorais e os Termos de Uso do YouTube e do Facebook
  — baixar e redistribuir conteúdo de terceiros sem autorização pode violar
  esses termos e a lei de direitos autorais.
- Não compartilhe sua `ACCESS_KEY` nem exponha este backend publicamente sem
  proteção — ele consome banda e processamento da sua VPS a cada download.

O autor desta ferramenta não se responsabiliza por usos que violem direitos
autorais ou termos de serviço de terceiros.
