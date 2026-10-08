import os
import time
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from sankhya_api import SankhyaAPI
import orderflow_auth

from config import Config

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY

# Configurações de acesso ao servidor Sankhya via .env / Config
SANKHYA_URL = Config.SANKHYA_BASE_URL
SANKHYA_USER = Config.SANKHYA_USERNAME
SANKHYA_PASS = Config.SANKHYA_PASSWORD
SANKHYA_TOKEN = Config.SANKHYA_APPKEY

sankhya = SankhyaAPI(base_url=SANKHYA_URL, usuario=SANKHYA_USER, senha=SANKHYA_PASS)


@app.after_request
def add_security_headers(response):
    """Adiciona cabeçalhos de segurança contra XSS, Clickjacking e MIME sniffing"""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.route("/healthz")
@app.route("/health")
def health_check():
    """Endpoint de monitoramento de saúde (Health Check) para o Render / Docker / Kubernetes"""
    return jsonify({"status": "ok", "app": "orderflow"}), 200


@app.before_request
def require_login():

    """Protege todas as rotas exigindo login prévio no Sankhya"""
    rota_atual = request.endpoint or ""
    # Permite acesso livre ao login, arquivos estáticos e health check
    if rota_atual in ["login", "static", "health_check"] or request.path in ["/healthz", "/health"] or request.path.startswith("/static/"):
        return None

    # Se o usuário não estiver autenticado na sessão, redireciona para a página de login ou responde 401 JSON
    if "usuario" not in session:
        if request.path.startswith("/api/"):
            return jsonify({"status": "0", "mensagem": "Sessão expirada. Faça login novamente.", "session_expired": True}), 401
        return redirect(url_for("login"))

    # Mantém a sessão ativa no Admin Panel (heartbeat a cada 40 segundos em requisições ativas)
    agora = time.time()
    last_ping = session.get("_last_admin_ping", 0)
    if agora - last_ping > 40:
        session["_last_admin_ping"] = agora
        import uuid
        import requests
        session_token = session.get("session_token")
        if not session_token:
            session_token = str(uuid.uuid4())
            session["session_token"] = session_token
        user_info = session.get("usuario", {})
        try:
            res = requests.post(
                f"{Config.ADMIN_PANEL_URL}/api/v1/session-heartbeat",
                json={
                    "session_token": session_token,
                    "tenant_id": Config.TENANT_ID,
                    "codusu": user_info.get("codusu"),
                    "nome_usuario": user_info.get("nome", "")
                },
                timeout=2
            )
            if res.status_code == 401:
                session.clear()
                return redirect(url_for("login"))
        except Exception:
            pass

        try:
            orderflow_auth.registrar_sessao_ativa(
                Config.TENANT_ID,
                user_info.get("codusu"),
                user_info.get("nome", ""),
                session_token,
                request.remote_addr
            )
        except Exception:
            pass

    # Validação de Bloqueio por Módulo do Tenant
    modulos = orderflow_auth.obter_modulos_habilitados_tenant(Config.TENANT_ID)
    rota_modulo_map = {
        "/": "inicio",
        "/portal-vendas": "portal_vendas",
        "/central-pedidos": "portal_vendas",
        "/novo-pedido": "portal_vendas",
        "/pedidos": "portal_vendas",
        "/consulta-produtos": "consulta_produtos",
        "/promocoes": "promocoes",
        "/bi": "bi",
        "/configuracoes": "configuracoes",
        "/acessos": "acessos"
    }
    for prefixo, mod_key in rota_modulo_map.items():
        if (request.path == prefixo or (prefixo != "/" and request.path.startswith(prefixo))) and mod_key not in modulos:
            # Se for superadmin, tem acesso irrestrito de configuração
            user_info = session.get("usuario", {})
            if user_info.get("is_superadmin"):
                break
            return render_template("403.html", mensagem="Este módulo não está contratado para o seu ambiente."), 403


@app.context_processor
def inject_modulos_habilitados():
    """Injeta a lista de módulos habilitados do tenant e dados da versão instalada em todos os templates."""
    modulos = orderflow_auth.obter_modulos_habilitados_tenant(Config.TENANT_ID)
    info_versao = orderflow_auth.obter_versao_tenant(Config.TENANT_ID)

    from urllib.parse import urlparse
    try:
        parsed_url = urlparse(Config.SANKHYA_BASE_URL)
        sankhya_host = parsed_url.netloc or "Sankhya OM"
    except Exception:
        sankhya_host = "Sankhya OM"

    return {
        "modulos_tenant": modulos,
        "modulo_habilitado": lambda mod_name: (mod_name in modulos),
        "info_versao": info_versao,
        "versao_atual": info_versao.get("versao_atual") or "v1.0.1",
        "versao_branch": info_versao.get("branch") or "main",
        "versao_ambiente": info_versao.get("ambiente") or os.getenv("AMBIENTE") or ("base" if str(Config.TENANT_ID).endswith("_base") else "teste"),
        "versao_status": info_versao.get("status_versao") or "estavel",
        "versao_titulo": info_versao.get("titulo") or "Versão Estável",
        "tenant_nome_empresa": info_versao.get("nome_empresa") or Config.TENANT_NAME or "Auto Giro",
        "sankhya_host": sankhya_host
    }


# ==========================================
# ROTAS DE AUTENTICAÇÃO
# ==========================================

@app.route("/login", methods=["GET", "POST"])
def login():
    # Se o parâmetro forcar_login estiver presente, limpa a sessão anterior para exigir login explícito
    if request.args.get("forcar_login") == "1":
        session.clear()
    elif "usuario" in session and request.method == "GET":
        return redirect(url_for("home"))

    erro = request.args.get("msg") or None
    usuario_digitado = ""

    if request.method == "POST":
        usuario_digitado = request.form.get("usuario", "").strip()
        senha_digitada = request.form.get("senha", "").strip()

        # Limpa dados anteriores de sessão
        session.clear()

        # 1. Autenticação prioritária de Super Admin do Tenant
        is_super, user_super = orderflow_auth.verificar_superadmin(Config.TENANT_ID, usuario_digitado, senha_digitada)
        if is_super and user_super:
            import uuid
            import requests
            session_token = str(uuid.uuid4())
            session["session_token"] = session_token
            session["usuario"] = user_super
            session["tenant_name"] = Config.TENANT_NAME
            session["boas_vindas"] = user_super.get("nome", "Super Admin")

            try:
                requests.post(
                    f"{Config.ADMIN_PANEL_URL}/api/v1/validate-license",
                    json={
                        "tenant_id": Config.TENANT_ID,
                        "codusu": user_super.get("codusu", 1),
                        "nome_usuario": user_super.get("nome", "Super Admin"),
                        "session_token": session_token
                    },
                    timeout=3
                )
            except Exception as e:
                print(f"[Multi-Tenant] Aviso: Não foi possível registrar sessão do Super Admin via API: {e}")

            # Registra no banco de dados compartilhado
            orderflow_auth.registrar_sessao_ativa(
                Config.TENANT_ID,
                user_super.get("codusu", 1),
                user_super.get("nome", "Super Admin"),
                session_token,
                request.remote_addr
            )

            return redirect(url_for("home"))

        # 2. Autenticação de Usuário Sankhya OM
        sucesso, user_info, msg_erro = sankhya.autenticar_usuario_sankhya(
            usuario=usuario_digitado,
            senha=senha_digitada
        )
        if not sucesso:
            print(f"[Auth] Tentativa de login sem sucesso para usuário '{usuario_digitado}'.")

        if sucesso and user_info:
            # 3. Validação de Permissão Nativa no OrderFlow (Grupos ou Usuários Permitidos)
            permitido, is_admin_local, msg_perm = orderflow_auth.verificar_permissao_orderflow(
                Config.TENANT_ID,
                user_info.get("codusu"),
                user_info.get("codgrupo")
            )

            if not permitido:
                erro = msg_perm or "Usuário sem permissão de acesso ao OrderFlow. Contate o administrador."
                return render_template("login.html", erro=erro, usuario=usuario_digitado)

            user_info["is_adm"] = is_admin_local
            user_info["is_superadmin"] = False

            # 4. Validação de Licenças Multi-Tenant & Registro de Sessão Ativa
            import uuid
            import requests
            session_token = session.get("session_token")
            if not session_token:
                session_token = str(uuid.uuid4())
                session["session_token"] = session_token

            try:
                res = requests.post(
                    f"{Config.ADMIN_PANEL_URL}/api/v1/validate-license",
                    json={
                        "tenant_id": Config.TENANT_ID,
                        "codusu": user_info.get("codusu"),
                        "nome_usuario": user_info.get("nome", usuario_digitado),
                        "session_token": session_token
                    },
                    timeout=3
                )
                if res.status_code != 200:
                    data = res.json()
                    is_local_request = request.remote_addr in ("127.0.0.1", "::1", "localhost") or str(Config.TENANT_ID).endswith("_local") or str(Config.TENANT_ID).endswith("_base") or info_versao.get("ambiente") == "base"
                    if is_local_request:
                        print(f"[Multi-Tenant] Aviso: Limite atingido no Admin ({data.get('message')}), mas acesso local permitido para desenvolvimento.")
                    else:
                        erro = data.get("message", "Licença inválida ou limite de usuários excedido.")
                        return render_template("login.html", erro=erro, usuario=usuario_digitado)
            except Exception as e:
                # Se o servidor admin remoto estiver offline, permite o acesso com log de aviso
                print(f"[Multi-Tenant] Aviso: Não foi possível validar a licença no servidor Admin: {e}")

            orderflow_auth.registrar_sessao_ativa(
                Config.TENANT_ID,
                user_info.get("codusu"),
                user_info.get("nome", usuario_digitado),
                session_token,
                request.remote_addr
            )

            session["usuario"] = user_info
            session["tenant_name"] = Config.TENANT_NAME
            session["boas_vindas"] = user_info.get("nome", usuario_digitado)
            return redirect(url_for("home"))
        else:
            erro = msg_erro or "Usuário ou senha inválidos no Sankhya."

    return render_template("login.html", erro=erro, usuario=usuario_digitado)


