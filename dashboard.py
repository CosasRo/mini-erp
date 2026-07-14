"""
Dashboard - Mini ERP PagoExpress
5 abas, uma por segmento: DRE, DRC, DRD, DRJC, DRR.
DRE segue o padrão de referência (estilo FP&A): KPIs com sparkline,
barras, linha, rosca, cascata (waterfall) e tabela detalhada.
As demais abas ainda são placeholders simples.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

ROXO_ESCURO = "#3A2D58"
ROXO_MEDIO = "#594A92"
ROXO_CLARO = "#8B7BB8"
AMARELO = "#ECBD42"
CINZA_CLARO = "#C9C7C1"
BRANCO = "#FFFFFF"
VERDE = "#7FCB9C"
VERMELHO = "#E5806B"

PALETA_GRAFICO = [ROXO_ESCURO, ROXO_MEDIO, AMARELO, CINZA_CLARO, ROXO_CLARO]

FUNDO_CARD = "#2A2140"
FUNDO_PAGINA = "#1a1530"


# ---------------------------------------------------------------
# Sparkline (SVG leve, sem depender do plotly, pra caber dentro do card)
# ---------------------------------------------------------------
def _sparkline_svg(valores: list, cor: str, largura: int = 100, altura: int = 30) -> str:
    if not valores or len(valores) < 2:
        return ""
    minimo, maximo = min(valores), max(valores)
    faixa = (maximo - minimo) or 1
    passo = largura / (len(valores) - 1)
    pontos = [
        f"{i * passo:.1f},{altura - ((v - minimo) / faixa) * (altura - 4) - 2:.1f}"
        for i, v in enumerate(valores)
    ]
    linha = " ".join(pontos)
    return f"""
    <svg width="{largura}" height="{altura}" viewBox="0 0 {largura} {altura}" style="display:block;">
        <polyline points="{linha}" fill="none" stroke="{cor}" stroke-width="2"
                  stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>
    </svg>
    """


def _kpi_card(icone: str, titulo: str, valor: str, delta: float, delta_label: str, historico: list):
    cor_delta = VERDE if delta >= 0 else VERMELHO
    seta = "▲" if delta >= 0 else "▼"
    sinal = "+" if delta >= 0 else ""
    spark = _sparkline_svg(historico, AMARELO)
    st.markdown(
        f"""
        <div style="background:{FUNDO_CARD}; border-radius:12px; padding:14px 16px;
                    border:1px solid #3d3560;">
            <div style="display:flex; align-items:center; gap:6px; font-size:12px;
                        color:{CINZA_CLARO}; text-transform:uppercase; letter-spacing:0.4px;">
                <span>{icone}</span><span>{titulo}</span>
            </div>
            <div style="font-size:22px; font-weight:700; color:{BRANCO}; margin-top:4px;">
                {valor}
            </div>
            <div style="font-size:11px; color:{cor_delta}; margin-top:2px;">
                {seta} {sinal}{delta:.1f}{delta_label}
            </div>
            <div style="margin-top:6px;">{spark}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------
# DRE -- dados de exemplo (mai/2026 x mai/2025), no formato do print
# ---------------------------------------------------------------
MESES_DRE = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

DRE_LINHAS = [
    ("Receita Líquida", 25845, 22942, False),
    ("Custo dos Produtos Vendidos", -15864, -14344, False),
    ("Lucro Bruto", 9981, 8598, True),
    ("Margem Bruta (%)", 38.7, 40.5, True),
    ("Despesas Operacionais", -4987, -4432, False),
    ("Outras Receitas (Despesas) Operacionais", -247, -190, False),
    ("EBITDA", 4747, 4166, True),
    ("Margem EBITDA (%)", 18.1, 18.8, True),
    ("Depreciação e Amortização", -812, -682, False),
    ("EBIT", 3935, 3485, False),
    ("Resultado Financeiro", 525, 421, False),
    ("Resultado Antes dos Impostos", 4460, 3906, True),
    ("Impostos sobre o Lucro", -1866, -1623, False),
    ("Lucro Líquido", 2594, 2283, True),
    ("Margem Líquida (%)", 10.1, 10.0, True),
]

RECEITA_ATUAL = [16.8, 17.9, 19.2, 22.9, 25.8, 24.1, 23.5, 24.8, 26.0, 27.1, 26.5, 28.3]
RECEITA_ANTERIOR = [14.9, 15.8, 16.9, 20.1, 22.9, 21.3, 20.9, 21.8, 22.9, 23.8, 23.3, 24.9]
MARGEM_EBITDA = [18.8, 19.1, 17.6, 18.5, 18.1, 18.3, 18.0, 18.4, 18.6, 18.9, 18.7, 19.0]

