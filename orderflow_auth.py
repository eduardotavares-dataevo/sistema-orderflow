"""
Módulo de Gestão de Permissões, Autenticação de Super Admin e Controle de Acessos Nativo do OrderFlow.
Desacoplado de campos adicionais Sankhya (TSIUSU.AD_ADMCENTRALPED e TSIGRU.AD_ACESSACENTRALPED).
"""
import os
import sqlite3
from werkzeug.security import check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "admin_panel", "admin.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def verificar_superadmin(tenant_id, usuario, senha):
    """
    Verifica se as credenciais correspondem ao Super Admin do Tenant no OrderFlow.
    Retorna: (is_superadmin: bool, user_info: dict)
    """
    if not usuario or not senha:
        return False, None

    usuario = usuario.strip().lower()
    senha = senha.strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, tenant_id, nome_empresa, superadmin_user, superadmin_password, superadmin_password_hash, status
        FROM tenants
        WHERE tenant_id = ?
    """, (tenant_id,))
    t = cursor.fetchone()
    conn.close()

    if not t:
        return False, None

    # Se a instância estiver suspensa ou bloqueada
    if t["status"] != "ativo":
        return False, None

    s_user = (t["superadmin_user"] or "admin").strip().lower()
    s_pass = t["superadmin_password"] or ""
    s_hash = t["superadmin_password_hash"] or ""

    if usuario == s_user:
        # Verifica senha em texto puro ou hash
        senha_valida = (senha == s_pass) or (s_hash and check_password_hash(s_hash, senha))
        if senha_valida:
            user_info = {
                "codusu": 1,
                "nome": f"Super Admin ({t['nome_empresa']})",
                "usuario": s_user.upper(),
                "codemp": 1,
                "codvend": 0,
                "nomevend": "Administrador do Tenant",
                "is_adm": True,
                "is_superadmin": True,
                "tenant_id": tenant_id,
                "jsessionid": None
            }
            return True, user_info

    return False, None


def verificar_permissao_orderflow(tenant_id, codusu, codgrupo):
    """
    Verifica se o usuário Sankhya possui permissão de acesso no OrderFlow.
    Regra:
    1. Verifica se o usuário (CODUSU) está na lista de usuários permitidos e ativo.
    2. Caso contrário, verifica se o grupo do usuário (CODGRUPO) está na lista de grupos permitidos e ativo.
    Retorna: (permitido: bool, is_admin: bool, motivo: str)
    """
    conn = get_db()
    cursor = conn.cursor()

    # 1. Checa se o usuário individual está explicitamente liberado
    if codusu:
        cursor.execute("""
            SELECT id, is_admin, ativo 
            FROM orderflow_usuarios_permitidos 
            WHERE tenant_id = ? AND codusu = ?
        """, (tenant_id, codusu))
        u = cursor.fetchone()
        if u:
            conn.close()
            if u["ativo"] == 1:
                return True, (u["is_admin"] == 1), "Acesso concedido por permissão individual de usuário."
            else:
                return False, False, "O acesso deste usuário foi desativado no OrderFlow."

    # 2. Checa se o grupo do usuário está liberado
    if codgrupo:
        cursor.execute("""
            SELECT id, nomegrupo, ativo 
            FROM orderflow_grupos_permitidos 
            WHERE tenant_id = ? AND codgrupo = ?
        """, (tenant_id, codgrupo))
        g = cursor.fetchone()
        if g:
            conn.close()
            if g["ativo"] == 1:
                return True, False, f"Acesso concedido pelo grupo {g['nomegrupo']}."
            else:
                return False, False, f"O grupo '{g['nomegrupo']}' está desativado no OrderFlow."

    conn.close()
    return False, False, "Usuário não possui permissão de acesso ao OrderFlow. Solicite liberação ao Super Admin."


# ==========================================
# CRUD DE GRUPOS PERMITIDOS
# ==========================================

def listar_grupos_permitidos(tenant_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, tenant_id, codgrupo, nomegrupo, ativo, criado_em
        FROM orderflow_grupos_permitidos
        WHERE tenant_id = ?
        ORDER BY codgrupo ASC
    """, (tenant_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def adicionar_ou_atualizar_grupo_permitido(tenant_id, codgrupo, nomegrupo=""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO orderflow_grupos_permitidos (tenant_id, codgrupo, nomegrupo, ativo)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(tenant_id, codgrupo) DO UPDATE SET
            nomegrupo = excluded.nomegrupo,
            ativo = 1
    """, (tenant_id, int(codgrupo), nomegrupo))
    conn.commit()
    conn.close()
    return True


def alternar_status_grupo_permitido(tenant_id, id_grupo):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orderflow_grupos_permitidos
        SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END
        WHERE tenant_id = ? AND id = ?
    """, (tenant_id, id_grupo))
    conn.commit()
    conn.close()
    return True


def excluir_grupo_permitido(tenant_id, id_grupo):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM orderflow_grupos_permitidos
        WHERE tenant_id = ? AND id = ?
    """, (tenant_id, id_grupo))
    conn.commit()
    conn.close()
    return True


# ==========================================
# CRUD DE USUÁRIOS PERMITIDOS
# ==========================================

def listar_usuarios_permitidos(tenant_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, tenant_id, codusu, nomeusu, nomevend, is_admin, ativo, criado_em
        FROM orderflow_usuarios_permitidos
        WHERE tenant_id = ?
        ORDER BY nomeusu ASC
    """, (tenant_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def adicionar_ou_atualizar_usuario_permitido(tenant_id, codusu, nomeusu, nomevend="-", is_admin=0):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO orderflow_usuarios_permitidos (tenant_id, codusu, nomeusu, nomevend, is_admin, ativo)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(tenant_id, codusu) DO UPDATE SET
            nomeusu = excluded.nomeusu,
            nomevend = excluded.nomevend,
            is_admin = excluded.is_admin,
            ativo = 1
    """, (tenant_id, int(codusu), nomeusu.strip(), nomevend.strip() if nomevend else "-", int(is_admin)))
    conn.commit()
    conn.close()
    return True


def alternar_status_usuario_permitido(tenant_id, id_usuario):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orderflow_usuarios_permitidos
        SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END
        WHERE tenant_id = ? AND id = ?
    """, (tenant_id, id_usuario))
    conn.commit()
    conn.close()
    return True


def alternar_perfil_admin_usuario(tenant_id, id_usuario):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orderflow_usuarios_permitidos
        SET is_admin = CASE WHEN is_admin = 1 THEN 0 ELSE 1 END
        WHERE tenant_id = ? AND id = ?
    """, (tenant_id, id_usuario))
    conn.commit()
    conn.close()
    return True


def excluir_usuario_permitido(tenant_id, id_usuario):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM orderflow_usuarios_permitidos
        WHERE tenant_id = ? AND id = ?
    """, (tenant_id, id_usuario))
    conn.commit()
    conn.close()
    return True


