#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ADO Discussion Templates - Templates padronizados para Discussion comments no ADO.

Tres templates:
  1. build_analysis_comment()  - Analise & Implementacao
  2. build_test_results_comment() - Resultados de Testes
  3. build_n3_brief_comment()  - N3 Brief (Enrichment Report)

Uso direto (gera HTML de exemplo):
  python scripts/ado_discussion_templates.py --demo
  python scripts/ado_discussion_templates.py --demo --output /tmp/demo.html

Uso programatico:
  from scripts.ado_discussion_templates import build_analysis_comment, post_discussion_comment
  html = build_analysis_comment(wi_id=1068362, wi_title="...", ...)
  post_discussion_comment(1068362, html)
"""

import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ADO_ORG = "https://dev.azure.com/tr-ggo"
ADO_PROJECT_ENC = "Mastersaf%20Fiscal%20Solutions"
ADO_API = f"{ADO_ORG}/{ADO_PROJECT_ENC}/_apis"

COLORS = {
    "header_bg": "#004578",
    "header_fg": "#fff",
    "pass_bg": "#dff6dd",
    "pass_fg": "#107c10",
    "fail_bg": "#fde7e9",
    "fail_fg": "#d13438",
    "skip_bg": "#fff4ce",
    "skip_fg": "#7a6400",
    "meta_bg": "#f0f0f0",
    "border": "#ccc",
    "section_fg": "#004578",
}

# ── HTML Helpers ──────────────────────────────────────────────────────

def html_escape(text):
    """Escapa caracteres especiais para HTML."""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _cell_style(extra=""):
    return f'style="padding:6px 10px;border:1px solid {COLORS["border"]};{extra}"'


def _table_open():
    return f'<table style="border-collapse:collapse;width:100%;font-size:0.9em;margin:6px 0 12px 0">'


def _table_header(*cols, widths=None):
    """Gera <tr> de cabecalho com fundo azul."""
    cells = ""
    for i, col in enumerate(cols):
        w = f"width:{widths[i]};" if widths and i < len(widths) else ""
        cells += (f'<th style="padding:6px 10px;border:1px solid {COLORS["border"]};'
                  f'background:{COLORS["header_bg"]};color:{COLORS["header_fg"]};'
                  f'text-align:left;{w}">{col}</th>')
    return f"<tr>{cells}</tr>"


def _table_row(*cells, bg=None):
    """Gera <tr> com celulas, opcionalmente com cor de fundo."""
    bg_style = f"background:{bg};" if bg else ""
    tds = ""
    for cell in cells:
        tds += f'<td {_cell_style(bg_style)}>{cell}</td>'
    return f"<tr>{tds}</tr>"


def _status_color(status):
    """Retorna (bg, fg) para um status."""
    s = str(status).upper().strip()
    if s in ("PASS", "OK", "SUCCESS", "VALID", "CONCLUIDA", "COMPILADO"):
        return COLORS["pass_bg"], COLORS["pass_fg"]
    elif s in ("FAIL", "ERRO", "ERROR", "INVALID", "FALHA"):
        return COLORS["fail_bg"], COLORS["fail_fg"]
    elif s in ("SKIP", "N/A", "WARNING", "PENDENTE", "NAO_EXECUTADO"):
        return COLORS["skip_bg"], COLORS["skip_fg"]
    return None, None


def _status_badge(status):
    """Retorna <span> colorido para status."""
    bg, fg = _status_color(status)
    s = html_escape(str(status))
    if bg:
        return (f'<span style="background:{bg};color:{fg};padding:2px 8px;'
                f'border-radius:3px;font-weight:bold;font-size:0.9em">{s}</span>')
    return f"<b>{s}</b>"


def _section_header(title):
    """Gera header de secao padrao."""
    return (f'<h4 style="margin:16px 0 6px 0;color:{COLORS["section_fg"]};'
            f'border-bottom:2px solid {COLORS["header_bg"]};padding-bottom:4px">'
            f'{html_escape(title)}</h4>')


def _meta_row(label, value):
    """Gera linha de metadado (label: value)."""
    return f'<b>{html_escape(label)}:</b> {html_escape(str(value))}'


def _banner(title):
    """Gera banner de titulo principal."""
    return (f'<div style="background:{COLORS["header_bg"]};color:{COLORS["header_fg"]};'
            f'padding:10px 16px;font-size:1.1em;font-weight:bold;'
            f'border-radius:4px 4px 0 0;margin-bottom:0">'
            f'{html_escape(title)}</div>')


def _meta_bar(*items):
    """Gera barra de metadados abaixo do banner."""
    parts = " &nbsp;|&nbsp; ".join(items)
    return (f'<div style="background:{COLORS["meta_bg"]};padding:8px 16px;'
            f'border:1px solid {COLORS["border"]};border-top:none;'
            f'font-size:0.9em;margin-bottom:12px">{parts}</div>')


def _footer(generator):
    """Gera rodape padrao."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (f'<hr style="margin:16px 0 8px 0;border:none;border-top:1px solid {COLORS["border"]}">'
            f'<p style="font-size:0.8em;color:#888">'
            f'Gerado automaticamente por <b>{html_escape(generator)}</b> em {ts}</p>')


# ── Template 1: Analise & Implementacao ──────────────────────────────

