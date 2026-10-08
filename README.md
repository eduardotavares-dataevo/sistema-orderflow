# OrderFlow

Aplicação web para consulta, criação e acompanhamento de pedidos integrada ao **Sankhya OM**, com painel administrativo central para gestão de clientes, tenants, licenças, versões e acessos.

## Componentes

| Componente | Responsabilidade | Porta padrão |
| --- | --- | --- |
| Aplicação OrderFlow | Operação comercial: login, dashboard, pedidos, produtos, promoções, BI e acessos | `6000` |
| Painel administrativo | Clientes, projetos, tenants, licenças, usuários, versões e deploys | `5005` |
| SQLite / PostgreSQL | Dados administrativos, permissões e sessões ativas | — |
| Sankhya OM | Autenticação, catálogo, estoque, parceiros e pedidos | Configurada por ambiente |

## Funcionalidades

- Login integrado ao Sankhya OM e autenticação de superadministrador por tenant.
- Dashboard comercial, portal de vendas e central de pedidos.
- Consulta de produtos, preços, estoque, produtos alternativos e sugeridos.
- Promoções, indicadores de BI e acompanhamento de comissões.
- Consulta, criação, edição, liberação, impressão e fechamento de pedidos.
- Controle de usuários, grupos autorizados e sessões ativas por tenant.
- Painel administrativo para clientes, projetos, instâncias, licenças, versões, deploy e rollback.

## Requisitos

- Python 3.11 ou superior
- Acesso ao Sankhya OM para utilizar os recursos de negócio
- Docker e Docker Compose (opcional, para execução em contêineres)
- `rsync` e acesso SSH à VPS (opcional, para o sincronizador)

## Configuração

Crie o arquivo local de configuração a partir do exemplo. Ele é ignorado pelo Git e **nunca deve ser enviado ao repositório**.

```bash
cp .env.example .env
```

Preencha ao menos as seguintes variáveis no `.env`:

```dotenv
TENANT_ID=minha_empresa
TENANT_NAME=Minha Empresa LTDA
SECRET_KEY=gere-um-valor-aleatorio-longo
ADMIN_SECRET_KEY=gere-outro-valor-aleatorio-longo

SANKHYA_BASE_URL=https://servidor-sankhya:8035
SANKHYA_APPKEY=chave-fornecida-pelo-sankhya
SANKHYA_USERNAME=usuario_da_integracao
SANKHYA_PASSWORD=senha_da_integracao

# Necessário somente antes da primeira inicialização do painel administrativo.
ADMIN_INITIAL_USERNAME=admin
ADMIN_INITIAL_PASSWORD=defina-uma-senha-forte
```

`ADMIN_INITIAL_PASSWORD` é usada apenas para criar o primeiro administrador quando o banco ainda está vazio. Após o primeiro acesso, remova-a do arquivo de ambiente.

Para gerar chaves de sessão seguras:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Execução local

Crie e ative um ambiente virtual, então instale as dependências:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Exporte o `.env` no shell para que os dois processos recebam a mesma configuração:

```bash
set -a
source .env
set +a
```

Em terminais separados, inicie a aplicação e o painel administrativo:

```bash
python app.py
```

```bash
python admin_panel/app.py
```

Endereços locais:

- Aplicação: `http://localhost:6000`
- Painel administrativo: `http://localhost:5005`
- Health checks: `/health` ou `/healthz`

O painel usa SQLite em `admin_panel/admin.db` por padrão. Para PostgreSQL, defina `ADMIN_DATABASE_URL` com uma URL `postgresql://...` e instale o driver compatível, por exemplo `psycopg2-binary`.

## Docker Compose

Com o `.env` configurado, suba os serviços:

```bash
docker compose up --build -d
```

O Compose disponibiliza:

- `orderflow-admin` em `http://localhost:5005`
- `orderflow-app` em `http://localhost:6010` (ou no valor de `PORT`)
- `orderflow-base` em `http://localhost:5010`

Para acompanhar os logs:

```bash
docker compose logs -f
```

O arquivo `caddy/Caddyfile` contém os proxies reversos previstos para os domínios de administração, produção e base. O Caddy não faz parte do `docker-compose.yml`; configure-o no ambiente de infraestrutura que o executará.

## Sincronização com VPS

O sincronizador observa os arquivos locais e os replica para a VPS via `rsync`/SSH. Antes do uso, instale sua dependência adicional:

```bash
pip install watchdog
./iniciar_sincronizador.sh
```

As variáveis `VPS_HOST`, `VPS_PORT`, `VPS_USER` e `REMOTE_DIR` definem o destino. Para encerrar o processo:

```bash
./parar_sincronizador.sh
```

## Estrutura

```text
.
├── app.py                 # Aplicação web principal
├── sankhya_api.py         # Cliente e operações da API Sankhya OM
├── orderflow_auth.py      # Permissões, superadmin e sessões por tenant
├── config.py              # Leitura de variáveis de ambiente
├── admin_panel/           # Painel administrativo e modelos do banco
├── templates/             # Telas da aplicação principal
├── static/                # CSS, JavaScript e ícones
├── docs/telas/            # Documentação das telas e regras de negócio
├── docker-compose.yml     # Orquestração dos serviços
└── sync_watcher.py        # Sincronização opcional com VPS
```

## Segurança

- Mantenha `.env`, bancos locais, certificados e chaves privadas fora do Git; as regras correspondentes já estão em `.gitignore`.
- Use chaves de sessão únicas em cada ambiente e habilite `SESSION_COOKIE_SECURE=true` em HTTPS.
- Não use credenciais padrão: o primeiro usuário do painel deve ser criado por `ADMIN_INITIAL_PASSWORD`.
- Faça a rotação imediata de qualquer chave que tenha sido exposta fora de um gerenciador de segredos.

## Documentação funcional

A descrição detalhada das telas, rotas e regras de negócio está em [docs/telas/README.md](docs/telas/README.md).