COMPOSICAO_RECEITA = [("Produto A", 38.9), ("Produto B", 27.1), ("Serviços", 18.3),
                       ("Produto C", 10.2), ("Outros", 5.5)]

WATERFALL_DRE = [
    ("mai/2025", 2283, "total"),
    ("Receita Líquida", 3603, "aumento"),
    ("Custo dos Produtos Vendidos", -2100, "diminuicao"),
    ("Despesas Operacionais", -900, "diminuicao"),
    ("Resultado Financeiro", 500, "aumento"),
    ("Impostos", -800, "diminuicao"),
    ("mai/2026", 2594, "total"),
]


def _grafico_barras_comparativo():
    fig = go.Figure()
    fig.add_bar(x=MESES_DRE, y=RECEITA_ANTERIOR, name="Anterior", marker_color=ROXO_CLARO)
    fig.add_bar(x=MESES_DRE, y=RECEITA_ATUAL, name="Atual", marker_color=ROXO_ESCURO,
                text=[f"{v:.1f} Mi" if v == max(RECEITA_ATUAL) else "" for v in RECEITA_ATUAL],
                textposition="outside")
    fig.update_layout(
        title=dict(text="Receita Líquida | R$ Mi", font=dict(color=BRANCO, size=13)),
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=CINZA_CLARO, size=10),
        legend=dict(orientation="h", y=1.15, font=dict(color=CINZA_CLARO)),
        margin=dict(t=60, b=10, l=10, r=10), height=280,
        xaxis=dict(gridcolor="#3d3560"), yaxis=dict(gridcolor="#3d3560"),
    )
    st.plotly_chart(fig, use_container_width=True)