def build_analysis_comment(
    wi_id,
    wi_title,
    pipeline="pipeline-ai",
    analysis=None,
    implementation=None,
    phases=None,
    pendencias=None,
    zendesk=None,
):
    """
    Gera HTML padronizado para comment de Analise/Implementacao.

    Args:
        wi_id: ID da Work Item
        wi_title: Titulo da WI
        pipeline: Nome do pipeline/skill (ex: "taxone-auto-fix")
        analysis: dict com {causa_raiz, veredicto, impacto, hipoteses}
        implementation: dict com {branch, pr_url, pr_number, files, ddls,
                                  code_review, compilacao}
        phases: list de dicts [{name, status, duration}]
        pendencias: list de strings (o que NAO foi feito)
        zendesk: dict com {ticket_id, status, resumo} ou None
    """
    analysis = analysis or {}
    implementation = implementation or {}
    phases = phases or []
    pendencias = pendencias or []

    html = ['<div style="box-sizing:border-box;font-family:Segoe UI,sans-serif">']

    # ── Banner ──
    html.append(_banner("Pipeline AI - Analise & Implementacao"))

    # ── Meta bar ──
    meta_items = [
        _meta_row("WI", f"#{wi_id} - {wi_title}"),
    ]
    meta_items.append(_meta_row("Data", datetime.now().strftime("%Y-%m-%d %H:%M")))
    meta_items.append(_meta_row("Pipeline", pipeline))

    branch = implementation.get("branch", "")
    pr_url = implementation.get("pr_url", "")
    pr_number = implementation.get("pr_number", "")
    if branch:
        meta_items.append(_meta_row("Branch", branch))
    if pr_url and pr_number:
        meta_items.append(f'<b>PR:</b> <a href="{html_escape(pr_url)}">#{pr_number}</a>')

    html.append(_meta_bar(*meta_items))

    # ── Secao: Analise ──
    if analysis:
        html.append(_section_header("Analise"))
        html.append('<ul style="margin:4px 0">')
        if analysis.get("causa_raiz"):
            html.append(f'<li><b>Causa raiz:</b> {html_escape(analysis["causa_raiz"])}</li>')
        if analysis.get("veredicto"):
            html.append(f'<li><b>Veredicto:</b> {_status_badge(analysis["veredicto"])}</li>')
        if analysis.get("impacto"):
            html.append(f'<li><b>Impacto:</b> {html_escape(analysis["impacto"])}</li>')
        if analysis.get("hipoteses"):
            html.append(f'<li><b>Hipoteses investigadas:</b></li>')
            html.append('<ul>')
            for h in analysis["hipoteses"]:
                html.append(f'<li>{html_escape(h)}</li>')
            html.append('</ul>')
        html.append('</ul>')

    # ── Secao: Implementacao ──
    if implementation:
        html.append(_section_header("Implementacao"))

        # Arquivos modificados
        files = implementation.get("files", [])
        if files:
            html.append(f'<p><b>Arquivos modificados ({len(files)}):</b></p>')
            html.append(_table_open())
            html.append(_table_header("Arquivo", "Acao"))
            for f in files:
                if isinstance(f, dict):
                    html.append(_table_row(
                        f'<code>{html_escape(f.get("path", ""))}</code>',
                        html_escape(f.get("action", "modificado"))
                    ))
                else:
                    html.append(_table_row(f'<code>{html_escape(f)}</code>', "modificado"))
            html.append('</table>')
        else:
            html.append('<p><i>Nenhum arquivo modificado (somente analise)</i></p>')

        # DDLs
        ddls = implementation.get("ddls", [])
        if ddls:
            html.append(f'<p><b>Scripts DDL:</b></p><ul>')
            for d in ddls:
                html.append(f'<li><code>{html_escape(d)}</code></li>')
            html.append('</ul>')

        # Code Review + Compilacao
        extras = []
        if implementation.get("code_review"):
            extras.append(f'<b>Code Review:</b> {_status_badge(implementation["code_review"])}')
        if implementation.get("compilacao"):
            extras.append(f'<b>Compilacao:</b> {_status_badge(implementation["compilacao"])}')
        if extras:
            html.append(f'<p>{" &nbsp;|&nbsp; ".join(extras)}</p>')

    # ── Secao: Fases Executadas ──
    if phases:
        html.append(_section_header("Fases Executadas"))
        html.append(_table_open())
        html.append(_table_header("Fase", "Status", "Duracao", widths=["50%", "25%", "25%"]))
        for p in phases:
            bg, _ = _status_color(p.get("status", ""))
            html.append(_table_row(
                html_escape(p.get("name", "")),
                _status_badge(p.get("status", "")),
                html_escape(p.get("duration", "")),
                bg=bg
            ))
        html.append('</table>')

    # ── Secao: Pendencias ──
    if pendencias:
        html.append(_section_header("Pendencias / Nao Realizado"))
        html.append('<ul style="margin:4px 0">')
        for pend in pendencias:
            html.append(f'<li>{html_escape(pend)}</li>')
        html.append('</ul>')

    # ── Secao: Zendesk ──
    if zendesk:
        html.append(_section_header("Contexto Zendesk"))
        html.append('<ul style="margin:4px 0">')
        if zendesk.get("ticket_id"):
            html.append(f'<li><b>Ticket:</b> TKT{zendesk["ticket_id"]} | '
                        f'<b>Status:</b> {html_escape(zendesk.get("status", "N/A"))}</li>')
        if zendesk.get("resumo"):
            html.append(f'<li><b>Resumo:</b> {html_escape(zendesk["resumo"])}</li>')
        html.append('</ul>')

    # ── Footer ──
    html.append(_footer(pipeline))
    html.append('</div>')

    return "\n".join(html)


# ── Template 2: Resultados de Testes ─────────────────────────────────

