---
name: taxone-doc-gen
description: Geracao de documentacao tecnica/funcional para features e modulos do TAX ONE
argument-hint: Nome da feature ou modulo a documentar
allowed-tools: ["Read", "Grep", "Glob", "Write"]
---

Gerar documentacao tecnica/funcional para a feature do TAX ONE especificada pelo usuario.

## Instrucoes

1. Ativar a skill `taxone-doc-gen` que contem o processo completo de geracao
2. O projeto TAX ONE e o diretorio de trabalho atual (current working directory)
3. Documentacoes existentes para referencia em `${CLAUDE_PLUGIN_ROOT}/knowledge/features/`

## Comportamento

- Priorizar analise do codigo-fonte PL/SQL como fonte primaria
- Incluir packages, procedures, functions, views, triggers e tabelas
- Incluir telas PowerBuilder e classes Java quando aplicavel
- Gerar documento com secoes padronizadas (arquitetura, modelo de dados, fluxos, regras de negocio)
- Salvar em `${CLAUDE_PLUGIN_ROOT}/knowledge/features/[feature].md`
- Apresentar resumo do que foi documentado

## Argumento

O argumento contem o nome da feature ou modulo. Exemplos:
- `Apuracao ICMS`
- `Obrigacoes Acessorias`
- `Calculo de Impostos`
- `Cadastro de Notas Fiscais`
- `Importacao de XMLs`

Se nenhum argumento for fornecido, listar as features disponiveis e perguntar qual documentar.
