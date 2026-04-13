# Telas Analisadas pelo Rule-Extractor

Registro de telas que ja passaram por extracao de regras de negocio. Serve como:
- **Base de conhecimento** para proximas extracoes (padroes ja identificados)
- **Checklist de revisao** quando o rule-extractor for evoluido (re-rodar para validar melhorias)
- **Inventario de migracao** para o Strangler Fig (quais telas estao prontas para reescrita)

---

## Legenda de Status

| Status | Significado |
|--------|-------------|
| `ANALISADA` | Regras extraidas e documentadas |
| `MIGRADA_WEB` | Tela ja reescrita em tecnologia web e em producao |
| `EM_MIGRACAO` | Tela em processo de reescrita |
| `PENDENTE_REVISAO` | Precisa re-analise apos evolucao do extractor |

---

## Telas Analisadas

### FEDERAL/saffdecf (ECF)

| # | Tela (Window) | Tipo | Complexidade | Regras | Status | Data Analise | Observacoes |
|---|---------------|------|-------------|--------|--------|-------------|-------------|
| 1 | `w_man_cnpj_efd_irpj` | CRUD simples | BAIXA | 7 | ANALISADA | 2026-04-13 | Tela piloto Strangler Fig. Herda de `w_sheet_dw_simples`. Sem gaps criticos para web. |
| 2 | `w_lib_proc_saffdecf` | Processamento | ALTA | 11 | ANALISADA | 2026-04-13 | Multiprocessamento, dynamic forms, OLE/IE. 5 gaps para web identificados. Herda de `w_lib_proc` (GENERICAS). |
| 3 | `w_lib_proc_saffdecf_geracao` | Processamento (geracao) | ALTA | — | ANALISADA | 2026-04-13 | Variante de geracao. Dynamic report tabs, portrait/landscape, line gap filling. |
| 4 | `w_lib_proc_saffdecf_relatorio` | Processamento (relatorio) | MUITO_ALTA | — | ANALISADA | 2026-04-13 | HTML via OLE, save local/server, multi-volume splitting, CSS copy. |

---

## Detalhes por Tela

### 1. w_man_cnpj_efd_irpj

- **Modulo:** FEDERAL/saffdecf/saffdecf1.pbl
- **Titulo:** Cadastro de CNPJ para ECF
- **Menu:** Parametros > Identificacao do CNPJ para Central de Cadastros > Cadastro de CNPJ
- **Heranca:** `w_man_cnpj_efd_irpj` → `w_sheet_dw_simples` → `w_sheet` → `window`
- **DataWindows:** `d_man_cnpj_efd_irpj` (1 DW, tabela `prt_cad_cnpj_irpj`)
- **User Objects:** `u_prt_cad_cnpj_irpj` (validacao + popup)
- **Tabelas:** `prt_cad_cnpj_irpj`, `x2057_cod_scp`, `relac_tab_grupo`
- **PL/SQL consumidor:** `ECF_MM_FPROC` (usa a tabela em JOINs de exportacao)
- **Tela irma:** `w_man_cnpj_efd_irpj_empresa` (mesma tabela + filtro empresa)

**Regras documentadas:**

| ID | Tipo | Descricao Resumida |
|----|------|--------------------|
| RV-001 | Validacao | Exclusao mutua CNPJ vs SCP |
| RV-002 | Validacao | cod_tabela obrigatorio |
| RV-003 | Validacao | grupo obrigatorio |
| RV-004 | Validacao | CNPJ ou SCP obrigatorio (pelo menos um) |
| RF-001 | Filtro | cod_tabela fixo em '2002' ou '2003' |
| RF-002 | Filtro | Filtro por cod_empresa/cod_estab no SCP |
| RF-003 | Filtro | Temporal: MAX(valid_scp) para data mais recente |

**Gaps para reescrita web:** Nenhum critico. DDDW → dropdown com lazy load. Popup dinamico → side panel ou modal.

**Arquivos de referencia:**
- `ws_objects/FEDERAL/saffdecf/saffdecf1.pbl.src/w_man_cnpj_efd_irpj.srw`
- `ws_objects/FEDERAL/saffdecf/saffdecf1.pbl.src/d_man_cnpj_efd_irpj.srd`
- `ws_objects/FEDERAL/saffdecf/saffdecf1.pbl.src/u_prt_cad_cnpj_irpj.sru`
- `ws_objects/GENERICAS/SAFGN/safgnfw1.pbl.src/w_sheet_dw_simples.srw`
- `artifacts/bd/V2R010/DDL_OS4302.SQL`