@app.route("/logout")
def logout():
    session_token = session.get("session_token")
    if session_token:
        try:
            import requests
            requests.post(
                f"{Config.ADMIN_PANEL_URL}/api/v1/unregister-session",
                json={"session_token": session_token},
                timeout=2
            )
        except Exception as e:
            print(f"[Multi-Tenant] Aviso: Falha ao desregistrar sessão no Admin: {e}")

        try:
            orderflow_auth.encerrar_sessao_ativa(Config.TENANT_ID, session_token=session_token)
        except Exception:
            pass

    session.clear()
    return redirect(url_for("login"))


@app.route("/api/heartbeat", methods=["POST", "GET"])
def api_heartbeat():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    import uuid
    import requests
    session_token = session.get("session_token")
    if not session_token:
        session_token = str(uuid.uuid4())
        session["session_token"] = session_token
    user_info = session.get("usuario", {})
    try:
        resp = requests.post(
            f"{Config.ADMIN_PANEL_URL}/api/v1/session-heartbeat",
            json={
                "session_token": session_token,
                "tenant_id": Config.TENANT_ID,
                "codusu": user_info.get("codusu"),
                "nome_usuario": user_info.get("nome", "")
            },
            timeout=2
        )
        if resp.status_code == 401 or (resp.headers.get("content-type", "").startswith("application/json") and resp.json().get("session_active") is False):
            # A sessão foi encerrada pela administração
            session.clear()
            return jsonify({"status": "0", "mensagem": "Sessão encerrada pelo administrador.", "session_expired": True}), 401

        session["_last_admin_ping"] = time.time()
    except Exception as e:
        print(f"[Heartbeat] Falha ao sincronizar com Admin: {e}")
    return jsonify({"status": "1"})


# ==========================================
# 1. INÍCIO & DASHBOARD
# ==========================================
@app.route("/")
def home():
    boas_vindas = session.pop("boas_vindas", None)
    return render_template(
        "index.html",
        nome_loja="Auto Giro Distribuidora",
        destaque="Central de Pedidos e Gestão Sankhya OM",
        boas_vindas=boas_vindas
    )


@app.route("/api/dashboard/faturamento-geral", methods=["GET"])
def api_faturamento_geral():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    user = session.get("usuario", {})
    is_admin = bool(user.get("is_adm"))
    user_codvend = user.get("codvend")

    dt_ini = request.args.get("dt_ini")
    dt_fim = request.args.get("dt_fim")
    codemp = request.args.get("codemp")
    
    # 1. Se for ADMIN, permite filtrar por qualquer vendedor ou ver todos
    # Se NÃO for ADMIN e tiver CODVEND vinculado, força apenas o CODVEND logado
    if is_admin:
        codvend = request.args.get("codvend")
    else:
        codvend = user_codvend if user_codvend and int(user_codvend) > 0 else None

    dados = sankhya.consultar_faturamento_geral(
        dt_ini=dt_ini,
        dt_fim=dt_fim,
        codemp=codemp,
        codvend=codvend
    )

    return jsonify({
        "status": "1",
        "dados": dados,
        "is_admin": is_admin
    })


@app.route("/api/dashboard/andamento-comissoes", methods=["GET"])
def api_andamento_comissoes():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    user = session.get("usuario", {})
    is_admin = bool(user.get("is_adm"))
    user_codvend = user.get("codvend")

    # "Esse deve aprender sempre o vendedor do usuário logado"
    # Se admin passar codvend especifico ou se usuário comum tiver codvend:
    req_codvend = request.args.get("codvend")
    if is_admin and req_codvend:
        codvend = req_codvend
    else:
        codvend = user_codvend

    if not codvend:
        return jsonify({
            "status": "0",
            "mensagem": "Usuário atual não possui código de vendedor (CODVEND) vinculado no Sankhya.",
            "dados": {"faixas": [], "info": {"codvend": 0, "apelido": user.get("nome", "")}}
        })

    dt_ini = request.args.get("dt_ini")
    dt_fim = request.args.get("dt_fim")
    codemp = request.args.get("codemp")

    dados = sankhya.consultar_andamento_comissoes(
        dt_ini=dt_ini,
        dt_fim=dt_fim,
        codemp=codemp,
        codvend=codvend
    )

    return jsonify({
        "status": "1",
        "dados": dados,
        "codvend_utilizado": codvend,
        "apelido": dados.get("info", {}).get("apelido") or user.get("nome", "")
    })



# ==========================================
# 2. CENTRAL DE PEDIDOS: NOVO PEDIDO OU PEDIDO EXISTENTE (5 QUADRANTES)
# ==========================================
@app.route("/central-pedidos/pedido/<int:nunota>")
@app.route("/central-pedidos/<int:nunota>")
def abrir_pedido_central(nunota):
    if "usuario" not in session:
        return redirect(url_for("login"))

    pedido_dados = sankhya.obter_pedido_completo(nunota)
    if not pedido_dados:
        return redirect(url_for("novo_pedido"))

    tipos_operacao = sankhya.listar_tipos_operacao()

    cab = pedido_dados["cabecalho"]
    tabela_preco = pedido_dados["tabela_preco"]
    limite_credito = pedido_dados["limite_credito"]
    itens = pedido_dados["itens"]
    opcoes_frete = sankhya.listar_opcoes_frete()

    return render_template(
        "central_pedidos.html",
        produtos=[],
        pagina_atual=1,
        total_paginas=0,
        total_registros=0,
        busca="",
        empresa={"codemp": cab["codemp"], "nomeemp": cab["nomeemp"]},
        tipos_operacao=tipos_operacao,
        data_neg=cab["dtneg"],
        codvend=cab["codvend"],
        nomevend=cab["nomevend"],
        tabela_preco=tabela_preco,
        tipos_negociacao=[],
        neg_padrao=None,
        limite_credito=limite_credito,
        opcoes_frete=opcoes_frete,
        pedido_existente=cab,
        itens_pedido_existente=itens
    )


