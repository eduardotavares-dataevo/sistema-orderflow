import os
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("ADMIN_DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'admin.db')}")

class DatabaseAdapter:
    """
    Camada de Abstração de Banco de Dados com suporte transparente a SQLite e PostgreSQL.
    """
    def __init__(self, db_url):
        self.db_url = db_url
        self.is_postgres = db_url.startswith("postgresql://") or db_url.startswith("postgres://")

    def get_connection(self):
        if self.is_postgres:
            try:
                import psycopg2
                import psycopg2.extras
                conn = psycopg2.connect(self.db_url)
                conn.autocommit = False
                return conn, "postgres"
            except ImportError:
                raise ImportError("Pacote 'psycopg2' é necessário para conectar ao PostgreSQL. Instale com: pip install psycopg2-binary")
        else:
            db_path = self.db_url.replace("sqlite:///", "")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn, "sqlite"

db_adapter = DatabaseAdapter(DATABASE_URL)

def get_db_connection():
    conn, db_type = db_adapter.get_connection()
    return conn

def gerar_senha_forte(tamanho=14):
    """Gera uma senha forte com letras maiúsculas, minúsculas, dígitos e símbolos."""
    import secrets
    import string
    alfabeto = string.ascii_letters + string.digits + "!@#$%&*-_"
    while True:
        senha = "".join(secrets.choice(alfabeto) for _ in range(tamanho))
        if (any(c.islower() for c in senha)
                and any(c.isupper() for c in senha)
                and any(c.isdigit() for c in senha)
                and any(c in "!@#$%&*-_" for c in senha)):
            return senha

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Tabela de Clientes
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        razao_social TEXT NOT NULL,
        nome_fantasia TEXT NOT NULL,
        cnpj TEXT UNIQUE,
        contato_nome TEXT,
        email TEXT,
        telefone TEXT,
        valor_saas REAL DEFAULT 0.0,
        valor_mensalidade REAL DEFAULT 0.0,
        valor_por_licenca REAL DEFAULT 0.0,
        dia_vencimento INTEGER DEFAULT 10,
        data_inicio_contrato DATE,
        observacoes TEXT,
        status TEXT NOT NULL DEFAULT 'ativo', -- 'ativo', 'inativo'
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Migrações alter table se a tabela já existia
    for col_def in [
        ("valor_saas", "REAL DEFAULT 0.0"),
        ("valor_mensalidade", "REAL DEFAULT 0.0"),
        ("valor_por_licenca", "REAL DEFAULT 0.0"),
        ("dia_vencimento", "INTEGER DEFAULT 10"),
        ("data_inicio_contrato", "DATE"),
        ("observacoes", "TEXT")
    ]:
        try:
            cursor.execute(f"ALTER TABLE clientes ADD COLUMN {col_def[0]} {col_def[1]};")
        except Exception:
            pass

    # 1.1 Tabela de Projetos (Conceito de Projetos, Branches Git e Vínculo por Cliente)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS projetos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_projeto TEXT NOT NULL,
        codigo_slug TEXT UNIQUE NOT NULL,
        cliente_id INTEGER,
        branch_git TEXT NOT NULL DEFAULT 'main',
        repositorio_url TEXT,
        descricao TEXT,
        status TEXT NOT NULL DEFAULT 'ativo',
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL
    );
    """)

    # 2. Tabela de Tenants (Instâncias/Ambientes)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tenants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER,
        projeto_id INTEGER,
        tenant_id TEXT UNIQUE NOT NULL,
        nome_empresa TEXT NOT NULL,
        cnpj TEXT,
        sankhya_url TEXT NOT NULL,
        sankhya_appkey TEXT,
        ambiente TEXT NOT NULL DEFAULT 'teste', -- 'producao' ou 'teste'
        porta INTEGER NOT NULL DEFAULT 6000,
        ip_externo TEXT,
        max_licencas INTEGER NOT NULL DEFAULT 5,
        status TEXT NOT NULL DEFAULT 'ativo', -- 'ativo', 'suspenso', 'bloqueado'
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        validade_licenca DATE,
        FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL,
        FOREIGN KEY (projeto_id) REFERENCES projetos(id) ON DELETE SET NULL
    );
    """)

    # Adaptações de coluna para banco existente
    for col_def in [
        ("cliente_id", "INTEGER"),
        ("projeto_id", "INTEGER"),
        ("ambiente", "TEXT DEFAULT 'teste'"),
        ("porta", "INTEGER DEFAULT 6000"),
        ("ip_externo", "TEXT"),
        ("versao_atual", "TEXT DEFAULT 'v1.0.5'"),
        ("superadmin_user", "TEXT DEFAULT 'admin'"),
        ("superadmin_password", "TEXT"),
        ("superadmin_password_hash", "TEXT"),
        ("modulos_habilitados", "TEXT")
    ]:
        try:
            cursor.execute(f"ALTER TABLE tenants ADD COLUMN {col_def[0]} {col_def[1]};")
        except Exception:
            pass

    # 2.1 Tabela de Grupos Permitidos no OrderFlow
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orderflow_grupos_permitidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        codgrupo INTEGER NOT NULL,
        nomegrupo TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, codgrupo)
    );
    """)

    # 2.2 Tabela de Usuários Permitidos no OrderFlow
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orderflow_usuarios_permitidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        codusu INTEGER NOT NULL,
        nomeusu TEXT NOT NULL,
        nomevend TEXT,
        is_admin INTEGER NOT NULL DEFAULT 0,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, codusu)
    );
    """)

    # 3. Tabela de Usuários Admin / Restrito
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        nome TEXT NOT NULL,
        perfil TEXT NOT NULL DEFAULT 'restrito', -- 'admin' ou 'restrito'
        status TEXT NOT NULL DEFAULT 'ativo',
        permissoes TEXT,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    try:
        cursor.execute("ALTER TABLE admin_users ADD COLUMN permissoes TEXT;")
    except Exception:
        pass

    # 4. Tabela de Sessões Ativas
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS active_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        codusu INTEGER NOT NULL,
        nome_usuario TEXT NOT NULL,
        session_token TEXT UNIQUE NOT NULL,
        ip_address TEXT,
        ultimo_ping TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
    );
    """)

    # 5. Tabela de Histórico de Contratos/Licenças do Cliente
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contrato_historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        data_inicio DATE NOT NULL,
        data_fim DATE,
        qtd_licencas INTEGER NOT NULL DEFAULT 1,
        valor_saas REAL NOT NULL DEFAULT 0.0,
        valor_mensalidade REAL NOT NULL DEFAULT 0.0,
        valor_por_licenca REAL NOT NULL DEFAULT 0.0,
        dia_vencimento INTEGER DEFAULT 10,
        observacao TEXT,
        criado_por TEXT,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
    );
    """)

    try:
        cursor.execute("ALTER TABLE contrato_historico ADD COLUMN valor_saas REAL DEFAULT 0.0;")
    except Exception:
        pass

    # Insere dados demonstrativos para uma instalação nova, sem credenciais reais.
    cursor.execute("SELECT COUNT(*) FROM clientes")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO clientes (razao_social, nome_fantasia, cnpj, contato_nome, email, telefone, status)
        VALUES ('Empresa Demonstração LTDA', 'Empresa Demonstração', '00.000.000/0001-00', 'Administrador', 'contato@example.com', '(00) 00000-0000', 'ativo')
        """)
        cliente_id = cursor.lastrowid

        # Histórico Inicial de Contrato para Auto Giro
        cursor.execute("""
        INSERT INTO contrato_historico (cliente_id, data_inicio, qtd_licencas, valor_mensalidade, valor_por_licenca, dia_vencimento, observacao, criado_por)
        VALUES (?, '2026-01-01', 10, 500.00, 49.90, 10, 'Contrato inicial de 10 licenças', 'Sistema')
        """, (cliente_id,))
    else:
        cursor.execute("SELECT id FROM clientes LIMIT 1")
        cliente_id = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tenants")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO tenants (cliente_id, tenant_id, nome_empresa, cnpj, sankhya_url, sankhya_appkey, ambiente, porta, max_licencas, status, validade_licenca)
        VALUES (?, ?, ?, '00.000.000/0001-00', ?, ?, 'teste', 6010, 2, 'ativo', '2027-12-31')
        """, (
            cliente_id,
            os.getenv("DEFAULT_TENANT_ID", "empresa_demo"),
            os.getenv("DEFAULT_TENANT_NAME", "Empresa Demonstração (Teste)"),
            os.getenv("DEFAULT_TENANT_SANKHYA_URL", ""),
            os.getenv("DEFAULT_TENANT_SANKHYA_APPKEY", ""),
        ))

    # O primeiro administrador só é criado quando a senha é informada por ambiente.
    # Isso evita publicar ou recriar credenciais conhecidas.
    cursor.execute("SELECT COUNT(*) FROM admin_users")
    if cursor.fetchone()[0] == 0:
        initial_password = os.getenv("ADMIN_INITIAL_PASSWORD")
        if initial_password:
            cursor.execute("""
            INSERT INTO admin_users (username, password_hash, nome, perfil, status)
            VALUES (?, ?, ?, 'admin', 'ativo')
            """, (
                os.getenv("ADMIN_INITIAL_USERNAME", "admin"),
                generate_password_hash(initial_password),
                "Administrador Principal",
            ))
        else:
            print(
                "[OrderFlow Admin] Nenhum administrador inicial foi criado. "
                "Defina ADMIN_INITIAL_PASSWORD antes da primeira inicialização."
            )

    # 6. Tabela de Versões / Releases do Sistema
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sistema_versoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag_versao TEXT UNIQUE NOT NULL,
        branch TEXT NOT NULL DEFAULT 'main',
        docker_tag TEXT,
        titulo TEXT NOT NULL,
        prompt TEXT,
        descricao TEXT,
        tipo TEXT DEFAULT 'minor', -- 'major', 'minor', 'patch', 'hotfix'
        status TEXT DEFAULT 'estavel', -- 'estavel', 'beta', 'depreciada'
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 7. Tabela de Histórico de Deploys e Rollbacks por Tenant
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tenant_deploys_historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        versao_tag TEXT NOT NULL,
        versao_anterior_tag TEXT,
        acao TEXT NOT NULL DEFAULT 'deploy', -- 'deploy', 'rollback'
        executado_por TEXT,
        status TEXT NOT NULL DEFAULT 'sucesso', -- 'sucesso', 'falha'
        observacao TEXT,
        log_execucao TEXT,
        data_deploy TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    try:
        cursor.execute("ALTER TABLE sistema_versoes ADD COLUMN prompt TEXT;")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE sistema_versoes ADD COLUMN projeto_id INTEGER;")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tenant_deploys_historico ADD COLUMN log_execucao TEXT;")
    except Exception:
        pass

    # Seed inicial de Projetos caso a tabela esteja vazia
    cursor.execute("SELECT COUNT(*) FROM projetos")
    if cursor.fetchone()[0] == 0:
        cursor.execute("SELECT id FROM clientes LIMIT 1")
        row_c = cursor.fetchone()
        c_id = row_c[0] if row_c else 1
        cursor.execute("""
            INSERT INTO projetos (nome_projeto, codigo_slug, cliente_id, branch_git, descricao, status)
            VALUES ('OrderFlow Auto Giro', 'proj_autogiro', ?, 'main', 'Projeto Principal Auto Giro Distribuidora de Peças.', 'ativo')
        """, (c_id,))
        def_proj_id = cursor.lastrowid
        cursor.execute("UPDATE tenants SET projeto_id = ? WHERE projeto_id IS NULL", (def_proj_id,))
    else:
        cursor.execute("SELECT id FROM projetos LIMIT 1")
        row_p = cursor.fetchone()
        def_proj_id = row_p[0] if row_p else 1

    # Seed inicial de Versões caso a tabela esteja vazia
    cursor.execute("SELECT COUNT(*) FROM sistema_versoes")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO sistema_versoes (tag_versao, branch, docker_tag, titulo, descricao, tipo, status, projeto_id, criado_em)
            VALUES 
            ('v1.0.0', 'main', 'eduardotavares24/orderflow:v1.0.0', 'Versão Base OrderFlow', 'Versão inicial estável da Central de Pedidos e Portal de Vendas integrada ao Sankhya OM.', 'major', 'estavel', ?, '2026-08-20 10:00:00'),
            ('v1.0.1', 'main', 'eduardotavares24/orderflow:v1.0.1', 'Multi-Tenant, Licenças em Tempo Real & IP Externo', 'Controle desacoplado de acessos, autenticação de Super Admin, monitoramento em tempo real de sessões ativas e configuração de IP externo.', 'minor', 'estavel', ?, '2026-09-08 22:00:00')
        """, (def_proj_id, def_proj_id))

    # Garante que todos os tenants existentes tenham versao_atual preenchida
    cursor.execute("UPDATE tenants SET versao_atual = 'v1.0.5' WHERE versao_atual IS NULL OR versao_atual = ''")
    cursor.execute("UPDATE tenants SET projeto_id = ? WHERE projeto_id IS NULL", (def_proj_id,))
    cursor.execute("UPDATE sistema_versoes SET projeto_id = ? WHERE projeto_id IS NULL", (def_proj_id,))

    # Atualiza tenant autogiro_local para autogiro_base (ambiente base, porta 5010)
    cursor.execute("""
        UPDATE tenants
        SET tenant_id = 'autogiro_base',
            nome_empresa = 'Auto Giro (Base)',
            ambiente = 'base',
            porta = 5010
        WHERE tenant_id = 'autogiro_local'
    """)
    cursor.execute("UPDATE active_sessions SET tenant_id = 'autogiro_base' WHERE tenant_id = 'autogiro_local'")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