def build_test_results_comment(
    wi_id,
    wi_title,
    agent="pipeline-ai",
    db_connection="",
    manual_tests=None,
    suite_results=None,
    evidence=None,
    nao_testado=None,
    attachments=None,
):
    """
    Gera HTML padronizado para comment de Resultados de Testes.

    Args:
        wi_id: ID da Work Item
        wi_title: Titulo da WI
        agent: Nome do agente/skill que gerou os testes
        db_connection: String de conexao do banco (ex: "V2R010AC@localhost:1521/MFSPDB")
        manual_tests: list de dicts [{num, cenario, status, detalhe}]
        suite_results: dict {xml, range, total, passed, failed, duration_s, failures, verdict}
        evidence: dict {antes_summary, depois_summary, antes_url, depois_url}
        nao_testado: list de strings (o que nao foi coberto)
        attachments: list de dicts [{filename, url}]
    """
    manual_tests = manual_tests or []
    nao_testado = nao_testado or []
    attachments = attachments or []

    html = ['<div style="box-sizing:border-box;font-family:Segoe UI,sans-serif">']

    # ── Banner ──
    html.append(_banner("Pipeline AI - Resultados de Testes"))

    # ── Meta bar ──
    meta_items = [
        _meta_row("WI", f"#{wi_id} - {wi_title}"),
        _meta_row("Data", datetime.now().strftime("%Y-%m-%d %H:%M")),
        _meta_row("Agente", agent),
    ]
    if db_connection:
        meta_items.append(_meta_row("Banco", db_connection))
    html.append(_meta_bar(*meta_items))

    # ── Resumo / Veredicto ──
    total_pass = sum(1 for t in manual_tests if str(t.get("status", "")).upper() in ("PASS", "OK"))
    total_fail = sum(1 for t in manual_tests if str(t.get("status", "")).upper() in ("FAIL", "ERRO", "ERROR"))
    total_skip = sum(1 for t in manual_tests if str(t.get("status", "")).upper() in ("SKIP", "N/A"))
    total_tests = len(manual_tests)

    suite_pass = (suite_results or {}).get("passed", 0)
    suite_fail = (suite_results or {}).get("failed", 0)

    overall_fail = total_fail + suite_fail
    if overall_fail > 0:
        verdict = "FAIL"
        verdict_bg, verdict_fg = COLORS["fail_bg"], COLORS["fail_fg"]
    elif total_tests == 0 and not suite_results:
        verdict = "SEM TESTES"
        verdict_bg, verdict_fg = COLORS["skip_bg"], COLORS["skip_fg"]
    else:
        verdict = "PASS"
        verdict_bg, verdict_fg = COLORS["pass_bg"], COLORS["pass_fg"]

    # Allow override do veredicto
    if suite_results and suite_results.get("verdict"):
        verdict = suite_results["verdict"]
        verdict_bg, verdict_fg = _status_color(verdict) if _status_color(verdict)[0] else (COLORS["pass_bg"], COLORS["pass_fg"])

    html.append(
        f'<div style="text-align:center;padding:12px;margin:0 0 12px 0;'
        f'background:{verdict_bg};border:2px solid {verdict_fg};border-radius:4px">'
        f'<span style="font-size:1.3em;font-weight:bold;color:{verdict_fg}">'
        f'Veredicto: {html_escape(verdict)}</span><br>'
        f'<span style="font-size:0.9em">'
    )
    parts = []
    if total_tests > 0:
        parts.append(f'Manuais: {total_pass}/{total_tests} PASS')
    if suite_results:
        parts.append(f'Suite: {suite_pass} OK, {suite_fail} FAIL')
    if total_skip > 0:
        parts.append(f'{total_skip} SKIP')
    html.append(" &nbsp;|&nbsp; ".join(parts) if parts else "Nenhum teste executado")
    html.append('</span></div>')

    # ── Secao: Testes Manuais ──
    if manual_tests:
        html.append(_section_header(f"Testes Manuais ({total_pass}/{total_tests} PASS)"))
        html.append(_table_open())
        html.append(_table_header("#", "Cenario", "Status", "Detalhe",
                                  widths=["5%", "40%", "12%", "43%"]))
        for t in manual_tests:
            status = str(t.get("status", "")).upper()
            bg, _ = _status_color(status)
            html.append(_table_row(
                html_escape(str(t.get("num", ""))),
                html_escape(t.get("cenario", "")),
                _status_badge(status),
                html_escape(t.get("detalhe", "")),
                bg=bg
            ))
        html.append('</table>')

    # ── Secao: SuiteAutomation ──
    if suite_results:
        html.append(_section_header("SuiteAutomation"))
        html.append('<ul style="margin:4px 0">')
        if suite_results.get("xml"):
            html.append(f'<li><b>XML:</b> <code>{html_escape(suite_results["xml"])}</code></li>')
        if suite_results.get("range"):
            html.append(f'<li><b>Range:</b> {html_escape(suite_results["range"])}</li>')

        duration = suite_results.get("duration_s", 0)
        html.append(
            f'<li><b>Comparacoes:</b> '
            f'<span style="color:{COLORS["pass_fg"]};font-weight:bold">{suite_pass} OK</span> | '
            f'<span style="color:{COLORS["fail_fg"]};font-weight:bold">{suite_fail} FAIL</span> | '
            f'Total: {suite_pass + suite_fail}'
            f'{f" | Duracao: {duration:.0f}s" if duration else ""}</li>'
        )
        html.append('</ul>')

        # Falhas
        failures = suite_results.get("failures", [])
        if failures:
            html.append(f'<p style="font-size:0.9em"><i>Primeiras falhas ({len(failures)} total):</i></p>')
            html.append('<ul style="font-size:0.85em;font-family:monospace">')
            for ff in failures[:10]:
                html.append(f'<li>{html_escape(ff)}</li>')
            if len(failures) > 10:
                html.append(f'<li>... e mais {len(failures) - 10} falhas</li>')
            html.append('</ul>')

    # ── Secao: Evidencias ANTES/DEPOIS ──
    if evidence:
        html.append(_section_header("Evidencias ANTES / DEPOIS"))
        html.append(_table_open())
        html.append(_table_header("ANTES (Pre-Fix)", "DEPOIS (Pos-Fix)", widths=["50%", "50%"]))

        antes_cell = _build_evidence_cell(evidence.get("antes_summary"))
        if evidence.get("antes_url"):
            antes_cell += f'<p><a href="{html_escape(evidence["antes_url"])}">Download evidencia ANTES</a></p>'

        depois_cell = _build_evidence_cell(evidence.get("depois_summary"))
        if evidence.get("depois_url"):
            depois_cell += f'<p><a href="{html_escape(evidence["depois_url"])}">Download evidencia DEPOIS</a></p>'

        html.append(
            f'<tr>'
            f'<td {_cell_style("vertical-align:top")}>{antes_cell}</td>'
            f'<td {_cell_style("vertical-align:top")}>{depois_cell}</td>'
            f'</tr>'
        )
        html.append('</table>')

    # ── Secao: Nao Testado ──
    if nao_testado:
        html.append(_section_header("Nao Testado / Pendencias"))
        html.append('<ul style="margin:4px 0">')
        for item in nao_testado:
            html.append(f'<li>{html_escape(item)}</li>')
        html.append('</ul>')

    # ── Secao: Arquivos Anexados ──
    if attachments:
        html.append(_section_header("Arquivos Anexados"))
        html.append('<ul style="margin:4px 0">')
        for att in attachments:
            name = att.get("filename", "arquivo")
            url = att.get("url", "")
            if url:
                html.append(f'<li><a href="{html_escape(url)}">{html_escape(name)}</a></li>')
            else:
                html.append(f'<li>{html_escape(name)}</li>')
        html.append('</ul>')

    # ── Footer ──
    html.append(_footer(agent))
    html.append('</div>')

    return "\n".join(html)


# ── Template 3: N3 Brief ─────────────────────────────────────────────

