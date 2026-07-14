"""
Dashboard - Mini ERP PagoExpress
5 abas, uma por segmento: DRE, DRC, DRD, DRJC, DRR.
Layout em cartões pequenos (KPIs) + gráfico de pizza, no padrão de
cores da empresa. Por enquanto são placeholders -- ainda vamos definir
juntos quais dados entram em cada quadradinho.
"""

import streamlit as st
import plotly.graph_objects as go

ROXO_ESCURO = "#3A2D58"
ROXO_MEDIO = "#594A92"
AMARELO = "#ECBD42"
CINZA_CLARO = "#C9C7C1"
BRANCO = "#FFFFFF"

PALETA_GRAFICO = [ROXO_ESCURO, ROXO_MEDIO, AMARELO, CINZA_CLARO]


def _card(titulo: str, valor: str, variacao: str = None, cor_valor: str = BRANCO):
    variacao_html = (
        f'<div style="font-size:12px; color:{AMARELO}; margin-top:2px;">{variacao}</div>'
        if variacao else ""
    )
    st.markdown(
        f"""
        <div style="background:{ROXO_MEDIO}; border-radius:10px; padding:14px 16px;
                    height:100%; box-shadow: 0 1px 3px rgba(0,0,0,0.2);">
            <div style="font-size:12px; color:{CINZA_CLARO}; text-transform:uppercase;
                        letter-spacing:0.5px;">{titulo}</div>
            <div style="font-size:24px; font-weight:700; color:{cor_valor}; margin-top:4px;">
                {valor}
            </div>
            {variacao_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


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


def _placeholder_aba(nome_completo: str, cards_exemplo: list, grafico_exemplo: tuple):
    st.caption(f"🚧 {nome_completo} — layout de exemplo, ainda vamos definir os dados reais de cada quadradinho.")

    linha1 = st.columns(4)
    for col, (titulo, valor, var) in zip(linha1, cards_exemplo):
        with col:
            _card(titulo, valor, var)

    st.markdown("<br>", unsafe_allow_html=True)
    col_graf, col_cards2 = st.columns([1, 1])
    with col_graf:
        _grafico_pizza(*grafico_exemplo)
    with col_cards2:
        sub = st.columns(2)
        for i in range(4):
            with sub[i % 2]:
                _card(f"Indicador {i + 1}", "—")
                st.markdown("<br>", unsafe_allow_html=True)


def render_dashboard(sb):
    aba_dre, aba_drc, aba_drd, aba_drjc, aba_drr = st.tabs(["DRE", "DRC", "DRD", "DRJC", "DRR"])

    with aba_dre:
        _placeholder_aba(
            "Demonstrativo do Resultado do Exercício",
            [("Receita Bruta", "R$ —", None), ("Despesas", "R$ —", None),
             ("Resultado Líquido", "R$ —", None), ("Margem", "—%", None)],
            ("Composição do Resultado", ["Receitas", "Custos", "Despesas", "Impostos"], [1, 1, 1, 1]),
        )

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
