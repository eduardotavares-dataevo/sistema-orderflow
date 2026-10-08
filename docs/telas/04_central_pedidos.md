# Tela 04: Central de Pedidos (Formulário, Itens & Fechamento)

- **Arquivo de Template**: `templates/central_pedidos.html`
- **Rotas Flask**: `/central-pedidos`, `/central-pedidos/novo`, `/central-pedidos/<nunota>` (GET / POST)
- **Função em app.py**: `central_pedidos()`
- **Permissão**: Requer autenticação

---

## 🎯 Objetivo
Ambiente de digitação e manutenção completa do pedido de venda (inserção, alteração de cabeçalho, inclusão/remoção de itens, cálculo de impostos, conferência de estoque, geração de link PIX e confirmação de nota).

---

## ⚙️ Regras de Negócio e Comportamento

1. **Cabeçalho (TGFCAB)**:
   - Campos: Empresa (`CODEMP`), Parceiro (`CODPARC`), Tipo de Operação (`CODTIPOPER`), Tipo de Negociação (`CODTIPVENDA`), Vendedor (`CODVEND`), Data de Negociação (`DTNEG`), Frete (`CIF_FOB`, `VLRFRETE`), Observações.
   - Atualiza carimbo de alteração com usuário logado (`DHALTER`).
   - Bloqueio de edição para pedidos já confirmados/faturados.

2. **Inclusão e Consulta de Produtos no Pedido (TGFITE & TGFPRO)**:
   - Busca de produtos com cálculo dinâmico de tabela de preço por perfil/região/parceiro (`TIPTABPRECO`).
   - Grade de itens permite ajuste de quantidade, desconto percentual/valor e controle de casas decimais.
   - Verificação em tempo real do saldo em estoque (`TGFEST`) por empresa e local.

3. **Validação de Limites & Liberações**:
   - Em caso de bloqueio comercial ou financeiro, permite solicitar liberação informando motivo para os liberadores cadastrados no Sankhya.

4. **Pagamento PIX**:
   - Integração com o gateway para geração de QR Code e chave Copia-e-Cola diretamente na tela do pedido.
