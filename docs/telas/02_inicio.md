# Tela 02: Início (Painel Principal)

- **Arquivo de Template**: `templates/index.html`
- **Rota Flask**: `/` (GET)
- **Função em app.py**: `home()`
- **Permissão**: Requer autenticação de sessão

---

## 🎯 Objetivo
Ponto de entrada do usuário autenticado no sistema. Exibe boas-vindas e serve como área para indicadores e atalhos operacionais rápidos da Auto Giro Distribuidora.

---

## ⚙️ Regras de Negócio e Comportamento

1. **Mensagem de Boas-Vindas**:
   - Se passado o parâmetro `login_sucesso=1`, exibe notificação flutuante temporária (toast) com o nome do usuário logado, que desaparece automaticamente após 5 segundos.
2. **Dashboard Canvas**:
   - Espaço reservado para visualização rápida de metas, limites de crédito, faturamento diário e atalhos para a emissão de novos pedidos.
3. **Navegação**:
   - Atalho direto para o Portal de Vendas e Consulta de Produtos.