def build_n3_brief_comment(
    wi_id,
    wi_title,
    n3_brief,
    pipeline="taxone-n3-triage",
):
    """
    Gera HTML padronizado para comment de N3 Brief (Enrichment Report).

    Args:
        wi_id: ID da Work Item
        wi_title: Titulo da WI
        n3_brief: dict completo do N3 Brief (conforme schema n3_brief.json)
        pipeline: Nome do pipeline/agente gerador
    """
    n3_brief = n3_brief or {}

    html = ['<div style="box-sizing:border-box;font-family:Segoe UI,sans-serif">']

    # ── Banner ──
    html.append(_banner("N3 Brief - Enrichment Report"))

    # ── Meta bar ──
    meta_items = [
        _meta_row("WI", f"#{wi_id} - {wi_title}"),
        _meta_row("Data", datetime.now().strftime("%Y-%m-%d %H:%M")),
        _meta_row("Agente", pipeline),
    ]
    module_info = n3_brief.get("module", {})
    if module_info.get("primary"):
        meta_items.append(_meta_row("Modulo", f'{module_info["primary"]} (via {module_info.get("resolved_via", "?")})'))

    confidence = n3_brief.get("confidence", {})
    overall = confidence.get("overall", 0)
    conf_color = COLORS["pass_fg"] if overall >= 80 else (COLORS["skip_fg"] if overall >= 50 else COLORS["fail_fg"])
    meta_items.append(f'<b>Confianca:</b> <span style="color:{conf_color};font-weight:bold">{overall}%</span>')

    html.append(_meta_bar(*meta_items))

    # ── Secao: Resumo do Problema ──
    problem = n3_brief.get("problem", {})
    if problem:
        html.append(_section_header("Resumo do Problema"))
        html.append('<ul style="margin:4px 0">')
        if problem.get("summary"):
            html.append(f'<li>{html_escape(problem["summary"])}</li>')
        if problem.get("error_message"):
            html.append(f'<li><b>Erro:</b> <code>{html_escape(problem["error_message"])}</code></li>')
        if problem.get("error_codes"):
            codes = ", ".join(f'<code>{html_escape(c)}</code>' for c in problem["error_codes"])
            html.append(f'<li><b>Codigos:</b> {codes}</li>')
        if problem.get("operation_type"):
            html.append(f'<li><b>Operacao:</b> {html_escape(problem["operation_type"])}</li>')
        html.append('</ul>')

    # ── Secao: Analise de Anexos ──
    att_analysis = problem.get("attachments_analysis") if problem else None
    if att_analysis and att_analysis.get("total_files", 0) > 0:
        logs_n = att_analysis.get("logs_processed", 0)
        xmls_n = att_analysis.get("xmls_processed", 0)
        total_n = att_analysis.get("total_files", 0)
        html.append(_section_header(f"Analise de Anexos ({total_n} arquivos)"))
        html.append(f'<p style="font-size:0.9em">'
                    f'<b>Logs processados:</b> {logs_n} &nbsp;|&nbsp; '
                    f'<b>XMLs processados:</b> {xmls_n} &nbsp;|&nbsp; '
                    f'<b>Total:</b> {total_n}</p>')

        # Key findings
        findings = att_analysis.get("key_findings", [])
        if findings:
            html.append(f'<p style="font-size:0.9em"><b>Achados principais:</b></p>')
            html.append('<ul style="margin:4px 0;font-size:0.9em">')
            for f in findings:
                html.append(f'<li>{html_escape(f)}</li>')
            html.append('</ul>')

        # Files table
        files = att_analysis.get("files", [])
        if files:
            html.append(_table_open())
            html.append(_table_header("Arquivo", "Fonte", "Tipo", "Processado", widths=["40%", "15%", "15%", "30%"]))
            for af in files:
                processed = "SIM" if af.get("processed") else "NAO"
                html.append(_table_row(
                    f'<code>{html_escape(af.get("filename", ""))}</code>',
                    html_escape(af.get("source", "wi")),
                    html_escape(af.get("type", "?")),
                    _status_badge("PASS" if af.get("processed") else "SKIP")
                ))
            html.append('</table>')

    # ── Secao: Contexto do Cliente ──
    customer = n3_brief.get("customer", {})
    html.append(_section_header("Contexto do Cliente"))
    html.append(_table_open())
    html.append(_table_header("Campo", "Valor", widths=["30%", "70%"]))
    for field, label in [("tenant", "Empresa/Tenant"), ("environment", "Ambiente"),
                         ("version", "Versao"), ("cnpj", "CNPJ")]:
        val = customer.get(field)
        if val:
            html.append(_table_row(label, html_escape(str(val))))
        else:
            html.append(_table_row(label, _status_badge("NAO INFORMADO")))
    html.append('</table>')

    # ── Secao: Contexto Zendesk ──
    zendesk = n3_brief.get("zendesk", {})
    if zendesk and zendesk.get("ticket_id"):
        html.append(_section_header("Contexto Zendesk"))
        html.append('<ul style="margin:4px 0">')
        zd_status = str(zendesk.get("status", "")).upper()
        html.append(f'<li><b>Ticket:</b> TKT{zendesk["ticket_id"]} | '
                    f'<b>Status:</b> {_status_badge(zd_status)}</li>')
        if zendesk.get("requester"):
            html.append(f'<li><b>Solicitante:</b> {html_escape(zendesk["requester"])}</li>')
        if zendesk.get("comments_summary"):
            html.append(f'<li><b>Resumo:</b> {html_escape(zendesk["comments_summary"])}</li>')
        if zendesk.get("root_cause_mentioned"):
            html.append(f'<li><b>Causa raiz mencionada:</b> {html_escape(zendesk["root_cause_mentioned"])}</li>')
        if zendesk.get("is_solved_without_code_change"):
            html.append(f'<li>{_status_badge("WARNING")} Resolvido sem alteracao de codigo (sinal de not-a-bug)</li>')
        html.append('</ul>')

        # Timeline
        timeline = zendesk.get("timeline", [])
        if timeline:
            html.append(f'<p style="font-size:0.9em"><b>Timeline ({len(timeline)} eventos):</b></p>')
            html.append(_table_open())
            html.append(_table_header("Data", "Ator", "Acao", widths=["15%", "15%", "70%"]))
            for evt in timeline[:10]:
                html.append(_table_row(
                    html_escape(str(evt.get("date", ""))),
                    html_escape(str(evt.get("actor", ""))),
                    html_escape(str(evt.get("action", "")))
                ))
            html.append('</table>')

    # ── Secao: Passos de Reproducao ──
    repro_steps = problem.get("repro_steps", [])
    html.append(_section_header("Passos de Reproducao"))
    if repro_steps:
        html.append('<ol style="margin:4px 0">')
        for step in repro_steps:
            html.append(f'<li>{html_escape(step)}</li>')
        html.append('</ol>')
    else:
        html.append(f'<p>{_status_badge("FAIL")} <b>Repro steps AUSENTES</b> — '
                    f'PM deve solicitar ao N1/N2</p>')

    # ── Secao: Matches Historicos ──
    hist = n3_brief.get("historical_matches", {})
    html.append(_section_header("Matches Historicos"))
    html.append(_table_open())
    html.append(_table_header("Fonte", "Status", "Match", "Detalhe", widths=["18%", "12%", "30%", "40%"]))

    # FAQ
    faq = hist.get("faq_triage", {})
    faq_class = faq.get("classification", "N/A")
    faq_article = faq.get("matched_article") or "nenhum"
    html.append(_table_row(
        "FAQ Triage",
        _status_badge("OK" if faq.get("score", 0) > 0 else "N/A"),
        html_escape(faq_class),
        f'Score: {faq.get("score", 0)} | Artigo: {html_escape(faq_article)}'
    ))

    # Zendesk Patterns
    zd_patterns = hist.get("zendesk_patterns", {})
    zd_matches = zd_patterns.get("matches", [])
    zd_not_bug = zd_patterns.get("not_a_bug_signal", False)
    html.append(_table_row(
        "Zendesk Patterns",
        _status_badge("OK" if zd_matches else "N/A"),
        f'{len(zd_matches)} match(es)' if zd_matches else "nenhum",
        f'Not-a-bug signal: {"SIM" if zd_not_bug else "NAO"}'
    ))

    # Solutions
    sol = hist.get("solutions", {})
    sol_matches = sol.get("matches", [])
    sol_detail = sol_matches[0].get("title", "") if sol_matches else "nenhum"
    html.append(_table_row(
        "Solutions",
        _status_badge("OK" if sol_matches else "N/A"),
        f'{len(sol_matches)} match(es)' if sol_matches else "nenhum",
        html_escape(sol_detail[:80])
    ))

    # ADO Fixes
    ado = hist.get("ado_fixes", {})
    ado_matches = ado.get("matches", [])
    ado_detail = ado_matches[0].get("title", "") if ado_matches else "nenhum"
    html.append(_table_row(
        "ADO Fixes",
        _status_badge("OK" if ado_matches else "N/A"),
        f'{len(ado_matches)} match(es)' if ado_matches else "nenhum",
        html_escape(ado_detail[:80])
    ))

    html.append('</table>')

    # ── Secao: Mapeamento de Componentes ──
    comp = n3_brief.get("component_mapping", {})
    if comp and (comp.get("tables_suspected") or comp.get("packages_suspected")):
        html.append(_section_header("Mapeamento de Componentes"))
        html.append('<ul style="margin:4px 0">')
        if module_info.get("primary"):
            html.append(f'<li><b>Modulo:</b> {html_escape(module_info["primary"])} '
                        f'({html_escape(module_info.get("vertical", ""))})</li>')
        tables = comp.get("tables_suspected", [])
        if tables:
            tables_str = ", ".join(f'<code>{html_escape(t)}</code>' for t in tables[:10])
            html.append(f'<li><b>Tabelas suspeitas:</b> {tables_str}</li>')
        pkgs = comp.get("packages_suspected", [])
        if pkgs:
            pkgs_str = ", ".join(f'<code>{html_escape(p)}</code>' for p in pkgs[:10])
            html.append(f'<li><b>Packages suspeitos:</b> {pkgs_str}</li>')
        html.append(f'<li><i>Fonte: {html_escape(comp.get("mapping_source", "?"))}</i></li>')
        html.append('</ul>')

    # ── Secao: Gaps de Dados ──
    gaps = n3_brief.get("data_gaps", [])
    if gaps:
        html.append(_section_header("Gaps de Dados"))
        html.append(_table_open())
        html.append(_table_header("Campo", "Severidade", "Motivo", widths=["25%", "15%", "60%"]))
        for gap in gaps:
            sev = str(gap.get("severity", "")).upper()
            sev_badge = _status_badge("FAIL" if sev == "HIGH" else ("WARNING" if sev == "MEDIUM" else "N/A"))
            html.append(_table_row(
                f'<code>{html_escape(gap.get("field", ""))}</code>',
                sev_badge,
                html_escape(gap.get("reason", ""))
            ))
        html.append('</table>')

    # ── Secao: Sinais de Classificacao ──
    signals = n3_brief.get("classification_signals", {})
    has_signals = any(signals.get(k) for k in ["not_a_bug_indicators", "feature_request_indicators",
                                                "duplicate_indicators", "rule_change_indicators",
                                                "incomplete_analysis_indicators"])
    if has_signals:
        html.append(_section_header("Sinais de Classificacao"))
        html.append(f'<p style="font-size:0.85em;color:#888"><i>Para avaliacao do PM — '
                    f'sinais sao observacoes brutas, NAO veredictos</i></p>')
        html.append('<ul style="margin:4px 0">')
        for key, label in [("not_a_bug_indicators", "Not-a-Bug"),
                           ("feature_request_indicators", "Feature Request"),
                           ("duplicate_indicators", "Duplicata"),
                           ("rule_change_indicators", "Chamado de Regra"),
                           ("incomplete_analysis_indicators", "Analise Incompleta")]:
            items = signals.get(key, [])
            if items:
                html.append(f'<li><b>{label}:</b></li><ul>')
                for item in items:
                    html.append(f'<li>{html_escape(item)}</li>')
                html.append('</ul>')
        html.append('</ul>')

    # ── Secao: Indice de Confianca ──
    if confidence:
        html.append(_section_header("Indice de Confianca"))
        html.append(_table_open())
        html.append(_table_header("Metrica", "Valor", widths=["40%", "60%"]))
        for key, label in [("data_completeness", "Completude dos Dados"),
                           ("module_resolution", "Resolucao de Modulo"),
                           ("zendesk_enrichment", "Enriquecimento Zendesk"),
                           ("overall", "GERAL")]:
            val = confidence.get(key, 0)
            bar_color = COLORS["pass_fg"] if val >= 80 else (COLORS["skip_fg"] if val >= 50 else COLORS["fail_fg"])
            bar_width = max(val, 5)
            bar_html = (f'<div style="background:#eee;border-radius:3px;overflow:hidden;height:18px;position:relative">'
                        f'<div style="background:{bar_color};width:{bar_width}%;height:100%"></div>'
                        f'<span style="position:absolute;left:6px;top:0;font-size:0.85em;font-weight:bold;'
                        f'line-height:18px">{val}%</span></div>')
            style = "font-weight:bold" if key == "overall" else ""
            html.append(_table_row(f'<span style="{style}">{label}</span>', bar_html))
        html.append('</table>')

        if confidence.get("note"):
            html.append(f'<p style="font-size:0.85em;color:#888"><i>{html_escape(confidence["note"])}</i></p>')

    # ── Footer ──
    html.append(_footer(pipeline))
    html.append('</div>')

    return "\n".join(html)


