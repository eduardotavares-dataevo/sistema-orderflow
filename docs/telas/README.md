# Documentação de Telas, Funções e Regras de Negócio — OrderFlow

Este diretório contém a documentação completa de cada tela do sistema **OrderFlow**, mapeando suas rotas, funcionalidades, regras de negócio associadas ao ERP Sankhya OM, permissões de acesso e estrutura de layout (incluindo responsividade Web e Mobile).

---

## 📑 Índice de Telas

1. [01 - Login & Autenticação](01_login.md)
2. [02 - Painel Principal (Início / Dashboard)](02_inicio.md)
3. [03 - Portal de Vendas (Seleção e Filtro de Pedidos)](03_portal_vendas.md)
4. [04 - Central de Pedidos (Edição, Itens e Fechamento)](04_central_pedidos.md)
5. [05 - Consulta de Produtos (Catálogo, Preços e Estoque TGFEST)](05_consulta_produtos.md)
6. [06 - Promoções](06_promocoes.md)
7. [07 - Business Intelligence (BI)](07_bi.md)
8. [08 - Configurações & Aparência](08_configuracoes.md)
9. [09 - Gestão de Acessos & Permissões (ADM)](09_acessos.md)

---

## 🔒 Princípios de Preservação de Regras

- Nenhuma alteração de layout, CSS ou interface pode modificar as queries SQL, regras de permissão (`TSIUSU`, `TSIGRU`), regras de precificação (`TIPTABPRECO`) ou status de notas (`TGFCAB.STATUSNOTA`).
- A sincronização com o Sankhya OM é bidirecional via API REST/JSON e chamadas de procedures nativas.
