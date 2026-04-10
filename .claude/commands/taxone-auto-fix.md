---
name: taxone-auto-fix
description: Pipeline automatizado de busca e correcao de bugs do Azure DevOps. Busca bugs via query, cria branch, corrige usando agentes especializados PL/SQL/PB/Java, faz commit/push, cria PR no GitHub e atualiza o work item no ADO.
argument-hint: ID da query do Azure DevOps (obrigatorio)
allowed-tools: ["Read", "Agent", "Bash", "AskUserQuestion"]
---

Iniciar o pipeline automatizado de busca e correcao de bugs do Azure DevOps.

## Instrucoes

1. Ativar a skill `taxone-auto-fix` que contem o pipeline completo
2. Seguir RIGOROSAMENTE as regras R1-R6 definidas na skill
3. O projeto TAX ONE e o diretorio de trabalho atual (current working directory)
4. A base de conhecimento esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/`
5. O guia da API do ADO esta em `${CLAUDE_PLUGIN_ROOT}/references/ado-api-guide.md`

## Restricoes Criticas

- Este comando TEM ACESSO a Read, Agent, Bash e AskUserQuestion
- Bash e usado APENAS para: curl (ADO API), git, gh (GitHub CLI)
- Toda implementacao de codigo DEVE ser feita via Agent com subagent_type="taxone-developer"
- Todo code review DEVE ser feito via Agent com subagent_type="taxone-reviewer"
- Analise DBA DEVE ser feita via Agent com subagent_type="taxone-dba"
- NUNCA usar agentes externos ao plugin
- Os UNICOS agentes permitidos sao: taxone-pm, taxone-architect, taxone-developer, taxone-angular, taxone-dba, taxone-reviewer, taxone-tester, taxone-sm
- O PAT token para Azure DevOps DEVE estar na variavel de ambiente ADO_PAT

## Comportamento

1. **PRIMEIRO:** Validar que ADO_PAT esta configurado e que o Query ID foi informado
2. **SEGUNDO:** Buscar bugs do ADO via query informada pelo usuario usando curl + ADO REST API
3. **TERCEIRO:** Apresentar lista de bugs e deixar usuario selecionar qual corrigir
4. **QUARTO:** Criar branch, carregar knowledge, lancar agentes para correcao e review
5. **ULTIMO:** Commit, push, criar PR no GitHub, atualizar work item no ADO

## Argumento

O argumento e o ID da query do Azure DevOps (OBRIGATORIO):
- `/taxone-auto-fix 9bb6e572-8580-4eb3-86e6-a56bc5303d69` - Buscar bugs desta query
- `/taxone-auto-fix` - Sem argumento: perguntar o ID da query ao usuario

O ID da query pode ser encontrado na URL do ADO:
`https://dev.azure.com/tr-ggo/Mastersaf%20Fiscal%20Solutions/_queries/query/{QUERY_ID}`
