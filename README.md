# tr_conversion_not

Agentes Claude para extração de regras de negócio do TAX ONE (Mastersaf DW) legado, viabilizando a reescrita em Java.

## Objetivo

O TAX ONE é um sistema de compliance fiscal brasileiro construído em **PowerBuilder + PL/SQL + Oracle**. Este repositório contém agentes Claude Code que automatizam a análise e extração de conhecimento do código legado para guiar a migração para **Java 21 / Spring Boot**.

## Estrutura

```
.claude/
├── agents/           # Agentes especializados
│   ├── taxone-rule-extractor.md   # Extrator de regras de negócio (principal)
│   ├── taxone-architect.md        # Análise arquitetural
│   ├── taxone-plsql.md            # Implementação PL/SQL
│   ├── taxone-pb.md               # Implementação PowerBuilder
│   ├── taxone-java.md             # Implementação Java
│   └── ...                        # Demais agentes (DBA, reviewers, testers, etc.)
├── commands/         # Slash commands reutilizáveis
└── settings.local.json  # Configurações locais (não commitado)
```

## Repositórios Relacionados

| Repositório | Conteúdo |
|-------------|----------|
| `tr/taxone_dw` | Código fonte legado (PB + PL/SQL) |
| `tr/taxsami_convertedFonts` | Fontes PB convertidas para Java (Mobilize jWebMAP) |
| `tr/taxone_modules_t1dw` | Nova aplicação Java nativa (Spring Boot 3.5) |
| `tr/taxsami_conversiontool` | Ferramenta de conversão PB→Java (Mobilize/Artinsoft) |
| `tr/taxsami_artifacts` | Runtime Java auxiliar (PB2JavaHelpers) |
| `tr/taxone_dw_conteudo` | Documentação, help online e layouts SAFX |
| `tr/taxsami_taxone_frontend` | Frontend Angular 11 (monorepo) |

## Agente Principal: taxone-rule-extractor

Analisa módulos do TAX ONE e produz documentos estruturados com:

- **Regras de Validação (RV)** — condições obrigatórias
- **Regras de Cálculo (RC)** — fórmulas fiscais
- **Regras de Filtro (RF)** — critérios de seleção
- **Regras de Transformação (RT)** — mapeamentos de dados
- **Regras de Workflow (RW)** — orquestração de processos

Cada regra é classificada com status de migração: `[NEW]`, `[CONVERTED]`, `[NATIVE]`, `[JAVA_GAP]`.

## Uso

```bash
# Executar o extrator de regras para um módulo
claude --agent taxone-rule-extractor "Extrair regras do módulo ESTADUAL/Safousp"
```
