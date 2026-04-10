---
name: taxone-us-auto-implement
description: Pipeline automatizado de implementacao de User Stories/Features do Azure DevOps. Busca US do ADO, faz analise arquitetural, implementa em PL/SQL/PB/Java, review, cria PR no GitHub e atualiza o work item.
argument-hint: ID da user story/feature do Azure DevOps ou ID da query (opcional)
allowed-tools: ["Read", "Agent", "Bash", "AskUserQuestion"]
---

Iniciar o pipeline automatizado de implementacao de User Stories/Features do Azure DevOps.

## Instrucoes

1. Ativar a skill `taxone-us-auto-implement` que contem o pipeline completo
2. Seguir RIGOROSAMENTE as regras R1-R6 definidas na skill
3. O projeto TAX ONE e o diretorio de trabalho atual (current working directory)
4. A base de conhecimento esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/`
5. O guia da API do ADO esta em `${CLAUDE_PLUGIN_ROOT}/references/ado-api-guide.md`

## Restricoes Criticas

- Este comando TEM ACESSO a Read, Agent, Bash e AskUserQuestion
- Bash e usado APENAS para: curl (ADO API, Zendesk API), git, gh (GitHub CLI)
- Toda implementacao de codigo DEVE ser feita via Agent com subagent_type="taxone-developer"
- Todo code review DEVE ser feito via Agent com subagent_type="taxone-reviewer"
- Analise arquitetural DEVE ser feita via Agent com subagent_type="taxone-architect"
- Analise DBA DEVE ser feita via Agent com subagent_type="taxone-dba"
- NUNCA usar agentes externos ao plugin
- Os UNICOS agentes permitidos sao: taxone-pm, taxone-architect, taxone-developer, taxone-angular, taxone-dba, taxone-reviewer, taxone-tester, taxone-sm
- O PAT token para Azure DevOps DEVE estar na variavel de ambiente ADO_PAT

## Comportamento

1. **PRIMEIRO:** Validar que ADO_PAT esta configurado
2. **SEGUNDO:** Buscar US/Feature do ADO (via work item ID, query ID ou URL)
3. **TERCEIRO:** Apresentar US e deixar usuario selecionar e confirmar opcoes
4. **QUARTO:** Analise arquitetural + DBA, criar branch, implementar, review
5. **ULTIMO:** Commit, push, criar PR no GitHub, atualizar work item no ADO

## Argumento

O argumento pode ser (todos sao OPCIONAIS - se nao fornecido, perguntar ao usuario):
- `/taxone-us-auto-implement 12345` - Work Item ID direto
- `/taxone-us-auto-implement query:9bb6e572-8580-4eb3-86e6-a56bc5303d69` - Query ID
- `/taxone-us-auto-implement https://dev.azure.com/tr-ggo/Mastersaf%20Fiscal%20Solutions/_workitems/edit/12345` - URL
- `/taxone-us-auto-implement` - Sem argumento: perguntar ao usuario
