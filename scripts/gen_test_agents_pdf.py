"""Generate PDF summary of taxone_dw test agents."""
from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, 'TAX ONE Support Dev - Agentes de Teste', align='R', new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f'Pagina {self.page_no()}/{{nb}}', align='C')

    def section_title(self, title):
        self.set_font('Helvetica', 'B', 13)
        self.set_text_color(30, 70, 130)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def agent_card(self, name, color, description, tools, phase):
        self.set_fill_color(245, 247, 250)
        y_start = self.get_y()
        if y_start > 250:
            self.add_page()
            y_start = self.get_y()
        self.rect(10, y_start, 190, 28, 'F')
        colors = {
            'green': (76, 175, 80), 'blue': (66, 133, 244), 'cyan': (0, 188, 212),
            'orange': (255, 152, 0), 'yellow': (255, 193, 7), 'magenta': (156, 39, 176),
            'lime': (139, 195, 74), 'red': (244, 67, 54), 'white': (158, 158, 158)
        }
        r, g, b = colors.get(color, (100, 100, 100))
        self.set_fill_color(r, g, b)
        self.rect(10, y_start, 4, 28, 'F')
        self.set_xy(16, y_start + 2)
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(30, 30, 30)
        self.cell(120, 6, name)
        self.set_font('Helvetica', 'I', 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, phase, align='R')
        self.set_xy(16, y_start + 10)
        self.set_font('Helvetica', '', 9)
        self.set_text_color(60, 60, 60)
        self.multi_cell(180, 5, description)
        self.set_xy(16, y_start + 20)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, f'Tools: {tools}')
        self.set_y(y_start + 30)


pdf = PDF()
pdf.alias_nb_pages()
pdf.set_auto_page_break(auto=True, margin=20)
pdf.add_page()

# Title
pdf.set_font('Helvetica', 'B', 22)
pdf.set_text_color(30, 70, 130)
pdf.cell(0, 15, 'Agentes de Teste - taxone_dw', align='C', new_x="LMARGIN", new_y="NEXT")
pdf.set_font('Helvetica', '', 11)
pdf.set_text_color(100, 100, 100)
pdf.cell(0, 8, '9 agentes especializados para validacao local de PL/SQL, DDL e PowerBuilder', align='C', new_x="LMARGIN", new_y="NEXT")
pdf.cell(0, 6, 'Repositorio: taxone-support-dev  |  Branch: douglas.crespo  |  Data: 07/04/2026', align='C', new_x="LMARGIN", new_y="NEXT")
pdf.ln(8)

# Pipeline
pdf.section_title('Pipeline de Execucao')
pdf.set_font('Courier', '', 9)
pdf.set_text_color(50, 50, 50)
pipeline = [
    'git diff -> classificar arquivos',
    '  |',
    '  v',
    'Fase 1: DDL Runner (schema primeiro)',
    '  |',
    '  v',
    'Fase 2: SP Compiler ----+---- PB Analyzer  (paralelo)',
    '  |                     |',
    '  v                     v',
    'Fase 3: SQL Validator',
    '  |',
    '  v',
    'Fase 4: PB Compiler',
    '  |',
    '  v',
    'Fase 5: DW SQL Tester',
    '  |',
    '  v',
    'Fase 6: Desktop Tester (opcional)',
    '  |',
    '  v',
    'Fase 7: Evidence Generator -> Evidencias_TU_MFS{WI}.docx',
    '  |',
    '  v',
    'Relatorio Consolidado -> GO / NO-GO',
]
for line in pipeline:
    pdf.cell(0, 4, line, new_x="LMARGIN", new_y="NEXT")
pdf.ln(6)

# Agents
pdf.section_title('Orquestrador')
pdf.agent_card(
    'taxone-dw-test-orchestrator', 'magenta',
    'Orquestra os 8 agentes. Detecta arquivos alterados via git diff, decide quais executar, coordena em fases e gera relatorio GO/NO-GO.',
    'Read, Write, Bash, Grep, Glob, Agent',
    'Orquestrador'
)