def _grafico_linha_margem():
    fig = go.Figure()
    fig.add_scatter(x=MESES_DRE, y=MARGEM_EBITDA, mode="lines+markers+text",
                     line=dict(color=AMARELO, width=2.5), marker=dict(size=6, color=AMARELO),
                     text=[f"{v:.1f}%" for v in MARGEM_EBITDA], textposition="top center",
                     textfont=dict(size=9, color=CINZA_CLARO))
    fig.update_layout(
        title=dict(text="Margem EBITDA | %", font=dict(color=BRANCO, size=13)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=CINZA_CLARO, size=10),
        margin=dict(t=40, b=10, l=10, r=10), height=280,
        xaxis=dict(gridcolor="#3d3560"), yaxis=dict(gridcolor="#3d3560", ticksuffix="%"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _grafico_rosca_composicao():
    labels = [c[0] for c in COMPOSICAO_RECEITA]
    valores = [c[1] for c in COMPOSICAO_RECEITA]
    total = sum(v * 258 / 25.8 for v in [25.8])  # apenas para exibir "R$ 25,8 Mi" no centro
    fig = go.Figure(data=[go.Pie(
        labels=labels, values=valores, hole=0.62,
        marker=dict(colors=PALETA_GRAFICO, line=dict(color=FUNDO_PAGINA, width=2)),
        textinfo="percent", textfont=dict(color=BRANCO, size=10),
    )])
    fig.update_layout(
        title=dict(text="Composição da Receita Líquida", font=dict(color=BRANCO, size=13)),
        annotations=[dict(text="R$ 25,8 Mi<br>Total", showarrow=False,
                           font=dict(color=BRANCO, size=12))],
        showlegend=True, legend=dict(font=dict(color=CINZA_CLARO, size=10)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=40, b=10, l=10, r=10), height=300,
    )
    st.plotly_chart(fig, use_container_width=True)


def _grafico_waterfall():
    x = [w[0] for w in WATERFALL_DRE]
    y = [w[1] for w in WATERFALL_DRE]
    medidas = ["absolute" if w[2] == "total" else "relative" for w in WATERFALL_DRE]
    fig = go.Figure(go.Waterfall(
        x=x, y=y, measure=medidas,
        increasing=dict(marker=dict(color=VERDE)),
        decreasing=dict(marker=dict(color=VERMELHO)),
        totals=dict(marker=dict(color=ROXO_ESCURO)),
        connector=dict(line=dict(color="#3d3560")),
        text=[f"{v/1000:.1f} Mi" if abs(v) >= 1000 else f"{v:.0f} Mil" for v in y],
        textposition="outside", textfont=dict(color=CINZA_CLARO, size=9),
    ))
    fig.update_layout(
        title=dict(text="Evolução do Lucro Líquido | R$ Mil", font=dict(color=BRANCO, size=13)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=CINZA_CLARO, size=9),
        margin=dict(t=40, b=10, l=10, r=10), height=300,
        xaxis=dict(gridcolor="#3d3560"), yaxis=dict(gridcolor="#3d3560"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _tabela_dre_resumo():
    linhas_html = []
    for nome, atual, anterior, negrito in DRE_LINHAS:
        eh_pct = "%" in nome
        var = atual - anterior
        var_pct = (var / abs(anterior) * 100) if anterior else 0
        cor_var = VERDE if var >= 0 else VERMELHO

        fmt = (lambda v: f"{v:.1f}%") if eh_pct else (lambda v: f"{v:,.0f}")
        hist = [anterior + (atual - anterior) * i / 5 for i in range(6)]
        spark = _sparkline_svg(hist, ROXO_CLARO, largura=70, altura=20)

        peso = "font-weight:700;" if negrito else ""
        linhas_html.append(f"""
        <tr style="{peso} border-bottom:1px solid #3d3560;">
            <td style="padding:6px 10px; color:{BRANCO};">{nome}</td>
            <td style="padding:6px 10px; text-align:right; color:{CINZA_CLARO};">{fmt(atual)}</td>
            <td style="padding:6px 10px; text-align:right; color:{CINZA_CLARO};">{fmt(anterior)}</td>
            <td style="padding:6px 10px; text-align:right; color:{cor_var};">{fmt(var)}</td>
            <td style="padding:6px 10px; text-align:right; color:{cor_var};">{var_pct:.1f}%</td>
            <td style="padding:6px 10px; text-align:center;">{spark}</td>
        </tr>
        """)

    st.markdown(
        f"""
        <div style="background:{FUNDO_CARD}; border-radius:12px; padding:16px; border:1px solid #3d3560;">
            <div style="font-size:13px; font-weight:700; color:{BRANCO}; margin-bottom:8px;">
                DRE - Resumo | R$ Mil
            </div>
            <table style="width:100%; border-collapse:collapse; font-size:12px;">
                <tr style="border-bottom:2px solid #3d3560;">
                    <th style="padding:6px 10px; text-align:left; color:{CINZA_CLARO};">Conta</th>
                    <th style="padding:6px 10px; text-align:right; color:{CINZA_CLARO};">mai/2026</th>
                    <th style="padding:6px 10px; text-align:right; color:{CINZA_CLARO};">mai/2025</th>
                    <th style="padding:6px 10px; text-align:right; color:{CINZA_CLARO};">Var. R$</th>
                    <th style="padding:6px 10px; text-align:right; color:{CINZA_CLARO};">Var. %</th>
                    <th style="padding:6px 10px; text-align:center; color:{CINZA_CLARO};">Tendência</th>
                </tr>
                {''.join(linhas_html)}
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_dre():
    col1, col2, col3 = st.columns(3)
    with col1:
        st.selectbox("Período", ["mai/2026"], key="dre_periodo")
    with col2:
        st.selectbox("Comparativo", ["mai/2025"], key="dre_comparativo")
    with col3:
        st.selectbox("Acumulado", ["Ano", "Mês", "Trimestre"], key="dre_acumulado")

    st.caption("🚧 Dados de exemplo, ainda vamos ligar nos números reais.")
    st.markdown("<br>", unsafe_allow_html=True)

    kpis = st.columns(6)
    dados_kpi = [
        ("💲", "Receita Líquida", "R$ 25,8 Mi", 12.6, "%", [22.9, 23.5, 24.1, 24.8, 25.2, 25.8]),
        ("%", "Margem Bruta", "38,7%", -1.8, " p.p.", [40.5, 40.0, 39.5, 39.0, 38.9, 38.7]),
        ("⚡", "EBITDA", "R$ 4,7 Mi", 8.3, "%", [4.2, 4.3, 4.4, 4.5, 4.6, 4.7]),
        ("📊", "Margem EBITDA", "18,1%", -0.7, " p.p.", [18.8, 18.6, 18.3, 18.2, 18.1, 18.1]),
        ("💰", "Lucro Líquido", "R$ 2,6 Mi", 15.2, "%", [2.3, 2.35, 2.4, 2.45, 2.5, 2.6]),
        ("📈", "Margem Líquida", "10,1%", 0.2, " p.p.", [10.0, 10.0, 10.1, 10.0, 10.1, 10.1]),
    ]
    for col, (icone, titulo, valor, delta, delta_lbl, hist) in zip(kpis, dados_kpi):
        with col:
            _kpi_card(icone, titulo, valor, delta, delta_lbl, hist)

    st.markdown("<br>", unsafe_allow_html=True)
    col_a, col_b, col_c = st.columns([1.2, 1, 1])
    with col_a:
        _grafico_barras_comparativo()
    with col_b:
        _grafico_linha_margem()
    with col_c:
        _grafico_rosca_composicao()

    st.markdown("<br>", unsafe_allow_html=True)
    col_d, col_e = st.columns([1, 1.3])
    with col_d:
        _grafico_waterfall()
    with col_e:
        _tabela_dre_resumo()


# ---------------------------------------------------------------
# Demais abas -- ainda placeholders simples (a definir depois)
# ---------------------------------------------------------------
def _grafico_pizza(titulo: str, labels: list, valores: list):
    fig = go.Figure(data=[go.Pie(
        labels=labels, values=valores, hole=0.55,
        marker=dict(colors=PALETA_GRAFICO, line=dict(color=ROXO_ESCURO, width=2)),
        textfont=dict(color=BRANCO, size=11),
    )])
    fig.update_layout(
        title=dict(text=titulo, font=dict(color=BRANCO, size=13)),
        showlegend=True,
        legend=dict(font=dict(color=CINZA_CLARO, size=10)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=40, b=10, l=10, r=10),
        height=260,
    )
    st.plotly_chart(fig, use_container_width=True)


def _card_simples(titulo: str, valor: str):
    st.markdown(
        f"""
        <div style="background:{ROXO_MEDIO}; border-radius:10px; padding:14px 16px;
                    height:100%; box-shadow: 0 1px 3px rgba(0,0,0,0.2);">
            <div style="font-size:12px; color:{CINZA_CLARO}; text-transform:uppercase;
                        letter-spacing:0.5px;">{titulo}</div>
            <div style="font-size:24px; font-weight:700; color:{BRANCO}; margin-top:4px;">
                {valor}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _placeholder_aba(nome_completo: str, cards_exemplo: list, grafico_exemplo: tuple):
    st.caption(f"🚧 {nome_completo} — layout de exemplo, ainda vamos definir os dados reais de cada quadradinho.")

    linha1 = st.columns(4)
    for col, (titulo, valor, _var) in zip(linha1, cards_exemplo):
        with col:
            _card_simples(titulo, valor)

    st.markdown("<br>", unsafe_allow_html=True)
    col_graf, col_cards2 = st.columns([1, 1])
    with col_graf:
        _grafico_pizza(*grafico_exemplo)
    with col_cards2:
        sub = st.columns(2)
        for i in range(4):
            with sub[i % 2]:
                _card_simples(f"Indicador {i + 1}", "—")
                st.markdown("<br>", unsafe_allow_html=True)


def render_dashboard(sb):
    aba_dre, aba_drc, aba_drd, aba_drjc, aba_drr = st.tabs(["DRE", "DRC", "DRD", "DRJC", "DRR"])

    with aba_dre:
        render_dre()

    with aba_drc:
        _placeholder_aba(
            "Demonstrativo de Resultado de Clientes",
            [("Clientes Ativos", "—", None), ("Receita por Cliente", "R$ —", None),
             ("Ticket Médio", "R$ —", None), ("Top Cliente", "—", None)],
            ("Receita por Cliente", ["Cliente A", "Cliente B", "Cliente C", "Outros"], [1, 1, 1, 1]),
        )

    with aba_drd:
        _placeholder_aba(
            "Demonstrativo de Resultado de Despesas e Custos",
            [("Despesas Totais", "R$ —", None), ("Custos Fixos", "R$ —", None),
             ("Custos Variáveis", "R$ —", None), ("Orçado x Realizado", "—%", None)],
            ("Despesas por Categoria", ["Pessoal", "Ocupação", "Marketing", "Outros"], [1, 1, 1, 1]),
        )

    with aba_drjc:
        _placeholder_aba(
            "Demonstrativo de Resultados Jurídicos e Contábil",
            [("Processos Ativos", "—", None), ("Valor em Risco", "R$ —", None),
             ("Impostos Pagos", "R$ —", None), ("Obrigações Pendentes", "—", None)],
            ("Processos por Tipo", ["Trabalhista", "Cível", "Fiscal", "Outros"], [1, 1, 1, 1]),
        )

    with aba_drr:
        _placeholder_aba(
            "Demonstrativo de Resultados de Remuneração",
            [("Folha Total", "R$ —", None), ("Pró-Labore", "R$ —", None),
             ("Comissões", "R$ —", None), ("Encargos", "R$ —", None)],
            ("Remuneração por Tipo", ["Salários", "Pró-Labore", "Comissões", "Encargos"], [1, 1, 1, 1]),
        )
