---
name: taxone-sprint-plan
description: Pipeline automatizado de Sprint Planning para o TAX ONE. Busca WIs do ADO (via query ou IDs), estima story points com base em historico, aloca devs/testers por afinidade de modulo e apresenta sprint board com balanceamento de carga.
argument-hint: ID da query do ADO ou lista de WI IDs (opcional)
allowed-tools: ["Read", "Agent", "Bash", "AskUserQuestion"]
---

Iniciar o pipeline automatizado de Sprint Planning do TAX ONE.

## Instrucoes

1. Ativar a skill `taxone-sprint-plan` que contem o pipeline completo
2. Seguir RIGOROSAMENTE as regras R1-R6 definidas na skill
3. O projeto TAX ONE e o diretorio de trabalho atual (current working directory)
4. A base de conhecimento esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/`
5. O guia da API do ADO esta em `${CLAUDE_PLUGIN_ROOT}/references/ado-api-guide.md`
6. O roster do time esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/team/TEAM_ROSTER.json`

## Restricoes Criticas

- Este comando TEM ACESSO a Read, Agent, Bash e AskUserQuestion
- Bash e usado APENAS para: curl (ADO API), echo/printenv
- O agente SM DEVE ser invocado via Agent com subagent_type="taxone-sm"
- O PAT token para Azure DevOps DEVE estar na variavel de ambiente ADO_PAT
- Os UNICOS agentes permitidos sao: taxone-sm
- **NUNCA** modificar codigo - este pipeline e somente leitura + atualizacao ADO

## Comportamento

1. **PRIMEIRO:** Validar que ADO_PAT esta configurado e parsear argumento
2. **SEGUNDO:** Buscar WIs do ADO (via query ID, lista de WI IDs ou perguntar ao usuario)
3. **TERCEIRO:** Lancar taxone-sm para descoberta de especializacoes (se cache ausente/desatualizado)
4. **QUARTO:** Lancar taxone-sm para estimativa de story points e alocacao
5. **ULTIMO:** Apresentar sprint board e (opcionalmente) aplicar no ADO com confirmacao

## Argumento

O argumento pode ser (todos sao OPCIONAIS - se nao fornecido, perguntar ao usuario):
- `/taxone-sprint-plan 9bb6e572-8580-4eb3-86e6-a56bc5303d69` - Query ID do ADO
- `/taxone-sprint-plan 123456,123457,123458` - Lista de WI IDs separados por virgula
- `/taxone-sprint-plan 123456` - WI ID unico (modo alocacao avulsa)
- `/taxone-sprint-plan` - Sem argumento: perguntar ao usuario

O ID da query pode ser encontrado na URL do ADO:
`https://dev.azure.com/tr-ggo/Mastersaf%20Fiscal%20Solutions/_queries/query/{QUERY_ID}`
