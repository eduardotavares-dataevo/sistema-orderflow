# Tela 03: Portal de Vendas (Seleção e Filtros de Pedidos)

- **Arquivo de Template**: `templates/portal_vendas.html`
- **Rotas Flask**: `/portal-vendas`, `/pedidos` (GET)
- **Função em app.py**: `portal_vendas()`
- **Permissão**: Requer autenticação

---

## 🎯 Objetivo
Interface principal de gerenciamento, consulta, impressão e acompanhamento de pedidos de venda emitidos no ERP Sankhya OM (Tabelas `TGFCAB` e `TGFITE`).

---

## ⚙️ Regras de Negócio e Comportamento

1. **Filtros de Busca**:
   - Período de negociação (`DTNEG`: data inicial e final).
   - Número Único do Pedido (`NUNOTA`).
   - Empresa (`CODEMP`).
   - Parceiro / Cliente (`CODPARC`).
   - Vendedor (`CODVEND`): Se o usuário logado não for administrador e possuir vendedor vinculado (`CODVEND > 0`), o filtro é restrito ao seu código. Administradores podem consultar qualquer vendedor.

2. **Grade Superior (Cabeçalhos de Pedidos - TGFCAB)**:
   - Paginação controlada de 15 pedidos por página.
   - Status da nota: `Liberado` (L), `Pendente` (P), etc.
   - Link de pagamento PIX e verificação de status.
   - Botão de Ações Rápidas do Sankhya (Menu raio azul) para consultar PIX, recalcular e acionar procedures.
   - Botão de impressão: pedidos pendentes (`STATUSNOTA != 'L'`) exibem aviso e bloqueiam impressão antes da confirmação.

3. **Grade Inferior (Itens do Pedido Selecionado - TGFITE)**:
   - Carrega via AJAX ao clicar em uma linha de pedido da grade superior (`/api/pedidos/<nunota>/itens`).
   - Mostra produtos, quantidades, referências, preços unitários, descontos e tributações.

4. **Responsividade Mobile**:
   - O painel lateral de filtros se transforma em um Drawer flutuante deslizante inferior (Bottom Sheet) no celular.
   - As grades suportam rolagem horizontal touch-friendly.
