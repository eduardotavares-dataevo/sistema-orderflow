import os
import secrets
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from models import init_db, get_db_connection, db_adapter

app = Flask(__name__, template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
# Nunca use uma chave padrão publicada. Configure ADMIN_SECRET_KEY para que as
# sessões sobrevivam aos reinícios em produção.
app.secret_key = os.getenv("ADMIN_SECRET_KEY") or secrets.token_urlsafe(48)

# Configurações de Sessão e Cookie Dedicado para o Painel Admin (Evita conflito com o OrderFlow / Tenants no mesmo domínio/IP)
from datetime import timedelta
app.config["SESSION_COOKIE_NAME"] = "orderflow_admin_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# Inicializa banco de dados
init_db()

# Decoradores e Gestão de Permissões
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "admin_user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "admin_user_id" not in session:
            return redirect(url_for("login"))
        if session.get("admin_perfil") != "admin":
            return jsonify({"erro": "Acesso restrito. Somente administradores principais."}), 403
        return f(*args, **kwargs)
    return decorated_function

def obter_permissoes_usuario(user_id):
    """Retorna o dicionário de permissões do usuário admin. Se admin, todas True."""
    if not user_id:
        return {}

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, perfil, permissoes FROM admin_users WHERE id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()

    if not u:
        return {}

    if u["perfil"] == "admin":
        return {
            "tenants": {"ver": True, "editar": True, "excluir": True, "acoes": True},
            "clientes": {"ver": True, "editar": True, "excluir": True},
            "projetos": {"ver": True, "editar": True, "excluir": True, "duplicar": True},
            "versoes": {"ver": True, "editar": True, "excluir": True, "deploy": True},
            "usuarios": {"ver": True, "gerenciar": True}
        }

    import json
    if u["permissoes"]:
        try:
            return json.loads(u["permissoes"])
        except Exception:
            pass

    return {
        "tenants": {"ver": True, "editar": False, "excluir": False, "acoes": False},
        "clientes": {"ver": True, "editar": False, "excluir": False},
        "projetos": {"ver": True, "editar": False, "excluir": False, "duplicar": False},
        "versoes": {"ver": True, "editar": False, "excluir": False, "deploy": False},
        "usuarios": {"ver": False, "gerenciar": False}
    }

def usuario_tem_permissao(user_id, modulo, acao="ver"):
    """Verifica se o usuário possui permissão para executar ação no módulo."""
    if not user_id:
        return False
    perms = obter_permissoes_usuario(user_id)
    return bool(perms.get(modulo, {}).get(acao, False))

def permissao_requerida(modulo, acao="ver"):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "admin_user_id" not in session:
                return redirect(url_for("login"))
            user_id = session.get("admin_user_id")
            if session.get("admin_perfil") == "admin":
                return f(*args, **kwargs)
            if not usuario_tem_permissao(user_id, modulo, acao):
                msg = f"Acesso negado. Você não possui permissão para '{acao}' no módulo de {modulo}."
                if request.is_json:
                    return jsonify({"erro": msg}), 403
                return redirect(url_for("dashboard", msg=msg, tipo="erro"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.context_processor
def inject_permissoes_context():
    user_id = session.get("admin_user_id")
    is_adm = session.get("admin_perfil") == "admin"
    perms = obter_permissoes_usuario(user_id) if user_id else {}

    def tem_perm(modulo, acao="ver"):
        if is_adm:
            return True
        return bool(perms.get(modulo, {}).get(acao, False))

    return {
        "tem_permissao": tem_perm,
        "is_admin": is_adm,
        "usuario_permissoes": perms
    }

@app.route("/healthz")
@app.route("/health")
def health_check():
    return jsonify({"status": "ok", "app": "orderflow-admin"}), 200

@app.route("/login", methods=["GET", "POST"])
def login():
    if "admin_user_id" in session and request.method == "GET":
        return redirect(url_for("projetos"))

    erro = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admin_users WHERE username = ? AND status = 'ativo'", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session.permanent = True
            session["admin_user_id"] = user["id"]
            session["admin_username"] = user["username"]
            session["admin_user_nome"] = user["nome"]
            session["admin_perfil"] = user["perfil"]
            return redirect(url_for("projetos"))
        else:
            erro = "Usuário ou senha inválidos."

    return render_template("login.html", erro=erro)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ==========================================
# 1. ENTRADA PRINCIPAL & GESTÃO DE INSTÂNCIAS (TENANTS)
# ==========================================
@app.route("/")
@login_required
def index():
    user_id = session.get("admin_user_id")
    if usuario_tem_permissao(user_id, "projetos", "ver"):
        return redirect(url_for("projetos"))
    elif usuario_tem_permissao(user_id, "tenants", "ver"):
        return redirect(url_for("dashboard"))
    elif usuario_tem_permissao(user_id, "clientes", "ver"):
        return redirect(url_for("clientes"))
    elif usuario_tem_permissao(user_id, "versoes", "ver"):
        return redirect(url_for("versoes"))
    elif usuario_tem_permissao(user_id, "usuarios", "ver"):
        return redirect(url_for("usuarios"))
    else:
        return "Acesso restrito. Sua conta não possui permissão para acessar os módulos administrativos.", 403


@app.route("/instancias")
@login_required
def dashboard():
    user_id = session.get("admin_user_id")
    # Se o usuário não tiver permissão de visualizar tenants, redireciona para a primeira tela permitida
    if not usuario_tem_permissao(user_id, "tenants", "ver"):
        if usuario_tem_permissao(user_id, "projetos", "ver"):
            return redirect(url_for("projetos"))
        elif usuario_tem_permissao(user_id, "clientes", "ver"):
            return redirect(url_for("clientes"))
        elif usuario_tem_permissao(user_id, "versoes", "ver"):
            return redirect(url_for("versoes"))
        elif usuario_tem_permissao(user_id, "usuarios", "ver"):
            return redirect(url_for("usuarios"))
        else:
            return "Acesso restrito. Sua conta não possui permissão para acessar os módulos administrativos.", 403

    projeto_id_filtro = request.args.get("projeto_id")
    if projeto_id_filtro and projeto_id_filtro.isdigit():
        projeto_id_filtro = int(projeto_id_filtro)
    else:
        projeto_id_filtro = None

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Limpa sessões expiradas
    cleanup_stale_sessions(cursor)
    conn.commit()

    projeto_selecionado = None
    if projeto_id_filtro:
        cursor.execute("""
            SELECT p.*, c.nome_fantasia as cliente_nome 
            FROM projetos p
            LEFT JOIN clientes c ON p.cliente_id = c.id
            WHERE p.id = ?
        """, (projeto_id_filtro,))
        row_proj = cursor.fetchone()
        if row_proj:
            projeto_selecionado = dict(row_proj)

    # Consulta tenants trazendo dados do cliente, do projeto, último contrato de licenças e contagem de sessões ativas
    query_tenants = """
        SELECT t.*, c.nome_fantasia as cliente_nome,
               p.nome_projeto, p.codigo_slug as projeto_slug, p.branch_git as projeto_branch,
               (SELECT h.qtd_licencas 
                FROM contrato_historico h 
                WHERE h.cliente_id = t.cliente_id 
                ORDER BY h.data_inicio DESC, h.id DESC 
                LIMIT 1) as cliente_licencas_historico,
               (SELECT COUNT(*) FROM active_sessions s WHERE s.tenant_id = t.tenant_id) as sessoes_ativas
        FROM tenants t
        LEFT JOIN clientes c ON t.cliente_id = c.id
        LEFT JOIN projetos p ON t.projeto_id = p.id
    """
    params_tenants = []
    if projeto_id_filtro:
        query_tenants += " WHERE t.projeto_id = ? "
        params_tenants.append(projeto_id_filtro)

    query_tenants += " ORDER BY t.criado_em DESC"
    cursor.execute(query_tenants, tuple(params_tenants))
    tenants_raw = cursor.fetchall()
    tenants = []
    import json
    for row in tenants_raw:
        item = dict(row)
        # Sincroniza a quantidade de licenças contratadas a partir do último histórico de contrato do cliente
        if item.get("cliente_licencas_historico") is not None:
            item["max_licencas"] = item["cliente_licencas_historico"]

        if item.get("modulos_habilitados"):
            try:
                item["modulos_lista"] = json.loads(item["modulos_habilitados"])
            except Exception:
                item["modulos_lista"] = ["inicio", "portal_vendas", "consulta_produtos", "promocoes", "bi", "configuracoes", "acessos"]
        else:
            item["modulos_lista"] = ["inicio", "portal_vendas", "consulta_produtos", "promocoes", "bi", "configuracoes", "acessos"]
        tenants.append(item)

    cursor.execute("""
        SELECT c.*,
               (SELECT h.qtd_licencas 
                FROM contrato_historico h 
                WHERE h.cliente_id = c.id 
                ORDER BY h.data_inicio DESC, h.id DESC 
                LIMIT 1) as ultimo_contrato_licencas
        FROM clientes c 
        WHERE c.status = 'ativo' 
        ORDER BY c.nome_fantasia ASC
    """)
    clientes_raw = cursor.fetchall()
    clientes = [dict(c) for c in clientes_raw]

    cursor.execute("SELECT id, nome_projeto, codigo_slug, branch_git FROM projetos WHERE status = 'ativo' ORDER BY nome_projeto ASC")
    projetos_raw = cursor.fetchall()
    projetos = [dict(p) for p in projetos_raw]

    cursor.execute("SELECT id, tag_versao, branch, titulo FROM sistema_versoes ORDER BY id DESC")
    versoes_raw = cursor.fetchall()
    versoes = [dict(v) for v in versoes_raw]

    cursor.execute("SELECT COUNT(*) FROM active_sessions")
    total_active_sessions = cursor.fetchone()[0]
    conn.close()

    return render_template("dashboard.html", tenants=tenants, clientes=clientes, projetos=projetos, versoes=versoes, total_active_sessions=total_active_sessions, projeto_selecionado=projeto_selecionado)

@app.route("/admin/tenant/matar-todas-sessoes", methods=["POST"])
@login_required
@permissao_requerida("tenants", "acoes")
def matar_todas_sessoes_tenant():
    tenant_id = request.form.get("tenant_id", "").strip()
    if not tenant_id:
        return "Tenant ID obrigatório", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM active_sessions WHERE tenant_id = ?", (tenant_id,))
    total = cursor.fetchone()[0]

    cursor.execute("DELETE FROM active_sessions WHERE tenant_id = ?", (tenant_id,))
    conn.commit()
    conn.close()

    return redirect(url_for("dashboard", msg=f"Todas as {total} sessão(ões) ativa(s) do tenant '{tenant_id}' foram desconectadas com sucesso!", tipo="alterar"))

@app.route("/admin/tenant/salvar", methods=["POST"])
@login_required
@permissao_requerida("tenants", "editar")
def salvar_tenant():
    t_id = request.form.get("id")
    tenant_id = request.form.get("tenant_id", "").strip().lower()
    nome_empresa = request.form.get("nome_empresa", "").strip()
    cliente_id = request.form.get("cliente_id") or None
    sankhya_url = request.form.get("sankhya_url", "").strip()
    sankhya_appkey = request.form.get("sankhya_appkey", "").strip()
    ambiente = request.form.get("ambiente", "teste")
    porta_form = request.form.get("porta")
    ip_externo = request.form.get("ip_externo", "").strip()
    max_licencas = int(request.form.get("max_licencas", 5))
    validade_licenca = request.form.get("validade_licenca", "2027-12-31")
    status = request.form.get("status", "ativo")
    superadmin_user = (request.form.get("superadmin_user") or "admin").strip()
    superadmin_password = (request.form.get("superadmin_password") or "").strip()

    if not tenant_id or not nome_empresa or not sankhya_url:
        return "Campos obrigatórios ausentes", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # O número de licenças contratadas é sempre obtido do último histórico de contrato do cliente vinculado
    if cliente_id:
        cursor.execute("""
            SELECT qtd_licencas 
            FROM contrato_historico 
            WHERE cliente_id = ? 
            ORDER BY data_inicio DESC, id DESC 
            LIMIT 1
        """, (cliente_id,))
        hist_row = cursor.fetchone()
        if hist_row and hist_row[0] is not None:
            max_licencas = int(hist_row[0])
        else:
            max_licencas = int(request.form.get("max_licencas", 5))
    else:
        max_licencas = int(request.form.get("max_licencas", 5))

    # Cálculo dinâmico da Porta por Ambiente:
    # Base -> 5010+ | Produção -> 6010+ | Teste -> 7010+
    if porta_form and porta_form.isdigit():
        porta = int(porta_form)
    elif t_id:
        # Na edição, se o campo de porta estiver vazio, preserva a porta já cadastrada
        cursor.execute("SELECT porta FROM tenants WHERE id = ?", (t_id,))
        porta_existente = cursor.fetchone()
        porta = porta_existente[0] if porta_existente and porta_existente[0] else 5010
    else:
        if ambiente == "base":
            min_porta = 5010
        elif ambiente == "producao":
            min_porta = 6010
        else:
            min_porta = 7010
        cursor.execute("SELECT MAX(porta) FROM tenants WHERE ambiente = ?", (ambiente,))
        max_p = cursor.fetchone()[0]
        porta = (max_p + 1) if max_p and max_p >= min_porta else min_porta

    # Tratamento de senha
    if not superadmin_password:
        if t_id:
            cursor.execute("SELECT superadmin_password, superadmin_password_hash FROM tenants WHERE id = ?", (t_id,))
            t_curr = cursor.fetchone()
            if t_curr and t_curr[0]:
                superadmin_password = t_curr[0]
                superadmin_hash = t_curr[1]
            else:
                from models import gerar_senha_forte
                superadmin_password = gerar_senha_forte(14)
                superadmin_hash = generate_password_hash(superadmin_password)
        else:
            from models import gerar_senha_forte
            superadmin_password = gerar_senha_forte(14)
            superadmin_hash = generate_password_hash(superadmin_password)
    else:
        superadmin_hash = generate_password_hash(superadmin_password)

    projeto_id = request.form.get("projeto_id") or None
    versao_atual = request.form.get("versao_atual", "v1.0.5").strip()

    # Menus / Módulos Habilitados no OrderFlow (Gerenciador de Menus)
    modulos_list = request.form.getlist("modulos[]")
    import json
    if t_id:
        # Na edição, salva a lista exata de módulos selecionados
        modulos_json = json.dumps(modulos_list)
    else:
        # Se novo cadastro e nada marcado, mantém todos os módulos por padrão
        modulos_json = json.dumps(modulos_list) if modulos_list else json.dumps([
            "inicio", "portal_vendas", "consulta_produtos", "promocoes", "bi", "configuracoes", "acessos"
        ])

    try:
        if t_id:
            # Recupera tenant_id anterior para verificar se mudou
            cursor.execute("SELECT tenant_id FROM tenants WHERE id = ?", (t_id,))
            t_ant = cursor.fetchone()
            old_tenant_id = t_ant[0] if t_ant else None

            # Verifica unicidade de tenant_id com outras instâncias
            cursor.execute("SELECT id FROM tenants WHERE tenant_id = ? AND id != ?", (tenant_id, t_id))
            if cursor.fetchone():
                conn.close()
                return f"Erro: O Tenant ID '{tenant_id}' já pertence a outra instância cadastrada.", 400

            # Edição de Tenant
            cursor.execute("""
                UPDATE tenants
                SET tenant_id = ?, cliente_id = ?, projeto_id = ?, versao_atual = ?, nome_empresa = ?, sankhya_url = ?, sankhya_appkey = ?, ambiente = ?, porta = ?, ip_externo = ?, max_licencas = ?, validade_licenca = ?, status = ?,
                    superadmin_user = ?, superadmin_password = ?, superadmin_password_hash = ?, modulos_habilitados = ?
                WHERE id = ?
            """, (tenant_id, cliente_id, projeto_id, versao_atual, nome_empresa, sankhya_url, sankhya_appkey, ambiente, porta, ip_externo, max_licencas, validade_licenca, status,
                  superadmin_user, superadmin_password, superadmin_hash, modulos_json, t_id))

            # Se o tenant_id mudou, atualiza referências nas sessões ativas
            if old_tenant_id and old_tenant_id != tenant_id:
                cursor.execute("UPDATE active_sessions SET tenant_id = ? WHERE tenant_id = ?", (tenant_id, old_tenant_id))

            flash_msg = f"Instância '{nome_empresa}' atualizada com sucesso!"
            action_type = "alterar"
        else:
            # Criação de Novo Tenant
            cursor.execute("SELECT id FROM tenants WHERE tenant_id = ?", (tenant_id,))
            if cursor.fetchone():
                conn.close()
                return f"Erro: O Tenant ID '{tenant_id}' já está cadastrado.", 400

            cursor.execute("""
                INSERT INTO tenants (cliente_id, projeto_id, versao_atual, tenant_id, nome_empresa, sankhya_url, sankhya_appkey, ambiente, porta, ip_externo, max_licencas, status, validade_licenca,
                                     superadmin_user, superadmin_password, superadmin_password_hash, modulos_habilitados)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (cliente_id, projeto_id, versao_atual, tenant_id, nome_empresa, sankhya_url, sankhya_appkey, ambiente, porta, ip_externo, max_licencas, status, validade_licenca,
                  superadmin_user, superadmin_password, superadmin_hash, modulos_json))
            flash_msg = f"Instância '{nome_empresa}' cadastrada com sucesso!"
            action_type = "inserir"
        conn.commit()
    except Exception as e:
        conn.close()
        return f"Erro ao salvar tenant: {e}", 400

    conn.close()
    if projeto_id:
        return redirect(url_for("dashboard", projeto_id=projeto_id, msg=flash_msg, tipo=action_type))
    return redirect(url_for("dashboard", msg=flash_msg, tipo=action_type))

@app.route("/admin/tenant/deletar/<int:id>", methods=["POST"])
@login_required
@permissao_requerida("tenants", "excluir")
def deletar_tenant(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tenant_id FROM tenants WHERE id = ?", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return redirect(url_for("dashboard", msg="Instância não encontrada.", tipo="erro"))

    t_id = row["tenant_id"]
    cursor.execute("DELETE FROM active_sessions WHERE tenant_id = ?", (t_id,))
    cursor.execute("DELETE FROM tenant_deploys_historico WHERE tenant_id = ?", (t_id,))
    cursor.execute("DELETE FROM tenants WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard", msg=f"Instância '{t_id}' excluída com sucesso!", tipo="deletar"))


@app.route("/api/v1/tenant-superadmin/<tenant_id>", methods=["GET"])
def api_get_tenant_superadmin(tenant_id):
    """Retorna credenciais do Super Admin e menus habilitados do tenant para o OrderFlow."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT superadmin_user, superadmin_password, superadmin_password_hash, status, nome_empresa, modulos_habilitados
        FROM tenants 
        WHERE tenant_id = ?
    """, (tenant_id,))
    t = cursor.fetchone()
    conn.close()
    if not t:
        return jsonify({"status": "0", "mensagem": "Tenant não localizado."}), 404
    
    import json
    modulos = []
    if t["modulos_habilitados"]:
        try:
            modulos = json.loads(t["modulos_habilitados"])
        except Exception:
            modulos = []

    return jsonify({
        "status": "1",
        "tenant_id": tenant_id,
        "superadmin_user": t["superadmin_user"] or "admin",
        "superadmin_password": t["superadmin_password"],
        "superadmin_password_hash": t["superadmin_password_hash"],
        "tenant_status": t["status"],
        "nome_empresa": t["nome_empresa"],
        "modulos_habilitados": modulos
    })

# ==========================================
# 2. GESTÃO DE CLIENTES & HISTÓRICO DE CONTRATOS
# ==========================================
@app.route("/clientes")
@login_required
@permissao_requerida("clientes", "ver")
def clientes():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.*, COUNT(t.id) as total_tenants,
               (SELECT COUNT(*) FROM contrato_historico h WHERE h.cliente_id = c.id) as total_contratos
        FROM clientes c
        LEFT JOIN tenants t ON t.cliente_id = c.id
        GROUP BY c.id
        ORDER BY c.razao_social ASC
    """)
    clientes_raw = cursor.fetchall()
    clientes_list = [dict(c) for c in clientes_raw]
    conn.close()

    return render_template("clientes.html", clientes=clientes_list)

@app.route("/admin/cliente/salvar", methods=["POST"])
@login_required
@permissao_requerida("clientes", "editar")
def salvar_cliente():
    cli_id = request.form.get("cliente_id")
    razao_social = request.form.get("razao_social", "").strip()
    nome_fantasia = request.form.get("nome_fantasia", "").strip()
    cnpj = request.form.get("cnpj", "").strip()
    contato_nome = request.form.get("contato_nome", "").strip()
    telefone = request.form.get("telefone", "").strip()
    email = request.form.get("email", "").strip()
    
    # Novos campos de gestão financeira/contrato
    valor_saas = float(request.form.get("valor_saas", 0) or 0)
    valor_mensalidade = float(request.form.get("valor_mensalidade", 0) or 0)
    valor_por_licenca = float(request.form.get("valor_por_licenca", 0) or 0)
    dia_vencimento = int(request.form.get("dia_vencimento", 10) or 10)
    data_inicio_contrato = request.form.get("data_inicio_contrato", "").strip() or None
    observacoes = request.form.get("observacoes", "").strip()
    status = request.form.get("status", "ativo")
    reg_historico = request.form.get("registrar_historico") == "1"

    if not razao_social or not nome_fantasia:
        return "Razão Social e Nome Fantasia são obrigatórios", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if cli_id:
            cursor.execute("""
                UPDATE clientes
                SET razao_social = ?, nome_fantasia = ?, cnpj = ?, contato_nome = ?, telefone = ?, email = ?, 
                    valor_saas = ?, valor_mensalidade = ?, valor_por_licenca = ?, dia_vencimento = ?, data_inicio_contrato = ?, observacoes = ?, status = ?
                WHERE id = ?
            """, (razao_social, nome_fantasia, cnpj, contato_nome, telefone, email, 
                  valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, data_inicio_contrato, observacoes, status, cli_id))
            flash_msg = f"Cliente '{nome_fantasia}' atualizado com sucesso!"
            action_type = "alterar"
            target_cliente_id = int(cli_id)
        else:
            cursor.execute("""
                INSERT INTO clientes (razao_social, nome_fantasia, cnpj, contato_nome, telefone, email, 
                                      valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, data_inicio_contrato, observacoes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (razao_social, nome_fantasia, cnpj, contato_nome, telefone, email, 
                  valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, data_inicio_contrato, observacoes, status))
            flash_msg = f"Cliente '{nome_fantasia}' cadastrado com sucesso!"
            action_type = "inserir"
            target_cliente_id = cursor.lastrowid
            reg_historico = True # Sempre registra histórico no primeiro cadastro

        # Se solicitado ou novo cadastro, salva a versão no histórico de contratos
        if reg_historico and target_cliente_id:
            qtd_lic = int(request.form.get("qtd_licencas", 5) or 5)
            dt_ini = data_inicio_contrato or "2026-01-01"
            cursor.execute("""
                INSERT INTO contrato_historico (cliente_id, data_inicio, qtd_licencas, valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, observacao, criado_por)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (target_cliente_id, dt_ini, qtd_lic, valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, observacoes, session.get("admin_username", "admin")))

        conn.commit()
    except Exception as e:
        conn.close()
        return f"Erro ao salvar cliente: {e}", 400

    conn.close()
    return redirect(url_for("clientes", msg=flash_msg, tipo=action_type))

@app.route("/admin/cliente/deletar/<int:id>", methods=["POST"])
@login_required
@permissao_requerida("clientes", "excluir")
def deletar_cliente(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tenants WHERE cliente_id = ?", (id,))
    total_tenants = cursor.fetchone()[0]
    if total_tenants > 0:
        conn.close()
        return redirect(url_for("clientes", msg="Não é possível excluir este cliente pois existem instâncias vinculadas a ele.", tipo="erro"))

    cursor.execute("DELETE FROM contrato_historico WHERE cliente_id = ?", (id,))
    cursor.execute("DELETE FROM clientes WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("clientes", msg="Cliente excluído com sucesso!", tipo="deletar"))

@app.route("/admin/cliente/<int:cliente_id>/historico")
@login_required
def historico_cliente(cliente_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,))
    cliente = cursor.fetchone()
    if not cliente:
        conn.close()
        return jsonify({"erro": "Cliente não encontrado"}), 404

    cursor.execute("""
        SELECT * FROM contrato_historico
        WHERE cliente_id = ?
        ORDER BY data_inicio DESC, id DESC
    """, (cliente_id,))
    historico_raw = cursor.fetchall()
    historico = [dict(h) for h in historico_raw]
    conn.close()

    return jsonify({
        "cliente": dict(cliente),
        "historico": historico
    })

@app.route("/admin/historico/salvar", methods=["POST"])
@admin_required
def salvar_historico():
    h_id = request.form.get("historico_id")
    cliente_id = request.form.get("cliente_id")
    data_inicio = request.form.get("data_inicio")
    qtd_licencas = int(request.form.get("qtd_licencas", 1))
    valor_saas = float(request.form.get("valor_saas", 0.0) or 0.0)
    valor_mensalidade = float(request.form.get("valor_mensalidade", 0.0) or 0.0)
    valor_por_licenca = float(request.form.get("valor_por_licenca", 0.0) or 0.0)
    dia_vencimento = int(request.form.get("dia_vencimento", 10))
    observacao = request.form.get("observacao", "").strip()

    if not cliente_id or not data_inicio:
        return jsonify({"erro": "Campos obrigatórios ausentes."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    if h_id:
        cursor.execute("""
            UPDATE contrato_historico
            SET data_inicio = ?, qtd_licencas = ?, valor_saas = ?, valor_mensalidade = ?, valor_por_licenca = ?, dia_vencimento = ?, observacao = ?
            WHERE id = ? AND cliente_id = ?
        """, (data_inicio, qtd_licencas, valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, observacao, h_id, cliente_id))
    else:
        cursor.execute("""
            INSERT INTO contrato_historico (cliente_id, data_inicio, qtd_licencas, valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, observacao, criado_por)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cliente_id, data_inicio, qtd_licencas, valor_saas, valor_mensalidade, valor_por_licenca, dia_vencimento, observacao, session.get("admin_username", "admin")))

    conn.commit()
    conn.close()
    return jsonify({"sucesso": True, "mensagem": "Registro de histórico salvo com sucesso!"})

@app.route("/admin/historico/deletar/<int:id>", methods=["POST"])
@admin_required
def deletar_historico(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contrato_historico WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return jsonify({"sucesso": True, "mensagem": "Registro de histórico excluído com sucesso!"})

# ==========================================
# 3. GESTÃO DE USUÁRIOS E PERMISSÕES
# ==========================================
@app.route("/usuarios")
@login_required
@permissao_requerida("usuarios", "ver")
def usuarios():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, nome, perfil, status, criado_em, permissoes FROM admin_users ORDER BY criado_em DESC")
    usuarios_raw = cursor.fetchall()
    usuarios_list = [dict(u) for u in usuarios_raw]
    conn.close()

    return render_template("usuarios.html", usuarios=usuarios_list)

@app.route("/admin/usuario/permissoes/salvar", methods=["POST"])
@login_required
@permissao_requerida("usuarios", "gerenciar")
def salvar_permissoes_usuario():
    user_id = request.form.get("user_id")
    if not user_id:
        return "ID do usuário obrigatório", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, perfil FROM admin_users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        conn.close()
        return "Usuário não localizado", 404

    if user["perfil"] == "admin":
        conn.close()
        return "Usuários com perfil Administrador possuem acesso irrestrito.", 400

    import json
    perms = {
        "tenants": {
            "ver": request.form.get("perm_tenants_ver") == "1",
            "editar": request.form.get("perm_tenants_editar") == "1",
            "excluir": request.form.get("perm_tenants_excluir") == "1",
            "acoes": request.form.get("perm_tenants_acoes") == "1"
        },
        "clientes": {
            "ver": request.form.get("perm_clientes_ver") == "1",
            "editar": request.form.get("perm_clientes_editar") == "1",
            "excluir": request.form.get("perm_clientes_excluir") == "1"
        },
        "versoes": {
            "ver": request.form.get("perm_versoes_ver") == "1",
            "editar": request.form.get("perm_versoes_editar") == "1",
            "excluir": request.form.get("perm_versoes_excluir") == "1",
            "deploy": request.form.get("perm_versoes_deploy") == "1"
        },
        "usuarios": {
            "ver": request.form.get("perm_usuarios_ver") == "1",
            "gerenciar": request.form.get("perm_usuarios_gerenciar") == "1"
        }
    }

    cursor.execute("UPDATE admin_users SET permissoes = ? WHERE id = ?", (json.dumps(perms), user_id))
    conn.commit()
    conn.close()

    return redirect(url_for("usuarios", msg=f"Permissões do usuário '{user['username']}' atualizadas com sucesso!", tipo="alterar"))

@app.route("/admin/usuario/novo", methods=["POST"])
@login_required
@permissao_requerida("usuarios", "gerenciar")
def novo_usuario():
    username = request.form.get("username", "").strip().lower()
    nome = request.form.get("nome", "").strip()
    password = request.form.get("password", "").strip()
    perfil = request.form.get("perfil", "restrito").strip()

    if not username or not nome or not password:
        return "Todos os campos são obrigatórios", 400

    password_hash = generate_password_hash(password)

    # Permissões padrão para novos usuários restritos
    import json
    permissoes_padrao = json.dumps({
        "tenants": {"ver": True, "editar": False, "excluir": False, "acoes": False},
        "clientes": {"ver": True, "editar": False, "excluir": False},
        "versoes": {"ver": True, "editar": False, "excluir": False, "deploy": False},
        "usuarios": {"ver": False, "gerenciar": False}
    })

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO admin_users (username, password_hash, nome, perfil, status, permissoes)
            VALUES (?, ?, ?, ?, 'ativo', ?)
        """, (username, password_hash, nome, perfil, permissoes_padrao))
        conn.commit()
    except Exception as e:
        conn.close()
        return f"Erro ao criar usuário: {e}", 400

    conn.close()
    return redirect(url_for("usuarios", msg=f"Usuário '{username}' criado com sucesso!", tipo="inserir"))

@app.route("/admin/usuario/alterar-senha", methods=["POST"])
@login_required
def alterar_senha():
    user_id = request.form.get("user_id")
    nova_senha = request.form.get("nova_senha", "").strip()

    if not user_id or not nova_senha:
        return "Campos obrigatórios ausentes", 400

    # Somente o próprio usuário ou quem tiver gerenciar usuários pode alterar a senha
    user_logado_id = session.get("admin_user_id")
    pode_gerenciar = session.get("admin_perfil") == "admin" or usuario_tem_permissao(user_logado_id, "usuarios", "gerenciar")
    if not pode_gerenciar and int(user_id) != user_logado_id:
        return "Sem permissão para alterar a senha deste usuário", 403

    password_hash = generate_password_hash(nova_senha)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE admin_users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.commit()
    conn.close()

    return redirect(url_for("usuarios", msg="Senha alterada com sucesso!", tipo="alterar"))

@app.route("/admin/usuario/deletar/<int:id>", methods=["POST"])
@login_required
@permissao_requerida("usuarios", "gerenciar")
def deletar_usuario(id):
    if id == session.get("admin_user_id"):
        return "Não é possível deletar seu próprio usuário logado.", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM admin_users WHERE id = ?", (id,))
    u = cursor.fetchone()
    nome = u["username"] if u else f"ID {id}"

    cursor.execute("DELETE FROM admin_users WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("usuarios", msg=f"Usuário '{nome}' deletado com sucesso!", tipo="deletar"))

# ==========================================
# 4. API VALIDATE LICENSE & SESSION HEARTBEAT
# ==========================================
def cleanup_stale_sessions(cursor):
    """Remove sessões ativas sem ping há mais de 3 minutos (180s)."""
    if db_adapter.is_postgres:
        cursor.execute("DELETE FROM active_sessions WHERE ultimo_ping < NOW() - INTERVAL '3 minutes'")
    else:
        cursor.execute("DELETE FROM active_sessions WHERE datetime(ultimo_ping) < datetime('now', '-3 minutes')")

@app.route("/api/v1/validate-license", methods=["POST"])
def validate_license():
    data = request.json or {}
    tenant_id = data.get("tenant_id")
    codusu = data.get("codusu")
    nome_usuario = data.get("nome_usuario", "Usuário")
    session_token = data.get("session_token")
    ip_address = request.remote_addr

    if not tenant_id:
        return jsonify({"valid": False, "message": "Tenant ID é obrigatório"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Limpa sessões inativas/expiradas
    cleanup_stale_sessions(cursor)
    conn.commit()

    cursor.execute("""
        SELECT t.*
        FROM tenants t
        WHERE t.tenant_id = ?
    """, (tenant_id,))
    tenant = cursor.fetchone()

    if not tenant:
        # Se for um tenant local/dev, auto-registra para garantir fluxo contínuo
        if str(tenant_id).endswith("_local") or str(tenant_id).endswith("_dev") or tenant_id == "local":
            cursor.execute("""
                INSERT INTO tenants (tenant_id, nome_empresa, max_licencas, status, ambiente, superadmin_user, superadmin_password, modulos_habilitados)
                VALUES (?, ?, 99, 'ativo', 'local', 'admin', 'dCJ*H', '["inicio", "portal_vendas", "consulta_produtos", "promocoes", "bi", "configuracoes", "acessos"]')
            """, (tenant_id, f"Auto Giro ({tenant_id})"))
            conn.commit()
            cursor.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,))
            tenant = cursor.fetchone()
        else:
            conn.close()
            return jsonify({"valid": False, "message": f"Tenant '{tenant_id}' não localizado."}), 404

    if tenant["status"] != "ativo":
        conn.close()
        return jsonify({"valid": False, "message": f"Assinatura do tenant '{tenant_id}' está {tenant['status'].upper()}."}), 403

    max_lic = tenant["max_licencas"] or 10

    # Verifica se o mesmo usuário (codusu) já possui sessão ativa neste tenant
    # Regra: 1 Usuário = 1 Licença (novo login do mesmo usuário reaproveita e atualiza o token)
    if codusu:
        cursor.execute("SELECT id, session_token FROM active_sessions WHERE tenant_id = ? AND codusu = ?", (tenant_id, codusu))
        existing_user_session = cursor.fetchone()
        if existing_user_session:
            cursor.execute("""
                UPDATE active_sessions
                SET session_token = ?, ip_address = ?, ultimo_ping = CURRENT_TIMESTAMP, nome_usuario = ?
                WHERE id = ?
            """, (session_token, ip_address, nome_usuario, existing_user_session["id"]))
            conn.commit()
            cursor.execute("SELECT COUNT(*) FROM active_sessions WHERE tenant_id = ?", (tenant_id,))
            current_count = cursor.fetchone()[0]
            conn.close()
            return jsonify({
                "valid": True,
                "tenant": tenant["nome_empresa"],
                "max_licenses": max_lic,
                "active_licenses": current_count
            })

    # Se já houver session_token, verifica/atualiza
    if session_token:
        cursor.execute("SELECT id FROM active_sessions WHERE session_token = ?", (session_token,))
        existing_session = cursor.fetchone()

        if existing_session:
            cursor.execute("""
                UPDATE active_sessions
                SET ultimo_ping = CURRENT_TIMESTAMP, ip_address = ?
                WHERE session_token = ?
            """, (ip_address, session_token))
            conn.commit()
            cursor.execute("SELECT COUNT(*) FROM active_sessions WHERE tenant_id = ?", (tenant_id,))
            current_count = cursor.fetchone()[0]
            conn.close()
            return jsonify({
                "valid": True,
                "tenant": tenant["nome_empresa"],
                "max_licenses": max_lic,
                "active_licenses": current_count
            })

    # Contagem de sessões ativas
    cursor.execute("SELECT COUNT(*) FROM active_sessions WHERE tenant_id = ?", (tenant_id,))
    active_count = cursor.fetchone()[0]

    # Identifica se é ambiente local ou tenant de desenvolvimento
    is_local_env = (
        ip_address in ("127.0.0.1", "::1", "localhost")
        or str(tenant_id).endswith("_local")
        or str(tenant_id).endswith("_dev")
        or tenant["ambiente"] == "local"
    )

    # Nova sessão tentando conectar (bloqueia apenas em ambiente de produção/remoto se atingir o limite)
    if not is_local_env and active_count >= max_lic:
        conn.close()
        return jsonify({
            "valid": False,
            "message": f"Limite de licenças atingido ({active_count}/{max_lic}). Entre em contato com a administração."
        }), 403

    # Registra nova sessão
    if session_token and codusu:
        cursor.execute("""
            INSERT INTO active_sessions (tenant_id, codusu, nome_usuario, session_token, ip_address, ultimo_ping)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (tenant_id, codusu, nome_usuario, session_token, ip_address))
        conn.commit()
        active_count += 1

    conn.close()
    return jsonify({
        "valid": True,
        "tenant": tenant["nome_empresa"],
        "max_licenses": max_lic,
        "active_licenses": active_count
    })

@app.route("/api/v1/session-heartbeat", methods=["POST"])
def session_heartbeat():
    data = request.json or {}
    session_token = data.get("session_token")
    tenant_id = data.get("tenant_id")
    codusu = data.get("codusu")
    nome_usuario = data.get("nome_usuario", "Usuário")
    ip_address = request.remote_addr

    if not session_token:
        return jsonify({"status": "error", "message": "session_token ausente"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE active_sessions
        SET ultimo_ping = CURRENT_TIMESTAMP, ip_address = ?
        WHERE session_token = ?
    """, (ip_address, session_token))

    if cursor.rowcount > 0:
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "session_active": True})

    # Sessão não encontrada na tabela active_sessions (foi encerrada/morta pela administração)
    conn.close()
    return jsonify({"status": "terminated", "session_active": False, "message": "Sessão encerrada pelo administrador."}), 401

@app.route("/api/v1/unregister-session", methods=["POST"])
def unregister_session():
    data = request.json or {}
    session_token = data.get("session_token")
    if not session_token:
        return jsonify({"status": "error", "message": "session_token ausente"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_sessions WHERE session_token = ?", (session_token,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

def parse_version_tuple(v_str):
    """
    Normaliza e extrai partes numéricas de uma versão.
    Exemplos:
      'v1.0.1'  -> [1, 0, 1]
      'v.1.0.2' -> [1, 0, 2]
      '1.0.2'   -> [1, 0, 2]
      'v1.0.10' -> [1, 0, 10]
    """
    if not v_str:
        return [0]
    import re
    nums = re.findall(r'\d+', str(v_str))
    if nums:
        return [int(n) for n in nums]
    return [0]

def comparar_versoes(v1, v2):
    """
    Retorna:
      -1 se v1 < v2
       0 se v1 == v2
       1 se v1 > v2
    """
    t1 = parse_version_tuple(v1)
    t2 = parse_version_tuple(v2)
    max_len = max(len(t1), len(t2))
    t1_pad = t1 + [0] * (max_len - len(t1))
    t2_pad = t2 + [0] * (max_len - len(t2))
    if t1_pad < t2_pad:
        return -1
    elif t1_pad > t2_pad:
        return 1
    return 0

# ==========================================
# 5. GERENCIADOR DE VERSÕES, BRANCHES & DEPLOYS
# ==========================================
def sincronizar_com_central():
    """
    Sincroniza versões bi-direcionalmente com o servidor central (VPS) se estiver rodando localmente.
    """
    central_url = os.getenv("CENTRAL_ADMIN_URL", "https://admin.dataevo.com.br").rstrip("/")
    host_req = request.host if has_request_context() else ""
    if "dataevo.com.br" in host_req or ("143.95.163.67" in host_req and "dataevo.com.br" in central_url):
        return False, "Esta instância já é o servidor central."

    import requests
    try:
        # 1. Puxa versões cadastradas na Central
        res = requests.get(f"{central_url}/api/v1/sistema-versoes", timeout=4)
        if res.status_code == 200:
            data = res.json()
            remote_versoes = data.get("versoes", [])
            conn = get_db_connection()
            cursor = conn.cursor()
            novas = 0
            for v in remote_versoes:
                tag = (v.get("tag_versao") or "").strip()
                if tag == "v.1.0.2":
                    tag = "v1.0.2"
                cursor.execute("SELECT id FROM sistema_versoes WHERE tag_versao = ?", (tag,))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO sistema_versoes (tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, criado_em)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        tag,
                        v.get("branch", "main"),
                        v.get("docker_tag"),
                        v.get("titulo"),
                        v.get("prompt"),
                        v.get("descricao"),
                        v.get("tipo", "minor"),
                        v.get("status", "estavel"),
                        v.get("criado_em")
                    ))
                    novas += 1
            conn.commit()

            # 2. Envia versões locais para a central (garantindo sincronização 2-way)
            cursor.execute("SELECT tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, criado_em FROM sistema_versoes")
            local_rows = [dict(r) for r in cursor.fetchall()]
            conn.close()

            try:
                requests.post(f"{central_url}/api/v1/sistema-versoes/sync", json={"versoes": local_rows}, timeout=4)
            except Exception:
                pass

            return True, f"Sincronização concluída! ({len(remote_versoes)} versões na Central, {novas} novas importadas)."
        return False, f"Servidor Central respondeu com status {res.status_code}."
    except Exception as e:
        return False, f"Não foi possível conectar ao servidor central: {e}"


@app.route("/api/v1/sistema-versoes", methods=["GET"])
def api_listar_sistema_versoes():
    """Retorna todas as versões cadastradas para sincronização entre instâncias do Painel Admin."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, criado_em FROM sistema_versoes ORDER BY id ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"status": "1", "versoes": rows})


@app.route("/api/v1/sistema-versoes/sync", methods=["POST"])
def api_sincronizar_sistema_versoes():
    """Recebe versões e realiza o upsert (inserção ou atualização) no banco de dados."""
    data = request.get_json() or {}
    versoes_in = data.get("versoes", [])
    if not versoes_in and isinstance(data, list):
        versoes_in = data

    if not versoes_in:
        return jsonify({"status": "0", "mensagem": "Nenhuma versão fornecida para sincronização."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    count_sync = 0

    try:
        for v in versoes_in:
            tag = (v.get("tag_versao") or "").strip()
            if not tag:
                continue
            if tag == "v.1.0.2":
                tag = "v1.0.2"

            titulo = (v.get("titulo") or "").strip() or f"Release {tag}"
            branch = (v.get("branch") or "main").strip()
            docker_tag = (v.get("docker_tag") or "").strip() or f"eduardotavares24/orderflow:{tag}"
            prompt = (v.get("prompt") or "").strip()
            descricao = (v.get("descricao") or "").strip()
            tipo = (v.get("tipo") or "minor").strip()
            status = (v.get("status") or "estavel").strip()
            projeto_id_v = v.get("projeto_id")
            if projeto_id_v and str(projeto_id_v).isdigit():
                projeto_id_v = int(projeto_id_v)
            else:
                projeto_id_v = None

            cursor.execute("SELECT id FROM sistema_versoes WHERE tag_versao = ?", (tag,))
            existente = cursor.fetchone()
            if existente:
                cursor.execute("""
                    UPDATE sistema_versoes
                    SET branch = ?, docker_tag = ?, titulo = ?, prompt = ?, descricao = ?, tipo = ?, status = ?, projeto_id = ?
                    WHERE tag_versao = ?
                """, (branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id_v, tag))
            else:
                criado_em = v.get("criado_em")
                if criado_em:
                    cursor.execute("""
                        INSERT INTO sistema_versoes (tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id, criado_em)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (tag, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id_v, criado_em))
                else:
                    cursor.execute("""
                        INSERT INTO sistema_versoes (tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (tag, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id_v))
            count_sync += 1

        # =========================================================================
        # REGRA CRÍTICA DE RELEASE:
        # A(s) instância(s) do ambiente 'BASE' são SEMPRE atualizadas automaticamente
        # para a nova versão assim que a versão for sincronizada/salva.
        # As instâncias de ambiente 'TESTE' e 'PRODUÇÃO' PERMANECEM INTACTAS.
        # =========================================================================
        if versoes_in:
            for v_item in versoes_in:
                tag_item = (v_item.get("tag_versao") or "").strip()
                if not tag_item:
                    continue
                p_id = v_item.get("projeto_id")
                if p_id and str(p_id).isdigit():
                    p_id = int(p_id)
                else:
                    p_id = None

                q_base = "SELECT id, tenant_id, nome_empresa, versao_atual FROM tenants WHERE ambiente = 'base'"
                p_base = []
                if p_id:
                    q_base += " AND (projeto_id = ? OR projeto_id IS NULL)"
                    p_base.append(p_id)

                cursor.execute(q_base, tuple(p_base))
                tenants_base_sync = [dict(r) for r in cursor.fetchall()]
                for tb in tenants_base_sync:
                    v_antiga = tb.get("versao_atual") or "v1.0.0"
                    cursor.execute("UPDATE tenants SET versao_atual = ? WHERE id = ?", (tag_item, tb["id"]))
                    cursor.execute("""
                        INSERT INTO tenant_deploys_historico (tenant_id, versao_tag, versao_anterior_tag, acao, executado_por, status, observacao, log_execucao)
                        VALUES (?, ?, ?, 'auto_deploy_base', 'Sincronização Central', 'sucesso', ?, 'Atualização automática imediata da instância Base via sincronização de release.')
                    """, (tb["tenant_id"], tag_item, v_antiga, f"Sincronização da versão {tag_item}"))

        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"status": "0", "erro": str(e)}), 500

    conn.close()
    return jsonify({"status": "1", "sincronizadas": count_sync})


@app.route("/admin/versoes/sincronizar-central", methods=["GET", "POST"])
@login_required
@permissao_requerida("versoes", "ver")
def rota_sincronizar_central():
    sucesso, msg = sincronizar_com_central()
    tipo = "inserir" if sucesso else "aviso"
    return redirect(url_for("versoes", msg=msg, tipo=tipo))


# ==========================================
# 4. GESTÃO DE PROJETOS & BRANCHES
# ==========================================
@app.route("/projetos")
@login_required
@permissao_requerida("projetos", "ver")
def projetos():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT p.*, c.nome_fantasia as cliente_nome, c.razao_social as cliente_razao,
               (SELECT COUNT(*) FROM tenants t WHERE t.projeto_id = p.id) as total_tenants,
               (SELECT COUNT(*) FROM sistema_versoes v WHERE v.projeto_id = p.id) as total_versoes
        FROM projetos p
        LEFT JOIN clientes c ON p.cliente_id = c.id
        ORDER BY p.criado_em DESC
    """)
    projetos_raw = cursor.fetchall()
    projetos = [dict(p) for p in projetos_raw]

    cursor.execute("SELECT id, nome_fantasia, razao_social FROM clientes WHERE status = 'ativo' ORDER BY nome_fantasia ASC")
    clientes = [dict(c) for c in cursor.fetchall()]

    conn.close()

    return render_template("projetos.html", projetos=projetos, clientes=clientes)


@app.route("/admin/projeto/salvar", methods=["POST"])
@login_required
@permissao_requerida("projetos", "editar")
def salvar_projeto():
    p_id = request.form.get("id")
    nome_projeto = request.form.get("nome_projeto", "").strip()
    codigo_slug = request.form.get("codigo_slug", "").strip().lower().replace(" ", "_")
    cliente_id = request.form.get("cliente_id") or None
    branch_git = request.form.get("branch_git", "main").strip()
    repositorio_url = request.form.get("repositorio_url", "").strip()
    descricao = request.form.get("descricao", "").strip()
    status = request.form.get("status", "ativo").strip()

    if not nome_projeto or not codigo_slug:
        return "Nome do Projeto e Código Slug são obrigatórios.", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if p_id:
            cursor.execute("SELECT id FROM projetos WHERE codigo_slug = ? AND id != ?", (codigo_slug, p_id))
            if cursor.fetchone():
                conn.close()
                return f"Erro: O código slug '{codigo_slug}' já pertence a outro projeto.", 400

            cursor.execute("""
                UPDATE projetos
                SET nome_projeto = ?, codigo_slug = ?, cliente_id = ?, branch_git = ?, repositorio_url = ?, descricao = ?, status = ?
                WHERE id = ?
            """, (nome_projeto, codigo_slug, cliente_id, branch_git, repositorio_url, descricao, status, p_id))
            msg = f"Projeto '{nome_projeto}' atualizado com sucesso!"
        else:
            cursor.execute("SELECT id FROM projetos WHERE codigo_slug = ?", (codigo_slug,))
            if cursor.fetchone():
                conn.close()
                return f"Erro: O código slug '{codigo_slug}' já está cadastrado.", 400

            cursor.execute("""
                INSERT INTO projetos (nome_projeto, codigo_slug, cliente_id, branch_git, repositorio_url, descricao, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (nome_projeto, codigo_slug, cliente_id, branch_git, repositorio_url, descricao, status))
            msg = f"Projeto '{nome_projeto}' cadastrado com sucesso!"

        conn.commit()
    except Exception as e:
        conn.close()
        return f"Erro ao salvar projeto: {e}", 400

    conn.close()
    return redirect(url_for("projetos", msg=msg, tipo="sucesso"))


@app.route("/admin/projeto/duplicar", methods=["POST"])
@login_required
@permissao_requerida("projetos", "duplicar")
def duplicar_projeto():
    origem_id = request.form.get("origem_id")
    novo_nome = request.form.get("novo_nome", "").strip()
    novo_slug = request.form.get("novo_slug", "").strip().lower().replace(" ", "_")
    cliente_id = request.form.get("cliente_id") or None
    nova_branch = request.form.get("nova_branch", "").strip()
    copiar_versoes = request.form.get("copiar_versoes") == "1"
    criar_instancia_local = request.form.get("criar_instancia_local") == "1"

    if not origem_id or not novo_nome or not novo_slug or not nova_branch:
        return "Projeto de origem, novo nome, slug e nova branch são obrigatórios.", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM projetos WHERE id = ?", (origem_id,))
    p_origem = cursor.fetchone()
    if not p_origem:
        conn.close()
        return "Projeto de origem não localizado.", 404

    try:
        cursor.execute("SELECT id FROM projetos WHERE codigo_slug = ?", (novo_slug,))
        if cursor.fetchone():
            conn.close()
            return f"Erro: O slug '{novo_slug}' já está em uso.", 400

        # 1. Cria o Novo Projeto
        cursor.execute("""
            INSERT INTO projetos (nome_projeto, codigo_slug, cliente_id, branch_git, repositorio_url, descricao, status)
            VALUES (?, ?, ?, ?, ?, ?, 'ativo')
        """, (novo_nome, novo_slug, cliente_id, nova_branch, p_origem["repositorio_url"], f"Projeto duplicado a partir de '{p_origem['nome_projeto']}'. Branch base: {nova_branch}."))
        novo_proj_id = cursor.lastrowid

        # 2. Copia as versões se solicitado
        if copiar_versoes:
            cursor.execute("SELECT * FROM sistema_versoes WHERE projeto_id = ? OR projeto_id IS NULL", (origem_id,))
            versoes_origem = cursor.fetchall()
            for v in versoes_origem:
                nova_tag = f"{v['tag_versao']}-{novo_slug}"
                cursor.execute("""
                    INSERT OR IGNORE INTO sistema_versoes (tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (nova_tag, nova_branch, v["docker_tag"], f"{v['titulo']} ({novo_nome})", v["prompt"], v["descricao"], v["tipo"], v["status"], novo_proj_id))

        # 3. Cria Instância Local se solicitado
        if criar_instancia_local:
            tenant_id_local = f"{novo_slug}_local"
            cursor.execute("SELECT id FROM tenants WHERE tenant_id = ?", (tenant_id_local,))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO tenants (cliente_id, projeto_id, tenant_id, nome_empresa, sankhya_url, sankhya_appkey, ambiente, porta, max_licencas, status, versao_atual)
                    VALUES (?, ?, ?, ?, ?, ?, 'teste', 6020, 5, 'ativo', 'v1.0.5')
                """, (
                    cliente_id,
                    novo_proj_id,
                    tenant_id_local,
                    f"{novo_nome} (Localhost)",
                    os.getenv("DEFAULT_TENANT_SANKHYA_URL", ""),
                    os.getenv("DEFAULT_TENANT_SANKHYA_APPKEY", ""),
                ))

        conn.commit()
        msg = f"Projeto '{novo_nome}' duplicado com sucesso! Nova branch vinculada: '{nova_branch}'."
    except Exception as e:
        conn.close()
        return f"Erro ao duplicar projeto: {e}", 400

    conn.close()
    return redirect(url_for("projetos", msg=msg, tipo="sucesso"))


@app.route("/admin/projeto/excluir/<int:p_id>", methods=["POST"])
@login_required
@permissao_requerida("projetos", "excluir")
def excluir_projeto(p_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT nome_projeto FROM projetos WHERE id = ?", (p_id,))
    p = cursor.fetchone()
    if not p:
        conn.close()
        return "Projeto não localizado", 404

    cursor.execute("UPDATE tenants SET projeto_id = NULL WHERE projeto_id = ?", (p_id,))
    cursor.execute("UPDATE sistema_versoes SET projeto_id = NULL WHERE projeto_id = ?", (p_id,))
    cursor.execute("DELETE FROM projetos WHERE id = ?", (p_id,))

    conn.commit()
    conn.close()
    return redirect(url_for("projetos", msg=f"Projeto '{p['nome_projeto']}' excluído com sucesso.", tipo="alterar"))


@app.route("/versoes")
@login_required
@permissao_requerida("versoes", "ver")
def versoes():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Busca todas as versões cadastradas com contagem de instâncias associadas e projeto vinculado
    cursor.execute("""
        SELECT v.*,
               p.nome_projeto,
               p.branch_git as projeto_branch,
               (SELECT COUNT(*) FROM tenants t WHERE t.versao_atual = v.tag_versao) as total_tenants
        FROM sistema_versoes v
        LEFT JOIN projetos p ON v.projeto_id = p.id
        ORDER BY v.criado_em DESC
    """)
    versoes_raw = cursor.fetchall()
    versoes_lista = [dict(v) for v in versoes_raw]

    # Ordena versões estritamente por SemVer decrescente (da mais recente para a mais antiga)
    versoes_lista.sort(key=lambda v: parse_version_tuple(v["tag_versao"]), reverse=True)

    # Busca todas as instâncias (tenants) ativas para exibição e seletor de deploy
    cursor.execute("""
        SELECT t.*, c.nome_fantasia as cliente_nome
        FROM tenants t
        LEFT JOIN clientes c ON t.cliente_id = c.id
        ORDER BY t.nome_empresa ASC
    """)
    tenants_raw = cursor.fetchall()
    tenants_lista = [dict(t) for t in tenants_raw]

    # Busca histórico recente de deploys e rollbacks
    cursor.execute("""
        SELECT h.*, t.nome_empresa as tenant_nome
        FROM tenant_deploys_historico h
        LEFT JOIN tenants t ON h.tenant_id = t.tenant_id
        ORDER BY h.data_deploy DESC
        LIMIT 25
    """)
    historico_raw = cursor.fetchall()
    historico_lista = [dict(h) for h in historico_raw]

    # Estatísticas gerais com comparação semântica de versões
    total_versoes = len(versoes_lista)
    versoes_estaveis = [v for v in versoes_lista if v["status"] == "estavel"]
    if versoes_estaveis:
        versao_recomendada = max(versoes_estaveis, key=lambda v: parse_version_tuple(v["tag_versao"]))["tag_versao"]
    elif versoes_lista:
        versao_recomendada = max(versoes_lista, key=lambda v: parse_version_tuple(v["tag_versao"]))["tag_versao"]
    else:
        versao_recomendada = "v1.0.6"

    total_tenants = len(tenants_lista)
    tenants_na_versao_recomendada = sum(1 for t in tenants_lista if t.get("versao_atual") == versao_recomendada)

    # Enriquece cada tenant com o status semântico exato da versão
    for t in tenants_lista:
        v_atual = t.get("versao_atual") or "v1.0.1"
        cmp_v = comparar_versoes(v_atual, versao_recomendada)
        if cmp_v == 0:
            t["status_versao_tipo"] = "recomendada"
            t["status_versao_label"] = f"Versão Recomendada ({versao_recomendada})"
        elif cmp_v > 0:
            t["status_versao_tipo"] = "dev"
            t["status_versao_label"] = f"Em Desenvolvimento / Local ({v_atual})"
        else:
            t["status_versao_tipo"] = "update"
            t["status_versao_label"] = f"Atualização disponível ({versao_recomendada})"

    # Determina o repositório Docker padrão a partir dos lançamentos anteriores
    docker_repo_padrao = "eduardotavares24/orderflow"
    for v in versoes_lista:
        dt = v.get("docker_tag") or ""
        if ":" in dt:
            repo_candidate = dt.split(":")[0].strip()
            if repo_candidate:
                docker_repo_padrao = repo_candidate
                break

    # Calcula a próxima versão SemVer sugerida para novo lançamento
    if versoes_lista:
        maior_v = max(versoes_lista, key=lambda v: parse_version_tuple(v["tag_versao"]))
        tupla = parse_version_tuple(maior_v["tag_versao"])
        if len(tupla) >= 3:
            proxima_versao = f"v{tupla[0]}.{tupla[1]}.{tupla[2] + 1}"
        elif len(tupla) == 2:
            proxima_versao = f"v{tupla[0]}.{tupla[1] + 1}.0"
        elif len(tupla) == 1:
            proxima_versao = f"v{tupla[0]}.0.1"
        else:
            proxima_versao = "v1.0.1"
    else:
        proxima_versao = "v1.0.0"

    # Busca lista de projetos ativos para atribuição no lançamento de release
    cursor.execute("SELECT id, nome_projeto, codigo_slug, branch_git FROM projetos WHERE status = 'ativo' ORDER BY nome_projeto ASC")
    projetos_raw = cursor.fetchall()
    projetos_lista = [dict(p) for p in projetos_raw]

    conn.close()
    return render_template(
        "versoes.html",
        versoes=versoes_lista,
        tenants=tenants_lista,
        projetos=projetos_lista,
        historico=historico_lista,
        total_versoes=total_versoes,
        versao_recomendada=versao_recomendada,
        proxima_versao=proxima_versao,
        docker_repo_padrao=docker_repo_padrao,
        total_tenants=total_tenants,
        tenants_na_versao_recomendada=tenants_na_versao_recomendada
    )


@app.route("/admin/versao/salvar", methods=["POST"])
@login_required
@permissao_requerida("versoes", "editar")
def salvar_versao():
    v_id = request.form.get("id")
    tag_versao = request.form.get("tag_versao", "").strip()
    branch = request.form.get("branch", "main").strip()
    projeto_id = request.form.get("projeto_id") or None
    if projeto_id and str(projeto_id).isdigit():
        projeto_id = int(projeto_id)
    else:
        projeto_id = None
    docker_tag = request.form.get("docker_tag", "").strip()
    if not docker_tag and tag_versao:
        docker_tag = f"eduardotavares24/orderflow:{tag_versao}"
    titulo = request.form.get("titulo", "").strip()
    prompt = request.form.get("prompt", "").strip()
    descricao = request.form.get("descricao", "").strip()
    tipo = request.form.get("tipo", "minor")
    status = request.form.get("status", "estavel")

    if not tag_versao or not titulo:
        return "Tag da versão e Título são campos obrigatórios.", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if v_id:
            cursor.execute("""
                UPDATE sistema_versoes
                SET tag_versao = ?, branch = ?, docker_tag = ?, titulo = ?, prompt = ?, descricao = ?, tipo = ?, status = ?, projeto_id = ?
                WHERE id = ?
            """, (tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id, v_id))
            flash_msg = f"Versão '{tag_versao}' atualizada com sucesso!"
            tipo_acao = "alterar"
        else:
            cursor.execute("SELECT id FROM sistema_versoes WHERE tag_versao = ?", (tag_versao,))
            if cursor.fetchone():
                conn.close()
                return f"Erro: A versão '{tag_versao}' já está cadastrada.", 400

            cursor.execute("""
                INSERT INTO sistema_versoes (tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (tag_versao, branch, docker_tag, titulo, prompt, descricao, tipo, status, projeto_id))
            flash_msg = f"Versão '{tag_versao}' registrada com sucesso!"
            tipo_acao = "inserir"

        # =========================================================================
        # REGRA CRÍTICA DE RELEASE:
        # A(s) instância(s) do ambiente 'BASE' são SEMPRE atualizadas automaticamente
        # para a nova versão assim que a versão for salva.
        # As instâncias de ambiente 'TESTE' e 'PRODUÇÃO' PERMANECEM INTACTAS,
        # dependendo exclusivamente da ação humana explícita de "Executar Deploy / Rollback".
        # =========================================================================
        query_base = "SELECT id, tenant_id, nome_empresa, versao_atual FROM tenants WHERE ambiente = 'base'"
        params_base = []
        if projeto_id:
            query_base += " AND (projeto_id = ? OR projeto_id IS NULL)"
            params_base.append(projeto_id)

        cursor.execute(query_base, tuple(params_base))
        tenants_base = [dict(r) for r in cursor.fetchall()]

        admin_nome = session.get("admin_user_nome", "Admin (Lançamento)")
        bases_atualizadas = []
        for tb in tenants_base:
            v_antiga = tb.get("versao_atual") or "v1.0.0"
            cursor.execute("UPDATE tenants SET versao_atual = ? WHERE id = ?", (tag_versao, tb["id"]))
            cursor.execute("""
                INSERT INTO tenant_deploys_historico (tenant_id, versao_tag, versao_anterior_tag, acao, executado_por, status, observacao, log_execucao)
                VALUES (?, ?, ?, 'auto_deploy_base', ?, 'sucesso', ?, 'Atualização automática imediata da instância Base ao lançar a nova release.')
            """, (tb["tenant_id"], tag_versao, v_antiga, admin_nome, f"Lançamento da versão {tag_versao}"))
            bases_atualizadas.append(f"{tb['nome_empresa']}")

        conn.commit()

        # Salva snapshot permanente dos arquivos desta versão em /opt/orderflow/releases/{tag_versao}
        try:
            salvar_snapshot_versao(tag_versao)
        except Exception as e_snap:
            print(f"[Snapshot Release] Aviso: {e_snap}")

        if bases_atualizadas:
            flash_msg += f" A(s) base(s) de desenvolvimento ({', '.join(bases_atualizadas)}) foram automaticamente promovidas para {tag_versao}. As bases de Teste e Produção foram preservadas intactas."
        else:
            flash_msg += " As bases de Teste e Produção foram preservadas intactas e requerem deploy manual."

        # Se estiver em ambiente local, envia imediatamente a nova versão para o servidor central
        central_url = os.getenv("CENTRAL_ADMIN_URL", "https://admin.dataevo.com.br").rstrip("/")
        if "dataevo.com.br" not in request.host:
            try:
                import requests
                requests.post(f"{central_url}/api/v1/sistema-versoes/sync", json={"versoes": [{
                    "tag_versao": tag_versao,
                    "branch": branch,
                    "docker_tag": docker_tag,
                    "titulo": titulo,
                    "prompt": prompt,
                    "descricao": descricao,
                    "tipo": tipo,
                    "status": status,
                    "projeto_id": projeto_id
                }]}, timeout=3)
            except Exception as sync_err:
                print(f"[Sync Versão] Aviso ao enviar para central: {sync_err}")

    except Exception as e:
        conn.close()
        return f"Erro ao salvar versão: {e}", 400

    conn.close()
    return redirect(url_for("versoes", msg=flash_msg, tipo=tipo_acao))

@app.route("/admin/versao/deletar/<int:id>", methods=["POST"])
@login_required
@permissao_requerida("versoes", "excluir")
def deletar_versao(id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT tag_versao FROM sistema_versoes WHERE id = ?", (id,))
    v = cursor.fetchone()
    if not v:
        conn.close()
        return redirect(url_for("versoes", msg="Versão não encontrada.", tipo="erro"))

    tag = v["tag_versao"]
    # Bloqueia exclusão se houver tenant utilizando a versão
    cursor.execute("SELECT COUNT(*) FROM tenants WHERE versao_atual = ?", (tag,))
    em_uso = cursor.fetchone()[0]
    if em_uso > 0:
        conn.close()
        return redirect(url_for("versoes", msg=f"A versão '{tag}' não pode ser excluída porque {em_uso} instância(s) estão em execução nela.", tipo="erro"))

    cursor.execute("DELETE FROM sistema_versoes WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("versoes", msg=f"Versão '{tag}' excluída com sucesso!", tipo="deletar"))

def salvar_snapshot_versao(tag_versao):
    """Gera um snapshot permanente da versão recém-lançada em /opt/orderflow/releases/{tag_versao}."""
    import shutil
    base_src = "/opt/orderflow"
    if os.path.exists(base_src):
        target_dir = f"/opt/orderflow/releases/{tag_versao}"
        ignore_func = shutil.ignore_patterns(
            ".git*", ".venv*", "__pycache__*", "scratch*", "admin_data*", "releases*", "admin_panel*", "*.pyc", "*.db"
        )
        try:
            shutil.copytree(base_src, target_dir, dirs_exist_ok=True, ignore=ignore_func)
        except Exception as e:
            print(f"[Snapshot] Erro ao criar snapshot {tag_versao}: {e}")

def aplicar_snapshot_tenant(tenant_id, nova_versao_tag):
    """Copia o código da versão alvo para o diretório isolado do tenant."""
    import shutil
    dest_dir = f"/opt/orderflow_releases/{tenant_id}"
    snap_dir = f"/opt/orderflow/releases/{nova_versao_tag}"
    source_dir = snap_dir if os.path.exists(snap_dir) else "/opt/orderflow"

    if os.path.exists("/opt/orderflow") or os.path.exists(source_dir):
        os.makedirs(dest_dir, exist_ok=True)
        ignore_func = shutil.ignore_patterns(
            ".git*", ".venv*", "__pycache__*", "scratch*", "admin_data*", "releases*", "admin_panel*", "*.pyc", "*.db"
        )
        try:
            shutil.copytree(source_dir, dest_dir, dirs_exist_ok=True, ignore=ignore_func)
            return True, f"Código da versão {nova_versao_tag} aplicado com sucesso no ambiente isolado do tenant."
        except Exception as e:
            return False, f"Erro ao aplicar release: {e}"
    return True, "Ambiente local: sem diretório de releases."

def executar_acao_docker(tenant_id, nova_versao_tag, docker_tag=None):
    """
    Executa a automação no Docker do host através do docker socket / docker compose.
    Retorna: (sucesso: bool, mensagem_log: str)
    """
    import subprocess
    import shutil

    logs = []

    # Se não for o ambiente base, aplica o snapshot isolado de código no diretório do tenant
    if tenant_id and tenant_id != "autogiro_base":
        sucesso_snap, msg_snap = aplicar_snapshot_tenant(tenant_id, nova_versao_tag)
        logs.append(msg_snap)

    service_name = "orderflow-base" if tenant_id == "autogiro_base" else "orderflow-app"

    # 1. Se tiver acesso ao socket do Docker (/var/run/docker.sock), reinicia instantaneamente via Docker REST API
    if os.path.exists("/var/run/docker.sock"):
        try:
            cmd = ["curl", "-s", "--unix-socket", "/var/run/docker.sock", "-X", "POST", f"http://localhost/containers/{service_name}/restart"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if res.returncode == 0:
                logs.append(f"Container '{service_name}' reiniciado com sucesso via Docker Socket.")
                return True, "\n".join(logs)
        except Exception as e_sock:
            logs.append(f"Aviso docker socket: {e_sock}")

    docker_bin = shutil.which("docker")
    if not docker_bin:
        return True, "\n".join(logs) + "\n(Deploy de código concluído. Docker CLI não disponível para restart automático)."

    compose_paths = [
        "/opt/orderflow/docker-compose.yml",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "docker-compose.yml"))
    ]
    compose_file = None
    for cp in compose_paths:
        if os.path.exists(cp):
            compose_file = cp
            break
    env_paths = [
        "/opt/orderflow/.env",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".env"))
    ]
    env_file = None
    for ep in env_paths:
        if os.path.exists(ep):
            env_file = ep
            break

    if docker_tag and env_file:
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                env_content = f.read()

            if "ORDERFLOW_IMAGE=" in env_content:
                lines = []
                for line in env_content.splitlines():
                    if line.startswith("ORDERFLOW_IMAGE="):
                        lines.append(f"ORDERFLOW_IMAGE={docker_tag}")
                    else:
                        lines.append(line)
                env_content = "\n".join(lines) + "\n"
            else:
                env_content += f"\nORDERFLOW_IMAGE={docker_tag}\n"

            with open(env_file, "w", encoding="utf-8") as f:
                f.write(env_content)
            logs.append(f"Configurado ORDERFLOW_IMAGE={docker_tag} em {env_file}")
        except Exception as e_env:
            logs.append(f"Aviso .env: {e_env}")

    # 1.1 Garante o pull da nova imagem Docker se tag foi informada
    if docker_tag:
        try:
            cmd_pull = [docker_bin, "pull", docker_tag]
            p_pull = subprocess.run(cmd_pull, capture_output=True, text=True, timeout=120)
            if p_pull.returncode == 0:
                logs.append(f"Imagem Docker '{docker_tag}' baixada com sucesso (docker pull).")
            else:
                logs.append(f"Aviso docker pull: {p_pull.stderr.strip() if p_pull.stderr else 'retorno ' + str(p_pull.returncode)}")
        except Exception as e_pull:
            logs.append(f"Exceção docker pull: {e_pull}")

    # 2. Executa Docker Compose Recreate ou Restart
    if compose_file:
        try:
            cmd = [docker_bin, "compose", "-f", compose_file, "up", "-d", "--no-deps", service_name]
            p_comp = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            if p_comp.returncode == 0:
                logs.append(f"Container '{service_name}' recriado com sucesso via Docker Compose.")
                if p_comp.stdout:
                    logs.append(p_comp.stdout.strip())
                return True, "\n".join(logs)
            else:
                logs.append(f"Aviso Compose up: {p_comp.stderr.strip() if p_comp.stderr else 'código ' + str(p_comp.returncode)}")
        except Exception as ce:
            logs.append(f"Erro Compose: {ce}")

    # 3. Fallback: docker restart
    try:
        cmd_restart = [docker_bin, "restart", service_name]
        p_res = subprocess.run(cmd_restart, capture_output=True, text=True, timeout=20)
        if p_res.returncode == 0:
            logs.append(f"Container '{service_name}' reiniciado com sucesso via Docker.")
            return True, "\n".join(logs)
        else:
            logs.append(f"Falha docker restart: {p_res.stderr.strip() if p_res.stderr else 'código ' + str(p_res.returncode)}")
            return False, "\n".join(logs)
    except Exception as re:
        logs.append(f"Exceção Docker: {re}")
        return False, "\n".join(logs)


@app.route("/admin/versao/deploy", methods=["POST"])
@login_required
@permissao_requerida("versoes", "deploy")
def deploy_versao():
    tenant_id = request.form.get("tenant_id", "").strip()
    nova_versao_tag = request.form.get("versao_tag", "").strip()
    observacao = request.form.get("observacao", "").strip()
    criar_backup = request.form.get("criar_backup") == "1"
    reiniciar_container = request.form.get("reiniciar_container") == "1"
    admin_nome = session.get("admin_user_nome", "Administrador")

    if not tenant_id or not nova_versao_tag:
        return "Tenant e Versão são obrigatórios", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, tenant_id, nome_empresa, versao_atual FROM tenants WHERE tenant_id = ?", (tenant_id,))
    t = cursor.fetchone()
    if not t:
        conn.close()
        return "Instância não localizada.", 404

    # Busca detalhes da versão de destino
    cursor.execute("SELECT id, tag_versao, branch, docker_tag, titulo FROM sistema_versoes WHERE tag_versao = ?", (nova_versao_tag,))
    v_info = cursor.fetchone()
    docker_tag = v_info["docker_tag"] if v_info else None

    versao_anterior = t["versao_atual"] or "v1.0.0"

    # Identifica ação como Rollback ou Deploy usando comparação semântica
    cmp_res = comparar_versoes(nova_versao_tag, versao_anterior)
    acao = "rollback" if cmp_res < 0 else "deploy"

    # Backup / Snapshot de Segurança do SQLite caso selecionado
    log_backup = ""
    if criar_backup:
        try:
            import shutil
            from datetime import datetime
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_file = os.path.join(base_dir, "admin.db")
            backup_dir = os.path.join(base_dir, "..", "admin_data", "backups")
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir, exist_ok=True)
            if os.path.exists(db_file):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"admin_{acao}_{tenant_id}_{timestamp}.db"
                shutil.copy2(db_file, os.path.join(backup_dir, backup_name))
                log_backup = f"Snapshot criado: {backup_name}"
        except Exception as b_err:
            log_backup = f"Aviso snapshot: {b_err}"

    # Atualiza versão do tenant no banco de dados
    cursor.execute("UPDATE tenants SET versao_atual = ? WHERE tenant_id = ?", (nova_versao_tag, tenant_id))

    # Executa a automação no Docker se solicitado
    log_docker = "Automação no Docker não solicitada."
    status_deploy = "sucesso"
    if reiniciar_container:
        sucesso_docker, log_docker = executar_acao_docker(tenant_id, nova_versao_tag, docker_tag)
        if not sucesso_docker:
            status_deploy = "aviso"

    log_final = f"{log_backup}\n{log_docker}".strip()

    # Registra no histórico de deploys com auditoria completa
    cursor.execute("""
        INSERT INTO tenant_deploys_historico (tenant_id, versao_tag, versao_anterior_tag, acao, executado_por, status, observacao, log_execucao)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (tenant_id, nova_versao_tag, versao_anterior, acao, admin_nome, status_deploy, observacao, log_final))

    conn.commit()
    conn.close()

    acao_label = "Reversão (Rollback)" if acao == "rollback" else "Deploy (Atualização)"
    msg_alerta = f"{acao_label} para a versão {nova_versao_tag} aplicado com sucesso na instância '{t['nome_empresa']}'!"
    if status_deploy == "aviso":
        msg_alerta += " (Aviso: Verifique o log de execução do Docker)."

    return redirect(url_for("versoes", msg=msg_alerta, tipo="alterar"))

if __name__ == "__main__":
    print("[OrderFlow Admin] Servidor iniciado na porta 5005...")
    app.run(host="0.0.0.0", port=5005, debug=True)