@app.route("/central-pedidos/novo")
@app.route("/novo-pedido")
@app.route("/produtos")
def novo_pedido():
    if "usuario" not in session:
        return redirect(url_for("login"))

    nunota_param = request.args.get("nunota", type=int)
    if nunota_param:
        return abrir_pedido_central(nunota_param)

    user_info = session.get("usuario", {})
    codemp = user_info.get("codemp", 1)
    empresa = sankhya.obter_empresa(codemp)
    tipos_operacao = sankhya.listar_tipos_operacao()
    opcoes_frete = sankhya.listar_opcoes_frete()

    # Vendedor do usuário logado (TSIUSU.CODVEND)
    codvend = user_info.get("codvend", 0)
    nomevend = user_info.get("nomevend", "-")
    if (not nomevend or nomevend == "-") and codvend > 0:
        vend_info = sankhya.obter_vendedor(codvend)
        nomevend = vend_info.get("nomevend", "-")

    # Data de negociação padrão SYSDATE (com permissão de alteração)
    data_neg = datetime.now().strftime("%d/%m/%Y")

    # Suporte a item promocional pré-selecionado (ex: vindo da tela de Promoções via + Pedido)
    codprod_param = request.args.get("codprod", type=int)
    item_promocional = None
    if codprod_param:
        try:
            prod_info = sankhya.obter_produto(codprod_param)
            if prod_info:
                preco_promo = sankhya.obter_promocao_produto(codprod_param, codemp=codemp)
                preco_normal = float(prod_info.get("preco") or 0.0)
                desconto_valor = 0.0
                desconto_perc = 0.0

                if preco_promo and preco_promo > 0 and preco_normal > preco_promo:
                    desconto_valor = preco_normal - preco_promo
                    desconto_perc = (desconto_valor / preco_normal) * 100
                elif not preco_promo or preco_promo <= 0:
                    preco_promo = preco_normal

                item_promocional = {
                    "id": codprod_param,
                    "codprod": codprod_param,
                    "nome": prod_info.get("descrprod", ""),
                    "descrprod": prod_info.get("descrprod", ""),
                    "refforn": prod_info.get("refforn", ""),
                    "marca": prod_info.get("marca", ""),
                    "codvol": prod_info.get("codvol", "UN"),
                    "preco": preco_normal,
                    "promocao": preco_promo,
                    "preco_normal": preco_normal,
                    "preco_cdesconto": preco_promo,
                    "desconto_valor": round(desconto_valor, 2),
                    "desconto_perc": round(desconto_perc, 2)
                }
        except Exception as e:
            print(f"[CentralPedidos] Aviso ao carregar item_promocional #{codprod_param}: {e}")

    pagina = request.args.get("pagina", 1, type=int)
    busca = request.args.get("busca", "").strip()

    if pagina < 1:
        pagina = 1

    if not busca:
        return render_template(
            "central_pedidos.html",
            produtos=[],
            pagina_atual=1,
            total_paginas=0,
            total_registros=0,
            busca="",
            empresa=empresa,
            tipos_operacao=tipos_operacao,
            data_neg=data_neg,
            codvend=codvend,
            nomevend=nomevend,
            tabela_preco=None,
            tipos_negociacao=[],
            neg_padrao=None,
            limite_credito="",
            opcoes_frete=opcoes_frete,
            pedido_existente=None,
            itens_pedido_existente=[],
            item_promocional=item_promocional
        )

    codemp = user_info.get("codemp", 1)

    # Busca 15 produtos por página diretamente do Sankhya para o quadrante
    lista_de_produtos, total_registros, total_paginas = sankhya.listar_produtos(
        pagina=pagina,
        itens_por_pagina=15,
        busca=busca,
        codemp=codemp
    )

    return render_template(
        "central_pedidos.html",
        produtos=lista_de_produtos,
        pagina_atual=pagina,
        total_paginas=total_paginas,
        total_registros=total_registros,
        busca=busca,
        empresa=empresa,
        tipos_operacao=tipos_operacao,
        data_neg=data_neg,
        codvend=codvend,
        nomevend=nomevend,
        tabela_preco=None,
        tipos_negociacao=[],
        neg_padrao=None,
        limite_credito="",
        opcoes_frete=opcoes_frete,
        pedido_existente=None,
        itens_pedido_existente=[],
        item_promocional=item_promocional
    )


# ==========================================
# 3. CONSULTA DE PRODUTOS (VISUALIZAÇÃO AMPLA TELA CHEIA)
# ==========================================
@app.route("/consulta-produtos")
def consulta_produtos():
    if "usuario" not in session:
        return redirect(url_for("login"))

    pagina = request.args.get("pagina", 1, type=int)
    busca = request.args.get("busca", "").strip()

    if pagina < 1:
        pagina = 1

    codemp = request.args.get("codemp", type=int) or session.get("usuario", {}).get("codemp", 1)
    codparc = request.args.get("codparc", 0, type=int)
    codvend = request.args.get("codvend", 0, type=int)
    codreg = request.args.get("codreg", 0, type=int)
    codcid = request.args.get("codcid", 0, type=int)
    codbai = request.args.get("codbai", 0, type=int)
    codtipparc = request.args.get("codtipparc", 0, type=int)
    codtipvenda = request.args.get("codtipvenda", 0, type=int)
    codlocal = request.args.get("codlocal", 0, type=int)

    # Identificar a regra TIPTABPRECO configurada no Sankhya
    tiptabpreco_param = sankhya.obter_parametro_tiptabpreco()

    # Não carregar produtos na tela de consulta até o usuário clicar em Buscar Produtos
    if not busca:
        lista_de_produtos = []
        total_registros = 0
        total_paginas = 0
    else:
        # Na visualização ampla com busca, buscamos 15 produtos por página
        lista_de_produtos, total_registros, total_paginas = sankhya.listar_produtos(
            pagina=pagina,
            itens_por_pagina=15,
            busca=busca,
            codemp=codemp,
            codparc=codparc,
            codvend=codvend,
            codreg=codreg,
            codcid=codcid,
            codbai=codbai,
            codtipparc=codtipparc,
            codtipvenda=codtipvenda,
            codlocal=codlocal
        )

    return render_template(
        "consulta_produtos.html",
        produtos=lista_de_produtos,
        pagina_atual=pagina,
        total_paginas=total_paginas,
        total_registros=total_registros,
        busca=busca,
        tiptabpreco_param=tiptabpreco_param,
        filtros_preco={
            "codemp": codemp,
            "codparc": codparc,
            "codvend": codvend,
            "codreg": codreg,
            "codcid": codcid,
            "codbai": codbai,
            "codtipparc": codtipparc,
            "codtipvenda": codtipvenda,
            "codlocal": codlocal
        }
    )


# ==========================================
# 4. PROMOÇÕES ATIVAS
# ==========================================
@app.route("/promocoes")
def promocoes():
    if "usuario" not in session:
        return redirect(url_for("login"))

    opcoes_filtros = sankhya.obter_opcoes_filtros_promocoes()
    hoje_fmt = datetime.now().strftime("%d/%m/%Y")
    hoje_iso = datetime.now().strftime("%Y-%m-%d")

    return render_template(
        "promocoes.html",
        opcoes_filtros=opcoes_filtros,
        empresas=opcoes_filtros.get("empresas", []),
        marcas=opcoes_filtros.get("marcas", []),
        grupos=opcoes_filtros.get("grupos", []),
        data_ini_sugerida=hoje_iso,
        data_fin_sugerida=hoje_iso,
        hoje_fmt=hoje_fmt,
        hoje_iso=hoje_iso
    )