pdf.section_title('Agentes de Banco (Oracle)')
pdf.agent_card(
    'taxone-dw-sp-compiler', 'green',
    'Compila packages PL/SQL (.pck) no Oracle local via sqlplus. Parseia erros ORA-/PLS-/SP2- e consulta user_errors.',
    'Read, Bash, Grep, Glob',
    'Fase 2a'
)
pdf.agent_card(
    'taxone-dw-ddl-runner', 'blue',
    'Executa scripts DDL de artifacts/bd/ no Oracle local. Valida antes de executar, respeita ordem, suporta dry-run.',
    'Read, Bash, Grep, Glob',
    'Fase 1'
)
pdf.agent_card(
    'taxone-dw-sql-validator', 'cyan',
    'Valida saude do banco apos compilacao/DDL. Verifica objetos invalidos, user_errors, dependencias. Recompila sob demanda.',
    'Read, Write, Bash, Grep, Glob',
    'Fase 3'
)

pdf.add_page()
pdf.section_title('Agentes PowerBuilder')
pdf.agent_card(
    'taxone-dw-pb-compiler', 'orange',
    'Compila PBLs e targets PB 19.0 via OrcaScript (orcascr190.exe). Gera .orca dinamicamente. Incremental primeiro, full como fallback.',
    'Read, Write, Bash, Grep, Glob',
    'Fase 4'
)
pdf.agent_card(
    'taxone-dw-pb-analyzer', 'yellow',
    'Analise estatica de fontes PB (.srd, .srw, .sru, .srm). Valida SQL de DataWindows, anti-patterns, referencias de menus.',
    'Read, Bash, Grep, Glob',
    'Fase 2b'
)
pdf.agent_card(
    'taxone-dw-pb-dw-tester', 'lime',
    'Teste funcional de DataWindows. Extrai SQL do retrieve= dos .srd, limpa escaping PB, executa no Oracle com WHERE 1=0.',
    'Read, Bash, Grep, Glob',
    'Fase 5'
)
pdf.agent_card(
    'taxone-dw-pb-desktop-tester', 'red',
    'Automacao desktop do app PB via pywinauto (default) ou AutoIt3. Suporta 3 modos: attach, exe, ide. Screenshots automaticos. Modo QA: funciona sem git.',
    'Read, Write, Bash, Grep, Glob',
    'Fase 6 (opcional) | QA'
)

pdf.section_title('Agente de Evidencia')
pdf.agent_card(
    'taxone-dw-evidence-generator', 'white',
    'Gera Evidencias_TU_MFS{WI_ID}.docx automaticamente. Coleta screenshots e resultados, monta no padrao do time (titulo + label + print). Modo QA: aceita prints de qualquer pasta.',
    'Read, Write, Bash, Grep, Glob',
    'Fase 7 | QA'
)

# Env vars table
pdf.ln(4)
pdf.section_title('Variaveis de Ambiente')
pdf.set_font('Helvetica', '', 9)
pdf.set_text_color(50, 50, 50)

env_vars = [
    ('TAXONE_DW_REPO', 'C:/Repositorios/taxone_dw', 'Path do repo', 'Sim'),
    ('SQLPLUS_PATH', 'C:/app/client/.../sqlplus.exe', 'Path do sqlplus', 'Sim'),
    ('ORACLE_CONN_STRING', 'V2R010AC/V2R010AC@MFS', 'Conexao Oracle', 'Sim'),
    ('ORCASCRIPT_PATH', 'C:/.../orcascr190.exe', 'Path do OrcaScript', 'Se usar PB'),
    ('PBC_PATH', 'C:/.../pbc190.exe', 'PB Compiler', 'Opcional'),
    ('PB_APP_LAUNCH_MODE', 'attach', 'Modo lancamento', 'Se desktop'),
    ('PB_APP_WINDOW_TITLE', 'MasterSaf', 'Titulo janela PB', 'Se desktop'),
]

