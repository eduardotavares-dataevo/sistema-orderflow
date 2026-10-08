# Regra de Auto Accept / Modo Totalmente Autônomo

Esta regra define o comportamento para o usuário e o workspace:

1. **Aprovação e Execução Direta (Auto-Accept)**:
   - Todas as modificações de arquivos, criações e exclusões devem ser executadas e aplicadas imediatamente sem solicitar confirmação manual prévia ao usuário.
   - Todos os comandos de terminal necessários (testes, curl, python, verificações, restart de serviços) devem ser executados proativamente sem pedir permissão.

2. **Resolução Autônoma de Ponta a Ponta**:
   - Diagnosticar, implementar, verificar e entregar as alterações solicitadas sem interrupções intermediárias desnecessárias.
   - Não bloquear a execução para perguntas triviais de "posso prosseguir?". Assumir a melhor decisão técnica e aplicar.

3. **Validação Automática**:
   - Sempre validar as alterações diretamente no ambiente após aplicar as edições.
