# Tela 08: Configurações & Aparência do Sistema

- **Arquivo de Template**: `templates/configuracoes.html`
- **Rotas Flask**: `/configuracoes`, `/contato` (GET)
- **Função em app.py**: `configuracoes()`
- **Permissão**: Requer autenticação (Bloco de parâmetros requer perfil ADM)

---

## 🎯 Objetivo
Configuração de preferências visuais individuais do usuário (Tema Claro / Escuro) e visualização de parâmetros técnicos da integração com o Sankhya OM e Gateway de Pagamentos PIX.

---

## ⚙️ Regras de Negócio e Comportamento

1. **Gerenciamento de Tema (Light / Dark)**:
   - Permite alternância rápida entre os temas visualizados.
   - Persiste a escolha no `localStorage` sob a chave `app_theme` e sincroniza o atributo `data-theme` na tag raiz `<html>`.
   - Atualiza todos os componentes do sistema via evento global `temaAlterado`.

2. **Parâmetros de Integração (Exclusivo Administradores)**:
   - Exibe a URL base do servidor Sankhya, usuário de integração e status de conexão da API.
   - Exibe os parâmetros do gateway de pagamentos PIX (Ambiente, TOP Padrão de Pedido e Versão).
