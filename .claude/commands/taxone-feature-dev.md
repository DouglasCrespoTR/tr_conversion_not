---
name: taxone-feature-dev
description: Pipeline generico de desenvolvimento no TAX ONE com 11 agentes e 13 fases. Inclui cenarios de teste, 4 camadas de teste (SQL + SuiteAutomation + E2E + Explorer), compilacao DBA, suporte Angular e Compound Engineering. Sem ADO.
argument-hint: Descreva a feature ou bug a ser trabalhado
allowed-tools: ["Read", "Agent", "Bash", "AskUserQuestion"]
---

Iniciar o pipeline de desenvolvimento do TAX ONE para a demanda especificada pelo usuario.

## Instrucoes

1. Ativar a skill `taxone-feature-dev` que contem o pipeline completo (v2)
2. Seguir as 13 fases na ordem definida na skill
3. O projeto TAX ONE e o diretorio de trabalho atual (current working directory)
4. A base de conhecimento esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/`

## Restricoes Criticas

- Este comando TEM ACESSO a Read, Agent, Bash e AskUserQuestion
- Bash e usado APENAS para: git, gh (GitHub CLI), compilacao via sqlplus
- Toda implementacao PL/SQL/PB/Java DEVE ser feita via Agent com subagent_type="taxone-developer"
- Toda implementacao Angular DEVE ser feita via Agent com subagent_type="taxone-angular"
- Todo code review DEVE ser feito via Agent com subagent_type="taxone-reviewer"
- Analise arquitetural DEVE ser feita via Agent com subagent_type="taxone-architect"
- Analise DBA e compilacao DEVE ser feita via Agent com subagent_type="taxone-dba"
- Testes SQL via Agent com subagent_type="taxone-tester"
- Testes E2E via Agent com subagent_type="taxone-e2e-tester"
- Regressao via Agent com subagent_type="taxone-suite-runner"
- Teste visual via Agent com subagent_type="taxone-explorer"
- NUNCA usar agentes externos ao plugin

## Comportamento Obrigatorio

1. **PRIMEIRO:** Carregar knowledge da feature em `${CLAUDE_PLUGIN_ROOT}/knowledge/features/` (se existir)
2. **SEGUNDO:** Alinhar escopo com o usuario e AGUARDAR confirmacao
3. **TERCEIRO:** Gerar cenarios de teste (test_scenarios.json) ANTES da implementacao
4. **QUARTO:** Seguir as fases do pipeline, PARANDO entre cada fase para reportar progresso
5. **QUINTO:** Compilar PL/SQL no banco ANTES de rodar qualquer teste
6. **ULTIMO:** Apresentar resumo final com todas as mudancas, testes e opcao de commit/PR

## Pipeline (13 Fases)

```
Fase 1    Brainstorming e Alinhamento
Fase 1.5  Geracao de Cenarios de Teste
Fase 2    Analise Arquitetural (architect)
Fase 2.5  Analise DBA Oracle (condicional)
Fase 3    Plano de Implementacao
Fase 4    Implementacao (developer / angular)
Fase 4.5  Cobertura de Regras (architect)
Fase 5    Compilacao DBA + Testes SQL
Fase 5.3  SuiteAutomation (condicional)
Fase 5.5  E2E Playwright (condicional)
Fase 5.7  Explorer Visual (condicional)
Fase 6    Code Review
Fase 7    Resumo + Compound Engineering
```

## Argumento

O argumento contem a descricao da feature ou bug. Exemplos:
- `Implementar novo calculo de DIFAL para operacoes interestaduais`
- `Corrigir apuracao de ICMS quando ha devolucao parcial`
- `Adicionar campo de observacao no cadastro de notas fiscais`
- `Otimizar view de consulta de obrigacoes acessorias`

Se nenhum argumento for fornecido, perguntar ao usuario o que deseja fazer.