pdf.set_fill_color(30, 70, 130)
pdf.set_text_color(255, 255, 255)
pdf.set_font('Helvetica', 'B', 8)
pdf.cell(42, 7, '  Variavel', fill=True)
pdf.cell(65, 7, '  Exemplo', fill=True)
pdf.cell(48, 7, '  Descricao', fill=True)
pdf.cell(35, 7, '  Obrigatorio', fill=True, new_x="LMARGIN", new_y="NEXT")

pdf.set_text_color(50, 50, 50)
for i, (var, ex, desc, req) in enumerate(env_vars):
    bg = (245, 247, 250) if i % 2 == 0 else (255, 255, 255)
    pdf.set_fill_color(*bg)
    pdf.set_font('Courier', '', 7)
    pdf.cell(42, 6, f'  {var}', fill=True)
    pdf.set_font('Helvetica', '', 7)
    pdf.cell(65, 6, f'  {ex}', fill=True)
    pdf.cell(48, 6, f'  {desc}', fill=True)
    pdf.cell(35, 6, f'  {req}', fill=True, new_x="LMARGIN", new_y="NEXT")

# Dependencies
pdf.ln(6)
pdf.section_title('Dependencias Python')
pdf.set_font('Courier', '', 10)
pdf.set_text_color(50, 50, 50)
pdf.cell(0, 6, 'pip install python-docx    # Evidence Generator', new_x="LMARGIN", new_y="NEXT")
pdf.cell(0, 6, 'pip install pywinauto      # Desktop Tester (opcao 1)', new_x="LMARGIN", new_y="NEXT")

# Quick reference
pdf.ln(6)
pdf.section_title('Referencia Rapida de Uso')
pdf.set_font('Helvetica', '', 9)
pdf.set_text_color(50, 50, 50)

commands = [
    ('B', 'Pipeline completo (Dev):'),
    ('C', '  "Usar taxone-dw-test-orchestrator para testar o branch atual"'),
    ('', ''),
    ('B', 'Agente individual (Dev):'),
    ('C', '  "Usar taxone-dw-sp-compiler para compilar PKG_IMP_ONLINE_FPROC.pck"'),
    ('C', '  "Usar taxone-dw-ddl-runner para executar DDL_MFS772688.sql"'),
    ('C', '  "Usar taxone-dw-pb-dw-tester para testar DataWindows alterados"'),
    ('C', '  "Usar taxone-dw-evidence-generator para gerar evidencia da WI 772688"'),
    ('', ''),
    ('B', 'Dry-run (ver plano sem executar):'),
    ('C', '  "Usar taxone-dw-test-orchestrator em modo dry-run"'),
    ('', ''),
    ('B', 'Modo QA (sem repo git - apenas .exe rodando):'),
    ('C', '  "Usar taxone-dw-pb-desktop-tester em modo QA para testar tela X. WI 772688"'),
    ('C', '  "Usar taxone-dw-evidence-generator em modo QA. Prints em C:\\prints\\"'),
]
for style, line in commands:
    if style == '':
        pdf.ln(2)
    elif style == 'B':
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    elif style == 'C':
        pdf.set_font('Courier', '', 8)
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")

# Docs reference
pdf.ln(6)
pdf.section_title('Documentacao Relacionada')
pdf.set_font('Helvetica', '', 9)
pdf.set_text_color(50, 50, 50)
docs = [
    'docs/testing-agents-setup.md   - Guia completo de setup para o time',
    'docs/SETUP_GUIDE.md            - Setup geral do taxone-support-dev',
    '.env.example                   - Template de variaveis de ambiente',
    'README.md                      - Visao geral do projeto',
]
for d in docs:
    pdf.cell(0, 5, d, new_x="LMARGIN", new_y="NEXT")

# Save
output = r'C:\Users\6123442\OneDrive - Thomson Reuters Incorporated\Desktop\Agentes_Teste_TaxOne_DW.pdf'
pdf.output(output)
print(f'PDF gerado com sucesso: {output}')