def _build_evidence_cell(summary):
    """Gera HTML de uma celula de evidencia (fases com status)."""
    if not summary or not summary.get("phases"):
        return "<p><i>Evidencia nao disponivel</i></p>"

    rows = ""
    for p in summary["phases"]:
        bg, _ = _status_color(p.get("status", p.get("css_class", "")))
        bg = bg or "#fff"
        rows += (f'<tr style="background:{bg}">'
                 f'<td style="padding:3px 6px;border:1px solid {COLORS["border"]}">'
                 f'{html_escape(p.get("fase", p.get("name", "")))}</td>'
                 f'<td style="padding:3px 6px;border:1px solid {COLORS["border"]}">'
                 f'<b>{html_escape(p.get("status", ""))}</b></td>'
                 f'<td style="padding:3px 6px;border:1px solid {COLORS["border"]}">'
                 f'{html_escape(p.get("duracao", p.get("duration", "")))}</td>'
                 f'</tr>')

    table = (f'<table style="border-collapse:collapse;font-size:0.85em;width:100%">'
             f'<tr style="background:{COLORS["header_bg"]};color:{COLORS["header_fg"]}">'
             f'<th style="padding:3px 6px">Fase</th>'
             f'<th style="padding:3px 6px">Status</th>'
             f'<th style="padding:3px 6px">Duracao</th></tr>'
             f'{rows}</table>')

    if summary.get("resumo"):
        table += f'<p style="font-size:0.85em"><b>{html_escape(summary["resumo"])}</b></p>'

    return table


# ── Post to ADO ──────────────────────────────────────────────────────

def get_ado_pat():
    """Retorna PAT do Azure DevOps (env ou .env file)."""
    pat = os.environ.get("ADO_PAT", "")
    if not pat:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("ADO_PAT="):
                    pat = line.partition("=")[2].strip().strip('"').strip("'")
                    break
    return pat


