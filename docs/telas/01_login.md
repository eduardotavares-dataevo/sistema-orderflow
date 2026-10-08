# Tela 01: Login & Autenticação

- **Arquivo de Template**: `templates/login.html`
- **Rota Flask**: `/login` (GET / POST)
- **Função em app.py**: `login()`
- **Permissão**: Pública (não requer sessão prévia)

---

## 🎯 Objetivo
Permitir a autenticação de usuários no sistema através da validação direta de credenciais no banco de dados e na API do ERP Sankhya OM (`TSIUSU`).

---

## ⚙️ Regras de Negócio e Comportamento

1. **Autenticação Sankhya**:
   - As credenciais informadas (usuário e senha) são enviadas via `sankhya.autenticar_usuario_sankhya()`.
   - Verifica se o usuário existe, está ativo e obtém `CODUSU`, `NOME`, `CODEMP`, `CODVEND`, `EMAIL`.
   
2. **Identificação de Perfil ADM e Grupo**:
   - Avalia o campo `TSIUSU.AD_ADMCENTRALPED`: se for `'S'`, o usuário recebe flag `is_adm = True`.
   - Avalia o grupo `TSIGRU.AD_ACESSACENTRALPED`: se for `'S'`, o usuário recebe `has_grupo = True`.
   
3. **Validação de Licenças Multi-Tenant**:
   - Conecta ao serviço de licenças (`ADMIN_PANEL_URL/api/v1/validate-license`) informando o token de sessão, tenant e código do usuário.
   
4. **Sessão do Flask**:
   - Armazena os dados do usuário em `session["usuario"]`.
   - Redireciona para o Portal de Vendas / Início (`/portal-vendas` ou `/`).