def obter_modulos_habilitados_tenant(tenant_id):
    """
    Retorna a lista de chaves de módulos/menus liberados para o tenant.
    Se não configurado ou nulo, retorna todos por padrão.
    """
    import json
    todos_modulos = ["inicio", "portal_vendas", "consulta_produtos", "promocoes", "bi", "configuracoes", "acessos"]
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT modulos_habilitados FROM tenants WHERE tenant_id = ?", (tenant_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row["modulos_habilitados"]:
            mods = json.loads(row["modulos_habilitados"])
            if isinstance(mods, list) and len(mods) > 0:
                return mods
    except Exception:
        pass
    return todos_modulos


def obter_versao_tenant(tenant_id):
    """
    Retorna os dados da versão e ambiente atualmente instalados/atribuídos para o tenant.
    Retorna dict com versao_atual, branch, titulo, ambiente, nome_empresa, status_versao.
    """
    default_version = os.getenv("ORDERFLOW_VERSION", "v1.0.1")
    default_env = os.getenv("AMBIENTE")
    if not default_env:
        if str(tenant_id).endswith("_base"):
            default_env = "base"
        elif str(tenant_id).endswith("_prod") or str(tenant_id) == "producao":
            default_env = "producao"
        else:
            default_env = "teste"

    resultado = {
        "versao_atual": default_version,
        "branch": "main",
        "titulo": "Versão de Produção",
        "ambiente": default_env,
        "nome_empresa": "",
        "status_versao": "estavel"
    }
    if not tenant_id:
        return resultado

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.versao_atual, t.ambiente, t.nome_empresa,
                   v.branch, v.titulo, v.tipo, v.status as status_versao
            FROM tenants t
            LEFT JOIN sistema_versoes v ON v.tag_versao = t.versao_atual
            WHERE t.tenant_id = ?
        """, (tenant_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            if row["versao_atual"]:
                resultado["versao_atual"] = row["versao_atual"]
            if row["ambiente"]:
                resultado["ambiente"] = row["ambiente"]
            if row["nome_empresa"]:
                resultado["nome_empresa"] = row["nome_empresa"]
            if row["branch"]:
                resultado["branch"] = row["branch"]
            if row["titulo"]:
                resultado["titulo"] = row["titulo"]
            if row["status_versao"]:
                resultado["status_versao"] = row["status_versao"]
    except Exception as e:
        print(f"[OrderFlow Auth] Erro ao consultar versão do tenant: {e}")

    return resultado


def registrar_sessao_ativa(tenant_id, codusu, nome_usuario, session_token, ip_address=""):
    """
    Registra ou atualiza sessão ativa diretamente no banco compartilhado.
    Garante que cada usuário (codusu) mantenha apenas 1 licença ativa.
    """
    if not tenant_id or not session_token:
        return False
    try:
        conn = get_db()
        cursor = conn.cursor()

        # Limpa sessões expiradas (mais de 3 minutos sem ping)
        try:
            cursor.execute("DELETE FROM active_sessions WHERE datetime(ultimo_ping) < datetime('now', '-3 minutes')")
        except Exception:
            pass

        # 1. Se já existe uma sessão para este mesmo usuário (codusu) no tenant, atualiza-a
        if codusu:
            cursor.execute("SELECT id FROM active_sessions WHERE tenant_id = ? AND codusu = ?", (tenant_id, codusu))
            user_row = cursor.fetchone()
            if user_row:
                cursor.execute("""
                    UPDATE active_sessions 
                    SET session_token = ?, ip_address = ?, ultimo_ping = CURRENT_TIMESTAMP, nome_usuario = ?
                    WHERE id = ?
                """, (str(session_token), ip_address, nome_usuario or "Usuário", user_row["id"]))
                conn.commit()
                conn.close()
                return True

        # 2. Se já existe pelo session_token, atualiza
        cursor.execute("SELECT id FROM active_sessions WHERE session_token = ?", (str(session_token),))
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE active_sessions 
                SET ultimo_ping = CURRENT_TIMESTAMP, ip_address = ?
                WHERE session_token = ?
            """, (ip_address, str(session_token)))
        else:
            cursor.execute("""
                INSERT INTO active_sessions (tenant_id, codusu, nome_usuario, session_token, ip_address, ultimo_ping)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (tenant_id, codusu or 1, nome_usuario or "Usuário", str(session_token), ip_address))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Auth] Erro ao registrar sessão no DB: {e}")
        return False


def listar_sessoes_ativas_tenant(tenant_id):
    """
    Retorna a lista de sessões de usuários ativas do tenant com ping nos últimos 3 minutos.
    """
    conn = get_db()
    cursor = conn.cursor()
    # Limpa sessões expiradas (mais de 3 minutos sem ping)
    try:
        cursor.execute("DELETE FROM active_sessions WHERE datetime(ultimo_ping) < datetime('now', '-3 minutes')")
        conn.commit()
    except Exception:
        pass

    cursor.execute("""
        SELECT id, tenant_id, codusu, nome_usuario, session_token, ip_address, ultimo_ping
        FROM active_sessions
        WHERE tenant_id = ?
        ORDER BY ultimo_ping DESC
    """, (tenant_id,))
    rows = cursor.fetchall()
    sessoes = [dict(r) for r in rows]
    conn.close()
    return sessoes


def encerrar_sessao_ativa(tenant_id, session_id=None, session_token=None):
    """
    Encerra/mata uma sessão ativa do tenant no banco de dados.
    Garante a exclusão tanto por token UUID quanto por id da sessão,
    e notifica o painel admin caso aplicável.
    """
    # Suporta chamada legada onde alvo era passado no 2º argumento
    if session_id and not session_token and isinstance(session_id, str) and "-" in session_id:
        session_token = session_id
        session_id = None

    conn = get_db()
    cursor = conn.cursor()

    # 1. Deleta por session_token se fornecido
    if session_token:
        cursor.execute("DELETE FROM active_sessions WHERE session_token = ?", (str(session_token),))

    # 2. Deleta por id se fornecido
    if session_id:
        cursor.execute("DELETE FROM active_sessions WHERE id = ?", (session_id,))

    # 3. Deleta com match flexível garantindo tenant
    if tenant_id and (session_id or session_token):
        cursor.execute("""
            DELETE FROM active_sessions
            WHERE LOWER(tenant_id) = LOWER(?) AND (id = ? OR session_token = ?)
        """, (tenant_id, session_id, str(session_token or session_id)))

    conn.commit()
    conn.close()

    # Notifica também o admin_panel se houver token
    if session_token:
        try:
            import requests
            from config import Config
            requests.post(
                f"{Config.ADMIN_PANEL_URL}/api/v1/unregister-session",
                json={"session_token": str(session_token)},
                timeout=1
            )
        except Exception:
            pass

    return True