def post_discussion_comment(wi_id, html, pat=None):
    """
    Posta Discussion comment na WI do ADO.

    Args:
        wi_id: ID da Work Item
        html: Conteudo HTML do comment
        pat: PAT do ADO (se None, busca automaticamente)

    Returns:
        True se postou com sucesso, False caso contrario
    """
    import requests

    if not pat:
        pat = get_ado_pat()
    if not pat:
        print("ERRO: ADO_PAT nao configurado", file=sys.stderr)
        return False

    auth = base64.b64encode(f":{pat}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }

    url = f"{ADO_API}/wit/workitems/{wi_id}/comments?api-version=7.1-preview.4"
    resp = requests.post(url, headers=headers, json={"text": html}, timeout=30)

    if resp.status_code in (200, 201):
        comment_id = resp.json().get("id", "?")
        print(f"  Discussion comment postado: ID={comment_id} (WI #{wi_id})")
        return True
    else:
        print(f"  FALHA ao postar comment: {resp.status_code}: {resp.text[:300]}",
              file=sys.stderr)
        return False


# ── Fetch WI from ADO ────────────────────────────────────────────────

def _fetch_wi(wi_id, pat=None):
    """Busca dados da WI no ADO. Retorna dict com campos ou None."""
    import requests

    if not pat:
        pat = get_ado_pat()
    if not pat:
        print("ERRO: ADO_PAT nao configurado", file=sys.stderr)
        return None

    auth = base64.b64encode(f":{pat}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    url = (f"{ADO_API}/wit/workitems/{wi_id}"
           f"?$expand=relations&api-version=7.1")
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        print(f"ERRO: Nao foi possivel buscar WI #{wi_id}: {resp.status_code}",
              file=sys.stderr)
        return None

    fields = resp.json().get("fields", {})
    return {
        "id": wi_id,
        "title": fields.get("System.Title", ""),
        "state": fields.get("System.State", ""),
        "type": fields.get("System.WorkItemType", ""),
        "tags": fields.get("System.Tags", ""),
        "area_path": fields.get("System.AreaPath", ""),
        "assigned_to": (fields.get("System.AssignedTo") or {}).get("displayName", ""),
        "developer": (fields.get("Custom.Developer") or {}).get("displayName", ""),
    }


def _find_pr_for_wi(wi_id):
    """Busca PR no GitHub associada a WI. Retorna dict ou None."""
    import subprocess
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", "tr/taxone_dw", "--state", "all",
             "--search", str(wi_id), "--json", "number,title,url,state",
             "--limit", "3"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            prs = json.loads(result.stdout)
            for pr in prs:
                if str(wi_id) in pr.get("title", ""):
                    return pr
            return prs[0] if prs else None
    except Exception:
        pass
    return None


def _find_test_report(wi_id):
    """Busca test_report.md ou test_data_manifest.json em tests/{wi_id}/."""
    test_dir = PROJECT_ROOT / "tests" / str(wi_id)
    if not test_dir.exists():
        return None, None

    report = test_dir / "test_report.md"
    manifest = test_dir / "test_data_manifest.json"

    report_data = None
    if report.exists():
        report_data = report.read_text(encoding="utf-8")

    manifest_data = None
    if manifest.exists():
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            pass

    return report_data, manifest_data


def _parse_test_report_md(report_text):
    """Extrai testes manuais de um test_report.md com tabela markdown."""
    import re
    tests = []
    if not report_text:
        return tests

    # Buscar tabela com | # | Teste | Status | Detalhe |
    lines = report_text.splitlines()
    in_table = False
    num = 0
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            if in_table:
                in_table = False
            continue

        cols = [c.strip() for c in line.split("|")]
        cols = [c for c in cols if c]  # remove vazios

        if len(cols) < 3:
            continue

        # Pular header e separador
        if cols[0] in ("#", "---", "Teste", "Cenario") or set(cols[0]) <= {"-"}:
            in_table = True
            continue

        # Tentar parsear como linha de teste
        try:
            test_num = int(cols[0])
        except ValueError:
            test_num = num + 1

        status = "PASS"
        for c in cols:
            if "FAIL" in c.upper():
                status = "FAIL"
                break
            elif "SKIP" in c.upper():
                status = "SKIP"
                break

        cenario = cols[1] if len(cols) > 1 else ""
        # Limpar markdown bold/italic
        cenario = re.sub(r'\*\*([^*]+)\*\*', r'\1', cenario)

        detalhe = cols[3] if len(cols) > 3 else (cols[2] if len(cols) > 2 else "")
        detalhe = re.sub(r'\*\*([^*]+)\*\*', r'\1', detalhe)

        num = test_num
        tests.append({
            "num": test_num,
            "cenario": cenario,
            "status": status,
            "detalhe": detalhe,
        })

    return tests


def _parse_suite_log(wi_id):
    """Busca resultados do SuiteAutomation em tests/{wi_id}/suite_*.log."""
    import re, glob
    test_dir = PROJECT_ROOT / "tests" / str(wi_id)
    logs = sorted(test_dir.glob("suite_*.log")) if test_dir.exists() else []
    if not logs:
        return None

    log_text = logs[-1].read_text(encoding="utf-8", errors="replace")

    # Parse: "Comparacao: X arquivos comparados, Y OK, Z FAIL"
    m = re.search(r'(\d+)\s+arquivos?\s+comparados.*?(\d+)\s+OK.*?(\d+)\s+FAIL', log_text, re.I)
    total = int(m.group(1)) if m else 0
    passed = int(m.group(2)) if m else 0
    failed = int(m.group(3)) if m else 0

    # Parse XML name
    xml_m = re.search(r'XML:\s*(\S+\.xml)', log_text, re.I)
    xml_name = xml_m.group(1) if xml_m else ""

    # Parse range
    range_m = re.search(r'Range:\s*(\S+)', log_text, re.I)
    range_str = range_m.group(1) if range_m else ""

    # Parse duration
    dur_m = re.search(r'Duracao:\s*([\d.]+)s', log_text, re.I)
    duration = float(dur_m.group(1)) if dur_m else 0

    verdict = "PASS" if failed == 0 and total > 0 else ("FAIL" if failed > 0 else "N/A")

    return {
        "xml": xml_name,
        "range": range_str,
        "total": total,
        "passed": passed,
        "failed": failed,
        "duration_s": duration,
        "verdict": verdict,
        "failures": [],
    }


# ── Interactive CLI ──────────────────────────────────────────────────

_AUTO_MODE = [False]  # mutable container to avoid global statement

def _prompt(msg, default=""):
    """Prompt interativo com default. Em auto mode, retorna default sem perguntar."""
    if _AUTO_MODE[0]:
        if default:
            print(f"  {msg}: {default} (auto)")
        return default
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {msg}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val if val else default


def _prompt_tests_interactive(wi_id):
    """Coleta testes manualmente via prompt interativo."""
    tests = []
    print("\n  Adicionar testes manuais (Enter vazio para terminar):")
    num = 1
    while True:
        cenario = _prompt(f"  Cenario {num} (descricao)")
        if not cenario:
            break
        status = _prompt(f"  Status", "PASS").upper()
        detalhe = _prompt(f"  Detalhe", "")
        tests.append({"num": num, "cenario": cenario, "status": status, "detalhe": detalhe})
        num += 1
    return tests


def cmd_analysis(args):
    """Subcomando: postar comment de Analise & Implementacao."""
    wi_id = args.wi_id
    print(f"\n=== Analise & Implementacao - WI #{wi_id} ===\n")

    # Se JSON fornecido, usar direto
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
        html = build_analysis_comment(**data)
    else:
        # Buscar dados da WI
        wi = _fetch_wi(wi_id)
        if not wi:
            return False
        print(f"  WI: #{wi_id} - {wi['title']}")

        # Buscar PR
        pr = _find_pr_for_wi(wi_id)
        pr_info = {}
        if pr:
            pr_info = {"branch": f"MFS{wi_id}", "pr_url": pr["url"],
                       "pr_number": pr["number"]}
            print(f"  PR: #{pr['number']} ({pr['state']})")
        else:
            print("  PR: nao encontrada")

        # Coletar dados interativamente
        causa = _prompt("Causa raiz")
        veredicto = _prompt("Veredicto", "BUG_CONFIRMED")
        impacto = _prompt("Impacto", "")

        analysis = {"causa_raiz": causa, "veredicto": veredicto}
        if impacto:
            analysis["impacto"] = impacto

        impl = dict(pr_info)
        code_review = _prompt("Code Review", "PASS")
        compilacao = _prompt("Compilacao", "COMPILADO")
        impl["code_review"] = code_review
        impl["compilacao"] = compilacao

        pendencias_str = _prompt("Pendencias (separar por ;)", "")
        pendencias = [p.strip() for p in pendencias_str.split(";") if p.strip()] if pendencias_str else []

        html = build_analysis_comment(
            wi_id=wi_id, wi_title=wi["title"],
            pipeline=args.pipeline or "pipeline-ai",
            analysis=analysis,
            implementation=impl,
            pendencias=pendencias,
        )

    # Salvar ou postar
    if args.dry_run:
        out = Path(args.output or f"tests/{wi_id}/analysis_preview.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f'<html><head><meta charset="utf-8"></head><body>{html}</body></html>',
                       encoding="utf-8")
        print(f"\n  Preview salvo em: {out}")
        return True

    return post_discussion_comment(wi_id, html)


def cmd_tests(args):
    """Subcomando: postar comment de Resultados de Testes."""
    wi_id = args.wi_id
    print(f"\n=== Resultados de Testes - WI #{wi_id} ===\n")

    # Se JSON fornecido, usar direto
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
        html = build_test_results_comment(**data)
    else:
        # Buscar dados da WI
        wi = _fetch_wi(wi_id)
        if not wi:
            return False
        print(f"  WI: #{wi_id} - {wi['title']}")

        # Tentar auto-detectar testes de tests/{wi_id}/
        report_text, manifest = _find_test_report(wi_id)
        manual_tests = []

        if report_text:
            manual_tests = _parse_test_report_md(report_text)
            if manual_tests:
                print(f"  Auto-detectados {len(manual_tests)} testes de test_report.md")
                for t in manual_tests:
                    print(f"    {t['num']}. {t['cenario']}: {t['status']}")

                usar = _prompt("Usar estes testes? (s/n)", "s")
                if usar.lower() != "s":
                    manual_tests = []

        if not manual_tests:
            manual_tests = _prompt_tests_interactive(wi_id)

        # Suite results
        suite_results = _parse_suite_log(wi_id)
        if suite_results and suite_results.get("total", 0) > 0:
            print(f"  Suite detectado: {suite_results['xml']} "
                  f"({suite_results['passed']} OK, {suite_results['failed']} FAIL)")
        else:
            suite_results = None
            print("  Suite: nenhum resultado encontrado")

        nao_testado_str = _prompt("Nao testado / pendencias (separar por ;)", "")
        nao_testado = [n.strip() for n in nao_testado_str.split(";") if n.strip()] if nao_testado_str else []

        html = build_test_results_comment(
            wi_id=wi_id, wi_title=wi["title"],
            agent=args.agent or "QA Manual",
            db_connection=args.db or "",
            manual_tests=manual_tests,
            suite_results=suite_results,
            nao_testado=nao_testado,
            attachments=[],
        )

    # Salvar ou postar
    if args.dry_run:
        out = Path(args.output or f"tests/{wi_id}/tests_preview.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f'<html><head><meta charset="utf-8"></head><body>{html}</body></html>',
                       encoding="utf-8")
        print(f"\n  Preview salvo em: {out}")
        return True

    return post_discussion_comment(wi_id, html)


def cmd_from_json(args):
    """Subcomando: gerar e postar a partir de arquivo JSON."""
    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    template_type = data.get("type", args.type)
    wi_id = data.get("wi_id", args.wi_id)

    if template_type == "analysis":
        data.pop("type", None)
        html = build_analysis_comment(**data)
    elif template_type == "tests":
        data.pop("type", None)
        html = build_test_results_comment(**data)
    else:
        print(f"ERRO: type deve ser 'analysis' ou 'tests', recebido: {template_type}",
              file=sys.stderr)
        return False

    if args.dry_run:
        out = Path(args.output or f"tests/{wi_id}/{template_type}_preview.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f'<html><head><meta charset="utf-8"></head><body>{html}</body></html>',
                       encoding="utf-8")
        print(f"Preview salvo em: {out}")
        return True

    return post_discussion_comment(wi_id, html)


def cmd_n3_brief(args):
    """Subcomando: postar comment de N3 Brief."""
    wi_id = args.wi_id
    print(f"\n=== N3 Brief - WI #{wi_id} ===\n")

    if not args.json:
        print("ERRO: N3 Brief requer --json com o n3_brief.json gerado pelo agente",
              file=sys.stderr)
        return False

    n3_brief = json.loads(Path(args.json).read_text(encoding="utf-8"))
    wi_title = n3_brief.get("wi", {}).get("title", f"WI #{wi_id}")
    html = build_n3_brief_comment(
        wi_id=wi_id, wi_title=wi_title, n3_brief=n3_brief,
        pipeline=args.pipeline or "taxone-n3-triage",
    )

    if args.dry_run:
        out = Path(args.output or f"tests/{wi_id}/n3_brief_preview.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f'<html><head><meta charset="utf-8"></head><body>{html}</body></html>',
                       encoding="utf-8")
        print(f"\n  Preview salvo em: {out}")
        return True

    return post_discussion_comment(wi_id, html)


def cmd_demo(args):
    """Subcomando: gerar HTML de demonstracao."""
    analysis_html = build_analysis_comment(
        wi_id=9999999, wi_title="Exemplo - Titulo da Demanda",
        pipeline="taxone-auto-fix",
        analysis={"causa_raiz": "Descricao da causa raiz do problema",
                  "veredicto": "BUG_CONFIRMED", "impacto": "Descricao do impacto"},
        implementation={"branch": "MFS9999999",
                        "pr_url": "https://github.com/tr/taxone_dw/pull/99999",
                        "pr_number": 99999,
                        "files": [{"path": "artifacts/sp/msaf/exemplo.sql", "action": "modificado"}],
                        "code_review": "PASS", "compilacao": "COMPILADO"},
        phases=[{"name": "Phase 0 - Carga WI", "status": "PASS", "duration": "2s"},
                {"name": "Phase 1 - Knowledge", "status": "PASS", "duration": "5s"},
                {"name": "Phase 5 - Testes", "status": "PASS", "duration": "60s"}],
        pendencias=["Testes E2E Playwright nao executados"],
        zendesk={"ticket_id": "999999", "status": "SOLVED", "resumo": "Descricao do ticket"},
    )
    test_html = build_test_results_comment(
        wi_id=9999999, wi_title="Exemplo - Titulo da Demanda",
        agent="QA Manual", db_connection="V2R010AC@localhost:1521/MFSPDB",
        manual_tests=[
            {"num": 1, "cenario": "Cenario basico", "status": "PASS", "detalhe": "OK"},
            {"num": 2, "cenario": "Cenario de erro", "status": "FAIL", "detalhe": "Valor inesperado"},
            {"num": 3, "cenario": "Cenario opcional", "status": "SKIP", "detalhe": "N/A"},
        ],
        suite_results={"xml": "CT_EXEMPLO.xml", "range": "1;5", "total": 5,
                       "passed": 4, "failed": 1, "duration_s": 30, "verdict": "FAIL",
                       "failures": ["Diff em arquivo X: esperado 100, obtido 200"]},
        nao_testado=["Teste de performance", "Teste de concorrencia"],
        attachments=[{"filename": "test_report.md", "url": "#"}],
    )

    n3_brief_html = build_n3_brief_comment(
        wi_id=9999999, wi_title="TKT999999 - Exemplo Erro Importacao SAFX07",
        n3_brief={
            "version": "1.0.0",
            "generated_by": "taxone-n3-triage",
            "generated_at": "2026-03-24T14:30:00Z",
            "wi": {"id": 9999999, "title": "TKT999999 - Exemplo Erro Importacao SAFX07",
                   "type": "User Story", "request_type": "Erro", "state": "Active",
                   "severity": "2 - High"},
            "problem": {
                "summary": "Importacao SAFX07 falha com ORA-01400 ao processar lote com campo CFOP nulo",
                "repro_steps": ["Importar arquivo SAFX07 com layout padrao",
                                "Arquivo contem registro sem CFOP preenchido",
                                "Processo de importacao aborta com ORA-01400"],
                "error_message": 'ORA-01400: cannot insert NULL into ("V2R010AC"."X07"."CFOP")',
                "error_codes": ["ORA-01400"],
                "operation_type": "importacao",
            },
            "customer": {"tenant": "CARGILL", "environment": "UAT02", "version": None, "cnpj": None},
            "module": {"primary": "Basicos", "vertical": "Basicos",
                       "operation_type": "importacao", "resolved_via": "area_path"},
            "zendesk": {"ticket_id": 999999, "status": "open",
                        "requester": "cliente@exemplo.com", "comments_count": 5,
                        "comments_summary": "Cliente reportou em 19/03. N1 confirmou em 20/03. N2 escalou N3.",
                        "root_cause_mentioned": None, "is_solved_without_code_change": False,
                        "timeline": [
                            {"date": "2026-03-19", "actor": "cliente", "action": "Abriu ticket reportando erro"},
                            {"date": "2026-03-20", "actor": "N1", "action": "Confirmou reproducao, escalou N2"},
                            {"date": "2026-03-22", "actor": "N2", "action": "Tentou reimportar, mesmo erro. Escalou N3"},
                        ]},
            "historical_matches": {
                "faq_triage": {"score": 3, "classification": "SEM-MATCH", "matched_article": None},
                "zendesk_patterns": {"matches": [{"pattern_id": "basicos-importacao",
                    "vertical": "basicos", "ticket_count": 75, "is_not_bug": False,
                    "relevance": "MEDIUM", "keywords_matched": ["importacao", "SAFX07"]}],
                    "not_a_bug_signal": False},
                "solutions": {"matches": []},
                "ado_fixes": {"matches": [{"wi_id": 1053976, "title": "SAFX236 rejeita campo nulo",
                    "relevance": "LOW", "reason": "Mesmo padrao de erro em tabela diferente"}]},
            },
            "component_mapping": {"tables_suspected": ["X07", "SAFX07", "TMP_X07"],
                "packages_suspected": ["PKG_TMP_DOCTO_FISCAL_FPROC"],
                "screens_suspected": [], "mapping_source": "explicit_mention"},
            "classification_signals": {
                "not_a_bug_indicators": [],
                "feature_request_indicators": [],
                "duplicate_indicators": [],
                "rule_change_indicators": [],
                "incomplete_analysis_indicators": ["Versao do sistema nao informada"],
                "note": "Sinais RAW para avaliacao do PM.",
            },
            "data_gaps": [
                {"field": "customer.version", "severity": "HIGH",
                 "reason": "Versao nao mencionada. Impede verificar se ja corrigido em patch mais novo."},
                {"field": "customer.cnpj", "severity": "MEDIUM",
                 "reason": "CNPJ nao informado. Necessario para query remota se preciso."},
            ],
            "confidence": {"data_completeness": 70, "module_resolution": 95,
                           "zendesk_enrichment": 80, "overall": 79,
                           "note": "Completude abaixo de 80% por falta de versao e CNPJ"},
        },
    )

    full = (f'<html><head><meta charset="utf-8"><title>Demo Templates</title></head>'
            f'<body style="max-width:900px;margin:20px auto;font-family:Segoe UI,sans-serif">'
            f'<h1>Template 1: Analise &amp; Implementacao</h1>{analysis_html}'
            f'<br><br><h1>Template 2: Resultados de Testes</h1>{test_html}'
            f'<br><br><h1>Template 3: N3 Brief</h1>{n3_brief_html}'
            f'</body></html>')

    out = Path(args.output) if args.output else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(full, encoding="utf-8")
        print(f"Demo salvo em: {out}")
    else:
        print(full)
    return True


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ADO Discussion Templates - Gerar e postar comments padronizados",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  # Postar analise interativamente
  python scripts/ado_discussion_templates.py analysis --wi-id 1068362

  # Postar testes (auto-detecta de tests/{WI_ID}/)
  python scripts/ado_discussion_templates.py tests --wi-id 1068362

  # Dry-run (preview sem postar)
  python scripts/ado_discussion_templates.py tests --wi-id 1068362 --dry-run

  # A partir de JSON
  python scripts/ado_discussion_templates.py from-json --json data.json

  # Demo
  python scripts/ado_discussion_templates.py demo --output preview.html
""")

    sub = parser.add_subparsers(dest="command", help="Subcomando")

    # Flags comuns
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--auto", action="store_true",
                        help="Modo automatico: usa defaults sem perguntar")

    # ── analysis ──
    p_analysis = sub.add_parser("analysis", parents=[common],
                                help="Postar comment de Analise & Implementacao")
    p_analysis.add_argument("--wi-id", type=int, required=True, help="ID da Work Item")
    p_analysis.add_argument("--pipeline", default="pipeline-ai", help="Nome do pipeline")
    p_analysis.add_argument("--json", help="Arquivo JSON com dados completos (pula modo interativo)")
    p_analysis.add_argument("--dry-run", action="store_true", help="Gerar preview sem postar")
    p_analysis.add_argument("--output", "-o", help="Arquivo de saida para preview")
    p_analysis.set_defaults(func=cmd_analysis)

    # ── tests ──
    p_tests = sub.add_parser("tests", parents=[common],
                             help="Postar comment de Resultados de Testes")
    p_tests.add_argument("--wi-id", type=int, required=True, help="ID da Work Item")
    p_tests.add_argument("--agent", default="QA Manual", help="Nome do agente/executor")
    p_tests.add_argument("--db", default="", help="String de conexao do banco")
    p_tests.add_argument("--json", help="Arquivo JSON com dados completos (pula modo interativo)")
    p_tests.add_argument("--dry-run", action="store_true", help="Gerar preview sem postar")
    p_tests.add_argument("--output", "-o", help="Arquivo de saida para preview")
    p_tests.set_defaults(func=cmd_tests)

    # ── n3-brief ──
    p_n3 = sub.add_parser("n3-brief", parents=[common],
                           help="Postar comment de N3 Brief (Enrichment Report)")
    p_n3.add_argument("--wi-id", type=int, required=True, help="ID da Work Item")
    p_n3.add_argument("--json", required=True, help="Arquivo n3_brief.json gerado pelo agente")
    p_n3.add_argument("--pipeline", default="taxone-n3-triage", help="Nome do pipeline/agente")
    p_n3.add_argument("--dry-run", action="store_true", help="Gerar preview sem postar")
    p_n3.add_argument("--output", "-o", help="Arquivo de saida para preview")
    p_n3.set_defaults(func=cmd_n3_brief)

    # ── from-json ──
    p_json = sub.add_parser("from-json", help="Gerar e postar a partir de arquivo JSON")
    p_json.add_argument("--json", required=True, help="Arquivo JSON com dados")
    p_json.add_argument("--type", choices=["analysis", "tests"], help="Tipo do template")
    p_json.add_argument("--wi-id", type=int, default=0, help="WI ID (override do JSON)")
    p_json.add_argument("--dry-run", action="store_true", help="Gerar preview sem postar")
    p_json.add_argument("--output", "-o", help="Arquivo de saida para preview")
    p_json.set_defaults(func=cmd_from_json)

    # ── demo ──
    p_demo = sub.add_parser("demo", help="Gerar HTML de demonstracao")
    p_demo.add_argument("--output", "-o", help="Arquivo de saida (default: stdout)")
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    _AUTO_MODE[0] = getattr(args, "auto", False)

    ok = args.func(args)
    sys.exit(0 if ok else 1)
