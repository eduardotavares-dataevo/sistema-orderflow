# Tela 05: Consulta de Produtos (Catálogo, Preços e Estoque TGFEST)

- **Arquivo de Template**: `templates/consulta_produtos.html`
- **Rota Flask**: `/consulta-produtos` (GET)
- **Função em app.py**: `consulta_produtos()`
- **Permissão**: Requer autenticação

---

## 🎯 Objetivo
Consulta executiva e detalhada do catálogo de mercadorias (`TGFPRO`), aplicação das regras de precificação dinâmica (`TIPTABPRECO` / `TGFTAB`) e detalhamento do saldo em estoque por Empresa e Local (`TGFEST`).

---

## ⚙️ Regras de Negócio e Comportamento

1. **Busca e Paginação**:
   - Só carrega registros se o usuário informar um termo de busca no campo amplo (otimização de tráfego e performance).
   - Busca por Código (`CODPROD`), Descrição (`DESCRPROD`), Referência do Fornecedor (`REFFORN`), Marca ou Grupo.
   - Paginação de 15 itens por página.

2. **Cálculo da Regra de Preço (`TIPTABPRECO`)**:
   - Lê a configuração global do Sankhya para identificar a regra de precificação (Ex: por Perfil, por Região/Parceiro, por Vendedor, por Tipo de Venda, Local ou Empresa).
   - Se aplicável, a barra lateral permite preencher os parâmetros (ex: Parceiro `CODPARC`, Vendedor `CODVEND`) e recalcular o preço líquido do cliente.

3. **Grade de Estoque Detalhado (`TGFEST`)**:
   - Ao selecionar uma linha da tabela de produtos superior, a tabela inferior é populada via AJAX com os dados de cada Empresa (`CODEMP`), Local (`CODLOCAL`), Estoque Físico, Reserva e Estoque Disponível.

4. **Interface e Usabilidade**:
   - Reordenação de colunas via Drag & Drop e redimensionamento salvo no `localStorage`.
   - Seletor de visibilidade de colunas (*Exibir / Ocultar Colunas*).
   - **Otimização solicitada**: Barra lateral de parâmetros recolhida/expansível sob demanda e tabelas adaptadas tanto para Desktop quanto para visualização limpa em Mobile (sem espremer o conteúdo).
