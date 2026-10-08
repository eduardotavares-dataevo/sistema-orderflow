# Tela 09: Gestão de Acessos & Permissões (ADM)

- **Arquivo de Template**: `templates/acessos.html`
- **Rotas Flask**: `/acessos`, `/sobre` (GET)
- **Função em app.py**: `acessos()`
- **Permissão**: Exclusivo para usuários com `TSIUSU.AD_ADMCENTRALPED = 'S'`

---

## 🎯 Objetivo
Controle de auditoria e listagem de usuários e permissões de acesso ao sistema OrderFlow, diretamente sincronizado com a base de dados do ERP Sankhya OM.

---

## ⚙️ Regras de Negócio e Comportamento

1. **Validação de Permissão**:
   - Apenas usuários com `is_adm == True` podem acessar a rota. Se outro usuário tentar acessar, é redirecionado para a home.
2. **Matriz de Perfis Sankhya**:
   - `ADMIN`: Possui `TSIUSU.AD_ADMCENTRALPED = 'S'`.
   - `Grupo`: Vinculado a grupo com `TSIGRU.AD_ACESSACENTRALPED = 'S'`.
   - `ADMIN/GRUPO`: Possui ambas as autorizações ativas simultaneamente.
3. **Listagem de Usuários**:
   - Tabela detalha Código (`CODUSU`), Nome de Login, Nome do Vendedor associado, Status ADM, Código do Grupo e Perfil resultante.