**Documento de analise:** `C:/Users/6123442/Desktop/analise_correlacao_w_man_cnpj_efd_irpj.md`

---

### 2. w_lib_proc_saffdecf

- **Modulo:** FEDERAL/saffdecf/saffdecf1.pbl
- **Titulo:** Processamento ECF (base)
- **Heranca:** `w_lib_proc_saffdecf` → `w_lib_proc` → `w_sheet` → `window`
- **Funcao principal:** `wf_executa_package()` — montagem dinamica de blocos PL/SQL
- **Variantes:** `w_lib_proc_saffdecf_geracao` (geracao), `w_lib_proc_saffdecf_relatorio` (relatorio)
- **PL/SQL chamados:** `LIB_PROC.job_execute`, `LIB_PARAMETROS.Salvar`, `DBMS_JOB.submit`, `{is_package}.Executar(params)`

**Regras documentadas (resumo):**

| ID | Tipo | Descricao Resumida |
|----|------|--------------------|
| RW-001 | Workflow | Multiprocessamento — loop N registros marcados |
| RW-002 | Workflow | Montagem dinamica de bloco PL/SQL para execucao |
| RW-003 | Workflow | Agendamento via DBMS_JOB.submit |
| RW-004 | Workflow | Criacao dinamica de tabs de parametros |
| RV-005 | Validacao | Validacao de parametros obrigatorios |
| RV-006 | Validacao | Verificacao de processo ja em execucao |
| RT-007 | Transformacao | Montagem de string de parametros para PL/SQL |
| RW-008 | Workflow | Tratamento de retorno (status, erros) |
| RW-009 | Workflow | Geracao de relatorio pos-processo |
| RW-010 | Workflow | Multi-volume splitting (relatorio) |
| RW-011 | Workflow | Save local vs server (relatorio) |

**5 Gaps para reescrita web:**

| # | Gap | Solucao Sugerida |
|---|-----|-----------------|
| 1 | Logica em parent (`w_lib_proc`) | Inheritance crawling — documentado |
| 2 | Dynamic form builder (`wf_cria_parametros`) | JSON Schema → dynamic form renderer |
| 3 | OLE/ActiveX (`ole_ie`) | iframe sandbox ou PDF renderer |
| 4 | DBMS_JOB (deprecated) | DBMS_SCHEDULER ou Spring Batch |
| 5 | Filesystem desktop (`FileOpen/Write/Close`) | Download API + server-side storage |

**Documento de analise:** `C:/Users/6123442/Desktop/analise_correlacao_w_lib_proc_saffdecf.md`

---

## Proximas Telas Candidatas

Priorizacao sugerida para analise pelo rule-extractor:

| Prioridade | Tela | Modulo | Tipo | Motivo |
|-----------|------|--------|------|--------|
| 1 | `w_man_cnpj_efd_irpj_empresa` | FEDERAL/saffdecf | CRUD simples | Tela irma da piloto, mesma tabela |
| 2 | Outros CRUDs de `saffdecf1.pbl` | FEDERAL/saffdecf | CRUD | Mesmo modulo, padroes similares |
| 3 | CRUDs de parametros (`safpr`) | Parametros | CRUD | Muitos cadastros simples, bom para escalar |
| 4 | `w_lib_proc` (GENERICAS) | GENERICAS | Framework | Documentar parent completo para reusar em todas as telas de processamento |
| 5 | Telas de processamento de outros modulos | Varios | Processamento | Apos documentar `w_lib_proc`, mais rapido |

---

## Historico de Evolucoes do Extractor

| Data | Versao | Mudanca | Impacto nas Telas Existentes |
|------|--------|---------|------------------------------|
| 2026-04-13 | 1.0 | Versao inicial com knowledge de repos e padroes PB/PL/SQL | N/A |
| 2026-04-13 | 2.0 | Adicionado inheritance crawling, gap analysis para web, padroes Mobilize, fase 4.5, secoes 6-7 no output | Telas 1-4 analisadas antes desta versao — considerar re-analise para validar novas secoes |
