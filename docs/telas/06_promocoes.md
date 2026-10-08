# Tela 06: Promoções

- **Arquivo de Template**: `templates/promocoes.html`
- **Rota Flask**: `/promocoes` (GET)
- **Função em app.py**: `promocoes()`
- **Permissão**: Requer autenticação

---

## 🎯 Objetivo
Listagem rápida de campanhas promocionais ativas para os vendedores e clientes da distribuidora.

---

## ⚙️ Regras de Negócio e Comportamento

1. **Cálculo de Desconto**:
   - Exibe o valor de tabela original (`de_valor`), percentual de desconto concedido na campanha (`percdesc`) e o valor final calculado com o abatimento promocional.
