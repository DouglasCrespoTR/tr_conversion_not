---
name: query-executor-prod-uat
description: Executar queries SQL em ambientes Production ou UAT de clientes. Dispara workflow de execucao remota para uma empresa especifica.
argument-hint: "empresa query_ou_arquivo [ambiente]" (ex: ACHE "SELECT * FROM DUAL" uat)
allowed-tools: ["Read", "Bash", "AskUserQuestion"]
---

Executar queries SQL em ambientes Production ou UAT de clientes.

## Instrucoes

1. Ativar a skill `query-executor-prod-uat` que contem o pipeline de execucao
2. Seguir as regras de seguranca definidas na skill
3. A base de conhecimento esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/`

## Restricoes Criticas

- Este comando TEM ACESSO a Read, Bash e AskUserQuestion
- NUNCA executar queries destrutivas (DELETE, DROP, TRUNCATE, UPDATE) sem confirmacao explicita do usuario
- Sempre confirmar empresa, ambiente (prod/uat) e query antes de disparar
- O PAT token para Azure DevOps DEVE estar na variavel de ambiente ADO_PAT

## Comportamento

1. **PRIMEIRO:** Identificar empresa, ambiente (prod ou uat) e query/arquivo SQL
2. **SEGUNDO:** Confirmar com usuario antes de disparar a execucao
3. **TERCEIRO:** Enviar a query para execucao via workflow remoto
4. **QUARTO:** Aguardar resultado e apresentar ao usuario

## Argumento

O argumento pode conter:
- `/query-executor-prod-uat ACHE "SELECT * FROM DUAL" uat` - Empresa + query inline + ambiente
- `/query-executor-prod-uat` - Sem argumento: perguntar empresa, query e ambiente ao usuario

## Exemplos

- `Executa essa query em UAT para a ACHE: SELECT * FROM DUAL`
- `Roda esse SQL em prod para a FEMSA`
- `Envia essa query para producao da BMW`
- `Manda esse arquivo .sql para o ambiente prod da MRV`