@app.route("/api/promocoes/consultar", methods=["GET", "POST"])
def api_consultar_promocoes():
    if "usuario" not in session:
        return jsonify({"sucesso": False, "erro": "Usuário não autenticado"}), 401

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
    else:
        payload = request.args.to_dict()

    busca = payload.get("busca", "").strip()
    pagina = int(payload.get("pagina", 1) or 1)
    limite = int(payload.get("limite", 50) or 50)

    data_ini = payload.get("data_ini", "").strip()
    data_fin = payload.get("data_fin", "").strip()

    if data_ini and "-" in data_ini:
        try:
            data_ini = datetime.strptime(data_ini, "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            pass
    if data_fin and "-" in data_fin:
        try:
            data_fin = datetime.strptime(data_fin, "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            pass

    raw_emp = payload.get("empresas") or []
    if isinstance(raw_emp, str):
        empresas = [e.strip() for e in raw_emp.split(",") if e.strip()]
    elif isinstance(raw_emp, list):
        empresas = raw_emp
    else:
        empresas = []

    raw_marcas = payload.get("marcas") or []
    if isinstance(raw_marcas, str):
        marcas = [m.strip() for m in raw_marcas.split(",") if m.strip()]
    elif isinstance(raw_marcas, list):
        marcas = raw_marcas
    else:
        marcas = []

    raw_grupos = payload.get("grupos") or []
    if isinstance(raw_grupos, str):
        grupos = [g.strip() for g in raw_grupos.split(",") if g.strip()]
    elif isinstance(raw_grupos, list):
        grupos = raw_grupos
    else:
        grupos = []

    codemp_loja = session.get("usuario", {}).get("codemp", 1)

    filtros = {
        "busca": busca,
        "data_ini": data_ini,
        "data_fin": data_fin,
        "empresas": empresas,
        "marcas": marcas,
        "grupos": grupos,
        "codemp_loja": codemp_loja
    }

    try:
        resultado = sankhya.consultar_promocoes_ativas(filtros)
        todos = resultado.get("produtos", [])
        total = len(todos)
        total_paginas = (total + limite - 1) // limite if total > 0 else 1

        if pagina < 1:
            pagina = 1
        if pagina > total_paginas:
            pagina = total_paginas

        inicio = (pagina - 1) * limite
        fim = inicio + limite
        pagina_itens = todos[inicio:fim]

        return jsonify({
            "sucesso": True,
            "total": total,
            "pagina": pagina,
            "limite": limite,
            "total_paginas": total_paginas,
            "produtos": pagina_itens,
            "estatisticas": resultado.get("estatisticas", {})
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Erro ao consultar promoções: {e}", exc_info=True)
        return jsonify({"sucesso": False, "erro": str(e)}), 500


# ==========================================
# 5. BI (BUSINESS INTELLIGENCE)
# ==========================================
@app.route("/bi")
def bi():
    return render_template("bi.html")


# ==========================================
# 6. GESTÃO DE ACESSOS & PERMISSÕES DO ORDERFLOW (SUPER ADMIN & ADM)
# ==========================================
@app.route("/acessos")
@app.route("/sobre")
def acessos():
    user = session.get("usuario", {})
    # Apenas Super Admin ou Administrador local autorizado tem acesso
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return redirect(url_for("home"))

    grupos_permitidos = orderflow_auth.listar_grupos_permitidos(Config.TENANT_ID)
    usuarios_permitidos = orderflow_auth.listar_usuarios_permitidos(Config.TENANT_ID)
    sessoes_ativas = orderflow_auth.listar_sessoes_ativas_tenant(Config.TENANT_ID)

    # Busca listas completas do Sankhya para os selects de inclusão rápida
    grupos_sankhya = sankhya.listar_grupos_sankhya()
    usuarios_sankhya = sankhya.listar_usuarios_sankhya()

    return render_template(
        "acessos.html",
        grupos_permitidos=grupos_permitidos,
        usuarios_permitidos=usuarios_permitidos,
        sessoes_ativas=sessoes_ativas,
        token_sessao_atual=session.get("session_token", ""),
        grupos_sankhya=grupos_sankhya,
        usuarios_sankhya=usuarios_sankhya,
        is_superadmin=user.get("is_superadmin", False)
    )


# --- APIs: CRUD DE GRUPOS PERMITIDOS ---
@app.route("/api/acessos/grupos/adicionar", methods=["POST"])
def api_acessos_grupo_adicionar():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    codgrupo = dados.get("codgrupo")
    nomegrupo = dados.get("nomegrupo", "").strip()

    if not codgrupo:
        return jsonify({"status": "0", "mensagem": "Por favor, informe o grupo."}), 400

    orderflow_auth.adicionar_ou_atualizar_grupo_permitido(Config.TENANT_ID, codgrupo, nomegrupo)
    return jsonify({"status": "1", "mensagem": f"Grupo '{nomegrupo or codgrupo}' permitido com sucesso!"})


@app.route("/api/acessos/grupos/alternar-status", methods=["POST"])
def api_acessos_grupo_alternar():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    id_grupo = dados.get("id")
    if not id_grupo:
        return jsonify({"status": "0", "mensagem": "ID do grupo não informado."}), 400

    orderflow_auth.alternar_status_grupo_permitido(Config.TENANT_ID, id_grupo)
    return jsonify({"status": "1", "mensagem": "Status do grupo alterado com sucesso!"})


@app.route("/api/acessos/grupos/excluir", methods=["POST"])
def api_acessos_grupo_excluir():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    id_grupo = dados.get("id")
    if not id_grupo:
        return jsonify({"status": "0", "mensagem": "ID do grupo não informado."}), 400

    orderflow_auth.excluir_grupo_permitido(Config.TENANT_ID, id_grupo)
    return jsonify({"status": "1", "mensagem": "Grupo removido da lista de permitidos!"})


# --- APIs: CRUD DE USUÁRIOS PERMITIDOS ---
@app.route("/api/acessos/usuarios/adicionar", methods=["POST"])
def api_acessos_usuario_adicionar():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    codusu = dados.get("codusu")
    nomeusu = dados.get("nomeusu", "").strip()
    nomevend = dados.get("nomevend", "-").strip()
    is_admin = int(dados.get("is_admin", 0))

    if codusu is None or not nomeusu:
        return jsonify({"status": "0", "mensagem": "Por favor, selecione um usuário válido."}), 400

    orderflow_auth.adicionar_ou_atualizar_usuario_permitido(Config.TENANT_ID, codusu, nomeusu, nomevend, is_admin)
    return jsonify({"status": "1", "mensagem": f"Usuário '{nomeusu}' permitido com sucesso!"})


@app.route("/api/acessos/usuarios/alternar-status", methods=["POST"])
def api_acessos_usuario_alternar():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    id_usuario = dados.get("id")
    if not id_usuario:
        return jsonify({"status": "0", "mensagem": "ID do usuário não informado."}), 400

    orderflow_auth.alternar_status_usuario_permitido(Config.TENANT_ID, id_usuario)
    return jsonify({"status": "1", "mensagem": "Status do usuário alterado com sucesso!"})


@app.route("/api/acessos/usuarios/alternar-admin", methods=["POST"])
def api_acessos_usuario_alternar_admin():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    id_usuario = dados.get("id")
    if not id_usuario:
        return jsonify({"status": "0", "mensagem": "ID do usuário não informado."}), 400

    orderflow_auth.alternar_perfil_admin_usuario(Config.TENANT_ID, id_usuario)
    return jsonify({"status": "1", "mensagem": "Perfil de administrador alterado com sucesso!"})


@app.route("/api/acessos/usuarios/excluir", methods=["POST"])
def api_acessos_usuario_excluir():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    id_usuario = dados.get("id")
    if not id_usuario:
        return jsonify({"status": "0", "mensagem": "ID do usuário não informado."}), 400

    orderflow_auth.excluir_usuario_permitido(Config.TENANT_ID, id_usuario)
    return jsonify({"status": "1", "mensagem": "Usuário removido da lista de permitidos!"})


# --- APIs: GESTÃO DE SESSÕES ATIVAS (KILL SESSION) ---
@app.route("/api/acessos/sessoes/encerrar", methods=["POST"])
def api_acessos_sessao_encerrar():
    user = session.get("usuario", {})
    if not user.get("is_superadmin") and not user.get("is_adm"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado."}), 403

    dados = request.get_json() or {}
    session_id = dados.get("session_id")
    token = dados.get("session_token")

    if not session_id and not token:
        return jsonify({"status": "0", "mensagem": "Sessão não informada."}), 400

    orderflow_auth.encerrar_sessao_ativa(Config.TENANT_ID, session_id=session_id, session_token=token)
    return jsonify({"status": "1", "mensagem": "Sessão derrubada/encerrada com sucesso!"})


# ==========================================
# 7. CONFIGURAÇÕES & APARÊNCIA DO SISTEMA
# ==========================================
@app.route("/configuracoes")
@app.route("/contato")
def configuracoes():
    if "usuario" not in session:
        return redirect(url_for("login"))

    sankhya_url = SANKHYA_URL or os.getenv("SANKHYA_BASE_URL", "https://autogiro.nuvemdatacom.com.br:8035")
    ambiente = os.getenv("AMBIENTE", "teste")
    base_sankhya = "Teste" if ("teste" in ambiente.lower() or "8035" in str(sankhya_url)) else "Produção"
    sankhya_username = SANKHYA_USER or os.getenv("SANKHYA_USERNAME", "integra.api")

    return render_template(
        "configuracoes.html",
        sankhya_url=sankhya_url,
        base_sankhya=base_sankhya,
        sankhya_username=sankhya_username
    )


# ==========================================
# ROTA PARA SERVIR A IMAGEM DO PRODUTO (TGFPRO.IMAGEM)
# ==========================================
@app.route("/produto/<int:codprod>/imagem")
def produto_imagem(codprod):
    img_bytes, mimetype = sankhya.obter_imagem_produto(codprod)
    if img_bytes and mimetype:
        from flask import Response
        return Response(img_bytes, mimetype=mimetype)
    else:
        # Se não houver imagem no banco, retorna 404
        from flask import abort
        return abort(404)


# ==========================================
# ROTA API: SALVAR CABEÇALHO DO PEDIDO (TGFCAB)
# ==========================================
@app.route("/api/pedidos/salvar-cabecalho", methods=["POST"])
def api_salvar_cabecalho_pedido():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    user = session.get("usuario", {})
    dados = request.get_json() or {}
    codemp = dados.get("codemp") or user.get("codemp", 1)
    codparc = dados.get("codparc")
    codtipoper = dados.get("codtipoper")
    codtipvenda = dados.get("codtipvenda")
    dtneg = dados.get("dtneg")
    codvend = dados.get("codvend") or user.get("codvend", 0)
    observacao = dados.get("observacao", "")
    cif_fob = dados.get("cif_fob", "") or ""
    vlrfrete = dados.get("vlrfrete", 0.0)
    nunota = dados.get("nunota")
    codusu = user.get("codusu")

    if not codparc:
        return jsonify({"status": "0", "mensagem": "Parceiro é obrigatório para salvar o pedido."}), 400
    if not codtipoper:
        return jsonify({"status": "0", "mensagem": "Tipo de Operação (TOP) é obrigatório."}), 400
    
    top_valida = sankhya.obter_tipo_operacao(codtipoper)
    if not top_valida:
        return jsonify({"status": "0", "mensagem": f"Tipo de Operação #{codtipoper} não é permitido para a Central de Pedidos. Selecione uma das TOPs válidas no modal."}), 400

    if not codtipvenda:
        return jsonify({"status": "0", "mensagem": "Tipo de Negociação é obrigatório."}), 400

    if not cif_fob.strip():
        # Condição de Frete é validada apenas ao fechar pedido, não ao salvar cabeçalho
        cif_fob = "C"  # Usa CIF como padrão temporário até o fechamento

    if nunota:
        faturado, _ = sankhya.verificar_pedido_faturado(nunota)
        if faturado:
            return jsonify({"status": "0", "mensagem": "O pedido já foi faturado e gerou uma nota de venda, portanto não pode ser alterado."}), 400

    sucesso, nunota_retorno, mensagem = sankhya.salvar_cabecalho_pedido(
        codemp=codemp,
        codparc=codparc,
        codtipoper=codtipoper,
        codtipvenda=codtipvenda,
        dtneg=dtneg,
        codvend=codvend,
        observacao=observacao,
        cif_fob=cif_fob,
        vlrfrete=vlrfrete,
        nunota=nunota,
        codusu=codusu,
        jsessionid=user.get("jsessionid")
    )


    if sucesso and nunota_retorno:
        return jsonify({
            "status": "1",
            "nunota": nunota_retorno,
            "mensagem": f"Cabeçalho do Pedido #{nunota_retorno} salvo com sucesso!"
        })
    else:
        return jsonify({
            "status": "0",
            "mensagem": mensagem or "Erro ao salvar cabeçalho do pedido no Sankhya."
        }), 500


# ==========================================
# ROTA API: ATUALIZAR CAMPO DO CABEÇALHO (TGFCAB)
# ==========================================
@app.route("/api/pedidos/atualizar-cabecalho", methods=["POST"])
def api_atualizar_cabecalho_pedido():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    user = session.get("usuario", {})
    dados = request.get_json() or {}
    nunota = dados.get("nunota")
    campo = dados.get("campo")
    valor = dados.get("valor")
    codusu = user.get("codusu")

    if not nunota:
        return jsonify({"status": "0", "mensagem": "Identificador do pedido é obrigatório para atualização do cabeçalho."}), 400
    if not campo:
        return jsonify({"status": "0", "mensagem": "Nome do campo é obrigatório."}), 400

    faturado, _ = sankhya.verificar_pedido_faturado(nunota)
    if faturado:
        return jsonify({"status": "0", "mensagem": "O pedido já foi faturado e gerou uma nota de venda, portanto não pode ser alterado."}), 400

    sucesso, mensagem = sankhya.atualizar_campo_cabecalho(
        nunota=nunota,
        campo=campo,
        valor=valor,
        codusu=codusu,
        jsessionid=user.get("jsessionid")
    )

    if sucesso:
        return jsonify({
            "status": "1",
            "nunota": nunota,
            "campo": campo,
            "valor": valor,
            "mensagem": mensagem
        })
    else:
        return jsonify({
            "status": "0",
            "mensagem": mensagem or f"Erro ao atualizar campo {campo} do pedido."
        }), 500


# ==========================================
# ROTA API: SALVAR ITEM DO PEDIDO (TGFITE)
# ==========================================
@app.route("/api/pedidos/salvar-item", methods=["POST"])
def api_salvar_item_pedido():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    user = session.get("usuario", {})
    dados = request.get_json() or {}
    nunota = dados.get("nunota")
    codprod = dados.get("codprod")
    qtdneg = dados.get("qtdneg")
    vlrunit = dados.get("vlrunit")
    vlrtot = dados.get("vlrtot")
    vlrdesc = dados.get("vlrdesc", 0)
    percdesc = dados.get("percdesc", 0)
    sequencia = dados.get("sequencia")
    codemp = dados.get("codemp") or user.get("codemp", 1)
    codlocalorig = dados.get("codlocalorig", 1000000)
    codvol = dados.get("codvol", "UN")
    codusu = user.get("codusu")

    if not nunota:
        return jsonify({"status": "0", "mensagem": "Identificador do pedido é obrigatório para salvar o item."}), 400
    if not codprod:
        return jsonify({"status": "0", "mensagem": "Código do produto é obrigatório."}), 400
    if not qtdneg or float(qtdneg) <= 0:
        return jsonify({"status": "0", "mensagem": "A quantidade deve ser maior que zero."}), 400

    faturado, _ = sankhya.verificar_pedido_faturado(nunota)
    if faturado:
        return jsonify({"status": "0", "mensagem": "O pedido já foi faturado e gerou uma nota de venda, portanto não pode receber alterações ou novos itens."}), 400

    sucesso, seq_salva, mensagem = sankhya.salvar_item_pedido(
        nunota=nunota,
        codprod=codprod,
        qtdneg=qtdneg,
        vlrunit=vlrunit,
        vlrtot=vlrtot,
        vlrdesc=vlrdesc,
        percdesc=percdesc,
        sequencia=sequencia,
        codemp=codemp,
        codlocalorig=codlocalorig,
        codvol=codvol,
        codusu=codusu,
        jsessionid=user.get("jsessionid")
    )

    if sucesso and seq_salva:
        return jsonify({
            "status": "1",
            "nunota": nunota,
            "sequencia": seq_salva,
            "mensagem": f"Item #{codprod} salvo com sucesso (Sequência {seq_salva})!"
        })
    else:
        return jsonify({
            "status": "0",
            "mensagem": mensagem or "Erro ao salvar item do pedido."
        }), 500


# ==========================================
# ROTA API: EXCLUIR ITEM DO PEDIDO (TGFITE)
# ==========================================
@app.route("/api/pedidos/excluir-item", methods=["POST"])
def api_excluir_item_pedido():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    user = session.get("usuario", {})
    dados = request.get_json() or {}
    nunota = dados.get("nunota")
    sequencia = dados.get("sequencia")

    if not nunota or not sequencia:
        return jsonify({"status": "0", "mensagem": "Identificador do pedido e sequência são obrigatórios para exclusão do item."}), 400

    faturado, _ = sankhya.verificar_pedido_faturado(nunota)
    if faturado:
        return jsonify({"status": "0", "mensagem": "O pedido já foi faturado e gerou uma nota de venda, portanto seus itens não podem ser excluídos."}), 400

    sucesso, mensagem = sankhya.excluir_item_pedido(
        nunota=nunota,
        sequencia=sequencia,
        codusu=user.get("codusu"),
        jsessionid=user.get("jsessionid")
    )

    if sucesso:
        return jsonify({
            "status": "1",
            "nunota": nunota,
            "sequencia": sequencia,
            "mensagem": f"Item Seq. {sequencia} excluído com sucesso!"
        })
    else:
        return jsonify({
            "status": "0",
            "mensagem": mensagem or "Erro ao excluir item do pedido."
        }), 500


# ==========================================
# ROTA API: EXCLUIR PEDIDO COMPLETO (TGFCAB)
# ==========================================
@app.route("/api/pedidos/excluir", methods=["POST"])
def api_excluir_pedido():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    dados = request.get_json() or {}
    nunota = dados.get("nunota")
    motivo = dados.get("motivo")
    observacao_motivo = dados.get("observacao_motivo")

    if not nunota:
        return jsonify({"status": "0", "mensagem": "Identificador do pedido é obrigatório para exclusão."}), 400

    faturado, statusnota = sankhya.verificar_pedido_faturado(nunota)
    if faturado:
        return jsonify({"status": "0", "mensagem": "O pedido já foi faturado e gerou uma nota de venda, portanto não pode ser excluído."}), 400

    if statusnota == "L" and not motivo:
        return jsonify({"status": "0", "mensagem": "Para pedidos confirmados, é obrigatório selecionar o motivo do cancelamento."}), 400

    sucesso, mensagem = sankhya.excluir_pedido(nunota=nunota, motivo=motivo, observacao=observacao_motivo)

    if sucesso:
        return jsonify({
            "status": "1",
            "nunota": nunota,
            "mensagem": mensagem or f"Pedido #{nunota} excluído com sucesso do Sankhya!"
        })
    else:
        return jsonify({
            "status": "0",
            "mensagem": mensagem or "Erro ao excluir pedido no Sankhya."
        }), 400


# ==========================================
# ROTAS API: VALIDAÇÃO DE LIMITES E LIBERAÇÕES (SANKHYA OM / TSILIB)
# ==========================================
@app.route("/api/pedidos/<int:nunota>/liberacoes", methods=["GET"])
def api_consultar_liberacoes(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    codparc = request.args.get("codparc", type=int)
    vlr_total = request.args.get("vlr_total", type=float)

    if not codparc or vlr_total is None:
        ped = sankhya.obter_pedido_completo(nunota)
        if ped and ped.get("cabecalho"):
            if not codparc:
                codparc = ped["cabecalho"].get("codparc", 0)
            if vlr_total is None:
                vlr_total = ped["cabecalho"].get("vlrnota", 0.0)

    user = session.get("usuario", {})
    dados = sankhya.consultar_limites_liberacoes(
        nunota=nunota,
        codparc=codparc or 0,
        vlr_total=vlr_total or 0.0,
        codusu=user.get("codusu", 1)
    )
    return jsonify({
        "status": "1",
        "dados": dados,
        "is_adm": bool(user.get("is_adm"))
    })


@app.route("/api/pedidos/<int:nunota>/solicitar-liberacao", methods=["POST"])
def api_solicitar_liberacao(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    payload = request.get_json() or {}
    codparc = payload.get("codparc")
    vlr_total = payload.get("vlr_total", 0.0)
    motivo = payload.get("motivo")

    if not codparc:
        ped = sankhya.obter_pedido_completo(nunota)
        if ped and ped.get("cabecalho"):
            codparc = ped["cabecalho"].get("codparc", 0)
            if not vlr_total:
                vlr_total = ped["cabecalho"].get("vlrnota", 0.0)

    user = session.get("usuario", {})
    sucesso, msg = sankhya.solicitar_liberacao_limites(
        nunota=nunota,
        codparc=codparc or 0,
        vlr_total=float(vlr_total or 0.0),
        motivo=motivo,
        codusu=user.get("codusu", 1),
        jsessionid=user.get("jsessionid")
    )
    return jsonify({
        "status": "1" if sucesso else "0",
        "mensagem": msg
    })


@app.route("/api/usuarios-liberadores", methods=["GET"])
def api_usuarios_liberadores():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado", "session_expired": True}), 401

    try:
        busca = request.args.get("busca", "").strip()
        evento_param = request.args.get("evento", "").strip()
        evento = int(evento_param) if evento_param.isdigit() else None
        usuarios = sankhya.listar_usuarios_liberadores(busca=busca, evento=evento)

        return jsonify({
            "status": "1",
            "usuarios": usuarios
        })
    except Exception as e:
        print(f"[ERRO /api/usuarios-liberadores] {e}")
        return jsonify({"status": "0", "mensagem": "Erro interno ao listar usuários liberadores."}), 500


@app.route("/api/pedidos/<int:nunota>/definir-liberador", methods=["POST"])
def api_definir_liberador(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    payload = request.get_json() or {}
    codusu_liberador = payload.get("codusu_liberador")
    eventos = payload.get("eventos")
    liberadores = payload.get("liberadores")

    if not codusu_liberador and not liberadores:
        return jsonify({"status": "0", "mensagem": "Informe os liberadores dos eventos."}), 400

    user = session.get("usuario", {})
    codusu_solicitante = user.get("codusu", 1)

    sucesso, msg = sankhya.definir_liberador_eventos(
        nunota=nunota,
        codusu_liberador=codusu_liberador,
        eventos=eventos,
        liberadores=liberadores,
        codusu_solicitante=codusu_solicitante,
        jsessionid=user.get("jsessionid")
    )

    return jsonify({
        "status": "1" if sucesso else "0",
        "mensagem": msg
    })


@app.route("/api/pedidos/<int:nunota>/liberar", methods=["POST"])
def api_liberar_pedido(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    user = session.get("usuario", {})
    if not user.get("is_adm"):
        return jsonify({
            "status": "0",
            "mensagem": "Apenas usuários administradores têm permissão para liberar limites no Sankhya."
        }), 403

    payload = request.get_json() or {}
    observacao = payload.get("observacao")

    sucesso, msg = sankhya.liberar_limites_pedido(
        nunota=nunota,
        observacao_lib=observacao,
        codusu_liberador=user.get("codusu", 1),
        jsessionid=user.get("jsessionid")
    )
    return jsonify({
        "status": "1" if sucesso else "0",
        "mensagem": msg
    })


@app.route("/api/pedidos/<int:nunota>/impressao", methods=["GET"])
def api_imprimir_pedido(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    sucesso, pdf_bytes, filename = sankhya.gerar_pdf_pedido(nunota)
    if not sucesso or not pdf_bytes:
        return jsonify({"status": "0", "mensagem": filename or "Erro ao gerar impressão do pedido."}), 400

    from flask import Response
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"{filename}\"",
            "Content-Type": "application/pdf"
        }
    )


@app.route("/api/impressoras", methods=["GET"])
def api_listar_impressoras():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401
    
    impressoras = sankhya.obter_impressoras_rede()
    return jsonify({
        "status": "1",
        "impressoras": impressoras
    })


@app.route("/api/pedidos/<int:nunota>/imprimir-servidor", methods=["POST"])
def api_imprimir_pedido_servidor(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    payload = request.get_json() or {}
    impressora = payload.get("impressora")
    nusvp = payload.get("nusvp", 1)
    copias = payload.get("copias", 1)

    if not impressora:
        return jsonify({"status": "0", "mensagem": "Selecione a impressora de destino."}), 400

    sucesso, mensagem = sankhya.imprimir_pedido_servidor(
        nunota=nunota,
        impressora_nome=impressora,
        nusvp=nusvp,
        copias=copias
    )
    if not sucesso:
        return jsonify({"status": "0", "mensagem": mensagem}), 400

    return jsonify({"status": "1", "mensagem": mensagem})


@app.route("/api/pedidos/<int:nunota>/fechar", methods=["POST"])
def api_fechar_pedido(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    payload = request.get_json() or {}
    codemp = payload.get("codemp")
    codparc = payload.get("codparc")
    vlr_total = payload.get("vlr_total")
    itens = payload.get("itens")

    if not codparc or vlr_total is None or not codemp:
        ped = sankhya.obter_pedido_completo(nunota)
        if ped and ped.get("cabecalho"):
            cab = ped["cabecalho"]
            if not codemp:
                codemp = cab.get("codemp", 1)
            if not codparc:
                codparc = cab.get("codparc", 0)
            if vlr_total is None:
                vlr_total = cab.get("vlrnota", 0.0)
            if not itens:
                itens = ped.get("itens", [])

    user = session.get("usuario", {})
    res = sankhya.fechar_pedido_sankhya(
        nunota=nunota,
        codemp=codemp or 1,
        codparc=codparc or 0,
        vlr_total=float(vlr_total or 0.0),
        itens=itens,
        codusu=user.get("codusu", 1),
        jsessionid=user.get("jsessionid")
    )

    # Se a sessão Sankhya foi renovada durante a confirmação, sincroniza na sessão Flask
    if sankhya.jsessionid and user.get("jsessionid") != sankhya.jsessionid:
        if "usuario" in session:
            session["usuario"]["jsessionid"] = sankhya.jsessionid
            session.modified = True

    return jsonify(res)


@app.route("/api/pedidos/<int:nunota>/visualizar-pix", methods=["GET"])
@app.route("/api/pedidos/visualizar-pix/<int:nunota>", methods=["GET"])
def api_visualizar_pix(nunota):
    if "usuario" not in session:
        return jsonify({"status": "0", "sucesso": False, "mensagem": "Não autenticado"}), 401

    res = sankhya.obter_pix_copiacola(nunota)
    return jsonify(res)


# ==========================================
# ROTA API: CONSULTA DE PRODUTOS (PAGINADA)
# ==========================================
@app.route("/api/produtos")
def api_produtos():
    if "usuario" not in session:
        return jsonify({"status": "0", "mensagem": "Não autenticado"}), 401

    pagina = request.args.get("pagina", 1, type=int)
    busca = request.args.get("busca", "").strip()
    itens_por_pagina = request.args.get("limite", type=int) or request.args.get("itens_por_pagina", 15, type=int)

    if pagina < 1:
        pagina = 1

    if not busca:
        return jsonify({
            "status": "1",
            "produtos": [],
            "pagina_atual": 1,
            "total_paginas": 0,
            "total_registros": 0,
            "busca": ""
        })

    codemp = request.args.get("codemp", type=int) or session.get("usuario", {}).get("codemp", 1)
    codparc = request.args.get("codparc", 0, type=int)
    produtos, total_registros, total_paginas = sankhya.listar_produtos(
        pagina=pagina,
        itens_por_pagina=itens_por_pagina,
        busca=busca,
        codemp=codemp,
        codparc=codparc
    )

    return jsonify({
        "status": "1",
        "produtos": produtos,
        "pagina_atual": pagina,
        "total_paginas": total_paginas,
        "total_registros": total_registros,
        "busca": busca
    })


# ==========================================
# ROTA API: PRODUTOS ALTERNATIVOS (TGFPAL)
# ==========================================
@app.route("/api/produtos/<int:codprod>/alternativos")
def api_produtos_alternativos(codprod):
    codemp = request.args.get("codemp", type=int) or session.get("usuario", {}).get("codemp", 1)
    codparc = request.args.get("codparc", type=int) or 0
    alternativos = sankhya.listar_produtos_alternativos(codprod, codemp=codemp, codparc=codparc)
    return jsonify({
        "codprod": codprod,
        "total": len(alternativos),
        "alternativos": alternativos
    })


# ==========================================
# ROTA API: PRODUTOS SUGERIDOS (TGFVCS)
# ==========================================
@app.route("/api/produtos/<int:codprod>/sugeridos")
def api_produtos_sugeridos(codprod):
    codemp = request.args.get("codemp", type=int) or session.get("usuario", {}).get("codemp", 1)
    codparc = request.args.get("codparc", type=int) or 0
    sugeridos = sankhya.listar_produtos_sugeridos(codprod, codemp=codemp, codparc=codparc)
    return jsonify({
        "codprod": codprod,
        "total": len(sugeridos),
        "sugeridos": sugeridos
    })


# ==========================================
# ROTA API: ESTOQUE DETALHADO (TGFEST)
# ==========================================
@app.route("/api/produtos/<int:codprod>/estoque")
def api_produto_estoque_detalhado(codprod):
    estoque = sankhya.obter_estoque_detalhado_tgfest(codprod)
    return jsonify({
        "status": "1",
        "codprod": codprod,
        "total": len(estoque),
        "estoque": estoque
    })



# ==========================================
# ROTAS API: PARCEIROS (TGFPAR) & EMPRESA (TSIEMP)
# ==========================================
@app.route("/api/parceiros", methods=["GET"])
def api_buscar_parceiros():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    busca = request.args.get("q", "").strip() or request.args.get("busca", "").strip()
    limite = request.args.get("limite", 50, type=int)
    
    parceiros = sankhya.buscar_parceiros(busca=busca, limite=limite)
    return jsonify({
        "status": "1",
        "total": len(parceiros),
        "parceiros": parceiros
    })


@app.route("/api/parceiros/<int:codparc>", methods=["GET"])
def api_obter_parceiro(codparc):
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    parceiro = sankhya.obter_parceiro(codparc)
    if parceiro:
        tabela_preco = sankhya.obter_tabela_preco_parceiro(codparc)
        limite_credito = sankhya.obter_limite_credito_parceiro(codparc)
        return jsonify({
            "status": "1", 
            "parceiro": parceiro,
            "tabela_preco": tabela_preco,
            "limite_credito": limite_credito
        })
    else:
        return jsonify({"status": "0", "msg": "Parceiro não encontrado"}), 404


@app.route("/api/parceiros/<int:codparc>/tabela-preco", methods=["GET"])
def api_obter_tabela_preco_parceiro(codparc):
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    tabela_preco = sankhya.obter_tabela_preco_parceiro(codparc)
    return jsonify({
        "status": "1",
        "codparc": codparc,
        "tabela_preco": tabela_preco
    })


@app.route("/api/parceiros/<int:codparc>/limite-credito", methods=["GET"])
def api_obter_limite_credito_parceiro(codparc):
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    limite_credito = sankhya.obter_limite_credito_parceiro(codparc)
    return jsonify({
        "status": "1",
        "codparc": codparc,
        "limite_credito": limite_credito
    })


@app.route("/api/parceiros/<int:codparc>/status-financeiro", methods=["GET"])
def api_obter_status_financeiro_parceiro(codparc):
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    status_fin = sankhya.obter_status_financeiro_parceiro(codparc)
    return jsonify({
        "status": "1",
        "codparc": codparc,
        "dados": status_fin
    })


# ==========================================
# ROTAS API: VENDEDORES (TGFVEN)
# ==========================================
@app.route("/api/vendedores", methods=["GET"])
def api_buscar_vendedores():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    busca = request.args.get("q", "").strip() or request.args.get("busca", "").strip()
    limite = request.args.get("limite", 50, type=int)
    
    vendedores = sankhya.buscar_vendedores(busca=busca, limite=limite)
    return jsonify({
        "status": "1",
        "total": len(vendedores),
        "vendedores": vendedores
    })


@app.route("/api/vendedores/<int:codvend>", methods=["GET"])
def api_obter_vendedor(codvend):
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    vendedor = sankhya.obter_vendedor(codvend)
    if vendedor:
        return jsonify({"status": "1", "vendedor": vendedor})
    return jsonify({"status": "0", "msg": "Vendedor não encontrado"}), 404


@app.route("/api/empresas", methods=["GET"])
def api_buscar_empresas():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    busca = request.args.get("q", "").strip() or request.args.get("busca", "").strip()
    limite = request.args.get("limite", 50, type=int)
    
    empresas = sankhya.buscar_empresas(busca=busca, limite=limite)
    return jsonify({
        "status": "1",
        "total": len(empresas),
        "empresas": empresas
    })


@app.route("/api/empresa", methods=["GET"])
def api_obter_empresa():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    codemp = session.get("usuario", {}).get("codemp", 1)
    empresa = sankhya.obter_empresa(codemp)
    return jsonify({"status": "1", "empresa": empresa})


@app.route("/api/tipos-operacao", methods=["GET"])
def api_listar_tipos_operacao():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    tipos = sankhya.listar_tipos_operacao()
    return jsonify({
        "status": "1",
        "total": len(tipos),
        "tipos_operacao": tipos
    })


@app.route("/api/tipos-operacao/<int:codtipoper>", methods=["GET"])
def api_obter_tipo_operacao(codtipoper):
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    tipo = sankhya.obter_tipo_operacao(codtipoper)
    if tipo:
        return jsonify({"status": "1", "tipo_operacao": tipo})
    else:
        return jsonify({"status": "0", "msg": "Tipo de operação não encontrado"}), 404


@app.route("/api/tipos-negociacao", methods=["GET"])
def api_listar_tipos_negociacao():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    codparc = request.args.get("codparc", 1158, type=int)
    codtipoper = request.args.get("codtipoper", 10002, type=int)
    
    tipos = sankhya.listar_tipos_negociacao(codparc=codparc, codtipoper=codtipoper)
    neg_padrao = sankhya.obter_negociacao_padrao(codparc=codparc, codtipoper=codtipoper)
    sugestao_cod = sankhya.obter_sugestao_negociacao_parceiro(codparc)
    
    return jsonify({
        "status": "1",
        "total": len(tipos),
        "codparc": codparc,
        "codtipoper": codtipoper,
        "sugestao_cod": sugestao_cod,
        "neg_padrao": neg_padrao,
        "tipos_negociacao": tipos
    })


@app.route("/api/tipos-negociacao/<int:codtipvenda>", methods=["GET"])
def api_obter_tipo_negociacao(codtipvenda):
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    tipo = sankhya.obter_tipo_negociacao(codtipvenda)
    if tipo:
        return jsonify({"status": "1", "tipo_negociacao": tipo})
    else:
        return jsonify({"status": "0", "msg": "Tipo de negociação não encontrado"}), 404


@app.route("/api/regioes", methods=["GET"])
def api_buscar_regioes():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    busca = request.args.get("q", "").strip() or request.args.get("busca", "").strip()
    limite = request.args.get("limite", 50, type=int)
    regioes = sankhya.buscar_regioes(busca=busca, limite=limite)
    return jsonify({"status": "1", "total": len(regioes), "regioes": regioes})


@app.route("/api/cidades", methods=["GET"])
def api_buscar_cidades():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    busca = request.args.get("q", "").strip() or request.args.get("busca", "").strip()
    limite = request.args.get("limite", 50, type=int)
    cidades = sankhya.buscar_cidades(busca=busca, limite=limite)
    return jsonify({"status": "1", "total": len(cidades), "cidades": cidades})


@app.route("/api/bairros", methods=["GET"])
def api_buscar_bairros():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    busca = request.args.get("q", "").strip() or request.args.get("busca", "").strip()
    limite = request.args.get("limite", 50, type=int)
    bairros = sankhya.buscar_bairros(busca=busca, limite=limite)
    return jsonify({"status": "1", "total": len(bairros), "bairros": bairros})


@app.route("/api/locais", methods=["GET"])
def api_buscar_locais():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    busca = request.args.get("q", "").strip() or request.args.get("busca", "").strip()
    limite = request.args.get("limite", 50, type=int)
    locais = sankhya.buscar_locais(busca=busca, limite=limite)
    return jsonify({"status": "1", "total": len(locais), "locais": locais})






# ==========================================
# ROTA PRINCIPAL: CENTRAL DE PEDIDOS / PORTAL DE VENDAS
# ==========================================
@app.route("/central-pedidos")
@app.route("/portal-vendas")
@app.route("/pedidos")
def portal_vendas():
    if "usuario" not in session:
        return redirect(url_for("login"))

    user_info = session.get("usuario", {})
    user_codvend = user_info.get("codvend", 0)

    # Garante que TSIUSU.CODVEND seja obtido se não estiver na sessão
    if not user_codvend and user_info.get("codusu"):
        sql_u = f"SELECT NVL(CODVEND, 0) FROM TSIUSU WHERE CODUSU = {user_info['codusu']}"
        res_u = sankhya._executar_sql(sql_u)
        if res_u and res_u.get("status") == "1" and res_u["responseBody"].get("rows"):
            user_codvend = int(res_u["responseBody"]["rows"][0][0]) if res_u["responseBody"]["rows"][0][0] else 0
            session["usuario"]["codvend"] = user_codvend

    is_admin = bool(user_info.get("is_adm") or (user_info.get("usuario") and str(user_info.get("usuario")).strip().upper() in ["ADMIN", "SUP"]))

    # Verifica se há parâmetros passados via URL/Formulário
    has_query_params = bool(request.args)
    atualizar_param = request.args.get("atualizar", "").strip()

    if has_query_params:
        deve_carregar = bool(atualizar_param) or any([
            request.args.get("dt_ini"),
            request.args.get("dt_fim"),
            request.args.get("nunota"),
            request.args.get("codemp"),
            request.args.get("codparc"),
            request.args.get("codvend"),
            request.args.get("codprod"),
            request.args.get("pagina")
        ])

        pagina = request.args.get("pagina", 1, type=int)
        if pagina < 1:
            pagina = 1

        if is_admin:
            filtro_codvend = request.args.get("codvend", "")
        else:
            if user_codvend and int(user_codvend) > 0:
                filtro_codvend = int(user_codvend)
            else:
                filtro_codvend = request.args.get("codvend", "")

        filtro_codemp = request.args.get("codemp", "")
        filtro_dt_ini = request.args.get("dt_ini", "")
        filtro_dt_fim = request.args.get("dt_fim", "")
        filtro_nunota = request.args.get("nunota", "")
        filtro_numnota = ""
        filtro_codparc = request.args.get("codparc", "")
        filtro_codprod = request.args.get("codprod", "")

        # Salva o estado dos filtros e carregamento na sessão do usuário
        session["portal_vendas_filtros"] = {
            "dt_ini": filtro_dt_ini,
            "dt_fim": filtro_dt_fim,
            "nunota": filtro_nunota,
            "codemp": filtro_codemp,
            "codparc": filtro_codparc,
            "codvend": filtro_codvend,
            "codprod": filtro_codprod,
            "pagina": pagina,
            "carregado": deve_carregar
        }
    else:
        # Se não houver parâmetros na URL, recupera os filtros previamente salvos na sessão
        saved_filters = session.get("portal_vendas_filtros", {})
        if saved_filters:
            filtro_dt_ini = saved_filters.get("dt_ini", "")
            filtro_dt_fim = saved_filters.get("dt_fim", "")
            filtro_nunota = saved_filters.get("nunota", "")
            filtro_codemp = saved_filters.get("codemp", "")
            filtro_codparc = saved_filters.get("codparc", "")
            if is_admin:
                filtro_codvend = saved_filters.get("codvend", "")
            else:
                if user_codvend and int(user_codvend) > 0:
                    filtro_codvend = int(user_codvend)
                else:
                    filtro_codvend = saved_filters.get("codvend", "")
            filtro_codprod = saved_filters.get("codprod", "")
            filtro_numnota = ""
            pagina = saved_filters.get("pagina", 1)
            deve_carregar = bool(saved_filters.get("carregado", False))
        else:
            filtro_dt_ini = ""
            filtro_dt_fim = ""
            filtro_nunota = ""
            filtro_codemp = ""
            filtro_codparc = ""
            if not is_admin and user_codvend and int(user_codvend) > 0:
                filtro_codvend = int(user_codvend)
            else:
                filtro_codvend = ""
            filtro_codprod = ""
            filtro_numnota = ""
            pagina = 1
            deve_carregar = False

    # Nomes descritivos para os filtros
    if is_admin:
        nome_vendedor_filtrado = ""
        if filtro_codvend:
            v_info = sankhya.obter_vendedor(filtro_codvend)
            nome_vendedor_filtrado = v_info.get("nomevend", "") if v_info else ""
        else:
            nome_vendedor_filtrado = "Todos os vendedores"
    else:
        nome_vendedor_filtrado = user_info.get("nomevend", "")

    nome_parceiro_filtrado = ""
    if filtro_codparc:
        p_info = sankhya.obter_parceiro(filtro_codparc)
        nome_parceiro_filtrado = p_info.get("nomeparc") or p_info.get("razaosocial") if p_info else ""

    nome_empresa_filtrada = ""
    if filtro_codemp:
        e_info = sankhya.obter_empresa(filtro_codemp)
        nome_empresa_filtrada = e_info.get("nomeemp", "") if e_info else ""

    descr_produto_filtrado = ""
    if filtro_codprod:
        pr_info = sankhya.obter_produto(filtro_codprod)
        descr_produto_filtrado = pr_info.get("descrprod", "") if pr_info else ""

    pedidos = []
    total_registros = 0
    total_paginas = 0
    primeiro_nunota = None
    itens_primeiro = []

    if deve_carregar:
        pedidos, total_registros, total_paginas, pagina = sankhya.listar_pedidos_vendas(
            codvend=filtro_codvend,
            codemp=filtro_codemp,
            dt_ini=filtro_dt_ini,
            dt_fim=filtro_dt_fim,
            nunota=filtro_nunota,
            numnota=filtro_numnota,
            codparc=filtro_codparc,
            codprod=filtro_codprod,
            pagina=pagina,
            itens_por_pagina=15
        )

        primeiro_nunota = pedidos[0]["nunota"] if pedidos else None
        itens_primeiro = sankhya.listar_itens_pedido_venda(primeiro_nunota) if primeiro_nunota else []

    return render_template(
        "portal_vendas.html",
        pedidos=pedidos,
        carregado=deve_carregar,
        total_registros=total_registros,
        total_paginas=total_paginas,
        pagina_atual=pagina,
        itens_iniciais=itens_primeiro,
        primeiro_nunota=primeiro_nunota,
        filtro_codvend=filtro_codvend,
        nome_vendedor_filtrado=nome_vendedor_filtrado,
        filtro_codemp=filtro_codemp,
        nome_empresa_filtrada=nome_empresa_filtrada,
        filtro_dt_ini=filtro_dt_ini,
        filtro_dt_fim=filtro_dt_fim,
        filtro_nunota=filtro_nunota,
        filtro_numnota=filtro_numnota,
        filtro_codparc=filtro_codparc,
        nome_parceiro_filtrado=nome_parceiro_filtrado,
        filtro_codprod=filtro_codprod,
        descr_produto_filtrado=descr_produto_filtrado,
        user_info=user_info,
        is_admin=is_admin
    )


# ==========================================
# ROTA API: SALVAR FILTROS DO PORTAL NA SESSÃO
# ==========================================
@app.route("/api/portal-vendas/salvar-filtros", methods=["POST"])
def api_salvar_filtros_portal():
    if "usuario" not in session:
        return jsonify({"status": "0", "msg": "Não autenticado"}), 401
    
    dados = request.get_json(silent=True) or {}
    filtros_atuais = session.get("portal_vendas_filtros", {})
    filtros_atuais.update(dados)
    session["portal_vendas_filtros"] = filtros_atuais
    return jsonify({"status": "1", "msg": "Filtros salvos na sessão"})


# ==========================================
# ROTA API: LISTAR PEDIDOS (JSON)
# ==========================================
@app.route("/api/pedidos")
def api_listar_pedidos():
    if "usuario" not in session:
        return jsonify({"erro": "Não autenticado"}), 401

    user_info = session.get("usuario", {})
    user_codvend = user_info.get("codvend", 0)

    if not user_codvend and user_info.get("codusu"):
        sql_u = f"SELECT NVL(CODVEND, 0) FROM TSIUSU WHERE CODUSU = {user_info['codusu']}"
        res_u = sankhya._executar_sql(sql_u)
        if res_u and res_u.get("status") == "1" and res_u["responseBody"].get("rows"):
            user_codvend = int(res_u["responseBody"]["rows"][0][0]) if res_u["responseBody"]["rows"][0][0] else 0
            session["usuario"]["codvend"] = user_codvend

    is_admin = bool(user_info.get("is_adm") or (user_info.get("usuario") and str(user_info.get("usuario")).strip().upper() in ["ADMIN", "SUP"]))

    # Restringe ao vendedor do usuário logado se TSIUSU.CODVEND > 0 e NÃO for ADMIN
    if not is_admin and user_codvend and int(user_codvend) > 0:
        codvend = int(user_codvend)
    else:
        codvend = request.args.get("codvend")
    codemp = request.args.get("codemp")
    dt_ini = request.args.get("dt_ini")
    dt_fim = request.args.get("dt_fim")
    nunota = request.args.get("nunota")
    numnota = None
    codparc = request.args.get("codparc")
    codprod = request.args.get("codprod")
    pagina = request.args.get("pagina", 1, type=int)

    pedidos, total_registros, total_paginas, pagina = sankhya.listar_pedidos_vendas(
        codvend=codvend,
        codemp=codemp,
        dt_ini=dt_ini,
        dt_fim=dt_fim,
        nunota=nunota,
        numnota=numnota,
        codparc=codparc,
        codprod=codprod,
        pagina=pagina,
        itens_por_pagina=15
    )
    return jsonify({
        "total": total_registros,
        "total_paginas": total_paginas,
        "pagina": pagina,
        "pedidos": pedidos
    })


# ==========================================
# ROTA API: ITENS DO PEDIDO (JSON)
# ==========================================
@app.route("/api/pedidos/<int:nunota>/itens")
def api_itens_pedido(nunota):
    if "usuario" not in session:
        return jsonify({"erro": "Não autenticado"}), 401

    itens = sankhya.listar_itens_pedido_venda(nunota)
    return jsonify({
        "nunota": nunota,
        "total": len(itens),
        "itens": itens
    })


# ==========================================
# TRATAMENTO SEGURO DE ERROS (SEM VAZAMENTO DE DADOS SENSÍVEIS)
# ==========================================
@app.errorhandler(404)
def erro_nao_encontrado(e):
    if request.path.startswith("/api/"):
        return jsonify({"status": "0", "mensagem": "Recurso não encontrado."}), 404
    return render_template("index.html", erro="Página não encontrada."), 404


@app.errorhandler(403)
def erro_proibido(e):
    if request.path.startswith("/api/"):
        return jsonify({"status": "0", "mensagem": "Acesso não autorizado a este recurso."}), 403
    return redirect(url_for("home"))


@app.errorhandler(500)
def erro_interno_servidor(e):
    # Loga o erro internamente sem expor detalhes de banco, senhas ou queries para o cliente
    print(f"[Segurança - Erro 500] Erro interno capturado na rota '{request.path}': {e}")
    if request.path.startswith("/api/"):
        return jsonify({"status": "0", "mensagem": "Ocorreu um erro interno ao processar a requisição. Tente novamente."}), 500
    return render_template("index.html", erro="Ocorreu um erro ao processar sua solicitação."), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 6000))
    print(f"[OrderFlow Client] Iniciando cliente no endereço http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)


