---
name: taxone-bug-fix
description: Pipeline autonomo e leve para diagnostico e correcao de bugs no TAX ONE. Carrega knowledge, explora o codigo PL/SQL/PB/Java, implementa o fix, valida com review e reporta o resultado final.
argument-hint: Descreva o bug a ser corrigido
allowed-tools: ["Read", "Agent", "AskUserQuestion"]
---

Iniciar o pipeline autonomo de correcao de bug no TAX ONE.

## Instrucoes

1. Ativar a skill `taxone-bug-fix` que contem o pipeline autonomo de correcao
2. Seguir RIGOROSAMENTE as regras R1-R4 definidas na skill
3. O projeto TAX ONE e o diretorio de trabalho atual (current working directory)
4. A base de conhecimento esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/`

## Restricoes Criticas

- Este comando TEM ACESSO APENAS a Read, Agent e AskUserQuestion. NAO possui Write, Edit, Bash, Grep ou Glob.
- Toda implementacao de codigo DEVE ser feita via Agent com subagent_type="taxone-developer"
- Todo code review DEVE ser feito via Agent com subagent_type="taxone-reviewer"
- NUNCA usar agentes externos ao plugin
- Os UNICOS agentes permitidos sao: taxone-developer, taxone-reviewer, taxone-dba

## Comportamento

1. **PRIMEIRO:** Carregar knowledge da feature em `${CLAUDE_PLUGIN_ROOT}/knowledge/features/[feature].md` (se existir)
2. **SEGUNDO:** Apresentar entendimento do bug + perguntar se precisa carregar mais knowledge. Este e o UNICO ponto de interacao com o desenvolvedor.
3. **TERCEIRO:** Apos confirmacao, trabalhar de forma AUTONOMA: explorar -> diagnosticar -> corrigir -> revisar
4. **ULTIMO:** Apresentar relatorio final com root cause, mudancas, justificativas e resultado do review

## Argumento

O argumento contem a descricao do bug. Exemplos:
- `Calculo de ICMS-ST gerando valor incorreto quando aliquota interestadual e zero`
- `Erro na apuracao de PIS/COFINS para empresas do Simples Nacional`
- `View de consulta de notas fiscais nao retornando registros cancelados`
- `Procedure de importacao travando com timeout em arquivos grandes`

Se nenhum argumento for fornecido, perguntar ao desenvolvedor qual bug deseja corrigir.
