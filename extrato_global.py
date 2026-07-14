"""
Extrato Financeiro Global - Mini ERP PagoExpress
Consolida BB PIX (1160-6), BB ADM (1547-4) e Asaas por dia, mostrando
saldo do dia e saldo global.

FASE 1 (esta versão): calcula saldo diário e saldo global usando só
Data + Valor de cada tabela -- funciona com os dados que já existem
hoje, sem precisar de nenhuma tabela nova no Supabase.

FASE 2 (próximo passo): quando a coluna "categoria" existir em bb_pix,
bb_adm e a Asaas estiver com o extrato classificado, é só preencher
"coluna_categoria" na config de cada conta (já preparado) que a quebra
por categoria (CASH-IN, TARIFA, etc.) aparece automaticamente.
"""

import pandas as pd
import streamlit as st
from supabase import create_client

# ---------------------------------------------------------------
# Configuração das contas -- cada tabela tem seus próprios nomes de
# coluna e formato de data.
# ---------------------------------------------------------------
CONTAS = [
    {
        "nome": "Banco do Brasil - Conta PIX (1160-6)",
        "tabela": "bb_pix",
        "coluna_data": "Data",
        "formato_data": "%d/%m/%Y",
        "coluna_valor": "Valor R$",
        "coluna_categoria": "categoria",
    },
    {
        "nome": "Banco do Brasil - Conta ADM (1547-4)",
        "tabela": "bb_adm",
        "coluna_data": "Data",
        "formato_data": "%d/%m/%Y",
        "coluna_valor": "Valor R$",
        "coluna_categoria": "categoria",
    },
    {
        "nome": "Asaas (PIX)",
        "tabela": "pix_asaas",
        "coluna_data": "Data",
        "formato_data": "%Y-%m-%d",
        "coluna_valor": "Valor",
        "coluna_categoria": "categoria",
    },
]


@st.cache_resource
def get_supabase():
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])


def buscar_extrato_conta(_sb, conta: dict) -> pd.DataFrame:
    """Busca todos os lançamentos de uma conta. Datas ficam como Timestamp
    (não como datetime.date) para evitar bug de comparação do pandas."""
    col_data = conta["coluna_data"]
    col_valor = conta["coluna_valor"]

    resp = _sb.table(conta["tabela"]).select("*").execute()
    df = pd.DataFrame(resp.data)
    if df.empty or col_data not in df.columns or col_valor not in df.columns:
        return pd.DataFrame(columns=[col_data, col_valor])

    df[col_data] = pd.to_datetime(df[col_data], format=conta["formato_data"], errors="coerce")
    df[col_valor] = pd.to_numeric(df[col_valor], errors="coerce").fillna(0.0)
    df = df.dropna(subset=[col_data])

    # remove linhas de saldo/controle que não são movimento real
    if "Historico" in df.columns:
        df = df[~df["Historico"].astype(str).str.strip().str.upper().isin(["SALDO ANTERIOR", "S A L D O"])]

    return df


def montar_extrato(_sb, data_ini, data_fim):
    data_ini_ts = pd.Timestamp(data_ini)
    data_fim_ts = pd.Timestamp(data_fim)
    dias = pd.date_range(data_ini_ts, data_fim_ts, freq="D")

    resultado = {}
    saldo_global = None

    for conta in CONTAS:
        col_data = conta["coluna_data"]
        col_valor = conta["coluna_valor"]
        col_cat = conta["coluna_categoria"]

        df = buscar_extrato_conta(_sb, conta)

        # saldo anterior = soma de tudo antes do período (automático,
        # sem precisar cadastrar saldo inicial em lugar nenhum)
        saldo_ini = df.loc[df[col_data] < data_ini_ts, col_valor].sum() if not df.empty else 0.0

        movimento_dia = (
            df[(df[col_data] >= data_ini_ts) & (df[col_data] <= data_fim_ts)]
            .groupby(col_data)[col_valor]
            .sum()
            if not df.empty
            else pd.Series(dtype=float)
        )

        saldo_dia = []
        saldo_corrente = saldo_ini
        for d in dias:
            saldo_corrente += movimento_dia.get(d, 0.0)
            saldo_dia.append(saldo_corrente)

        serie_saldo = pd.Series(saldo_dia, index=dias, name=conta["nome"])
        resultado[conta["nome"]] = serie_saldo

        # quebra por categoria, só se a coluna já existir nessa tabela
        if col_cat in df.columns and not df.empty:
            resultado[conta["nome"] + "__categorias"] = (
                df[(df[col_data] >= data_ini_ts) & (df[col_data] <= data_fim_ts)]
                .groupby([col_cat, col_data])[col_valor]
                .sum()
                .unstack(col_data)
                .reindex(columns=dias, fill_value=0.0)
            )

        saldo_global = serie_saldo if saldo_global is None else saldo_global.add(serie_saldo, fill_value=0.0)

    return resultado, saldo_global


def _formatar_colunas_data(df_ou_serie):
    """Troca o índice de Timestamp por texto dd/mm só na hora de exibir."""
    novo = df_ou_serie.copy()
    novo.columns = [c.strftime("%d/%m") if hasattr(c, "strftime") else c for c in novo.columns]
    return novo


def render_extrato_global():
    st.subheader("Extrato financeiro global")

    col1, col2 = st.columns(2)
    data_ini = col1.date_input("De")
    data_fim = col2.date_input("Até")

    if data_ini > data_fim:
        st.error("Data inicial maior que a data final.")
        return

    sb = get_supabase()
    resultado, saldo_global = montar_extrato(sb, data_ini, data_fim)

    st.markdown("**Saldo global**")
    tabela_global = saldo_global.to_frame("SALDO GLOBAL").T
    st.dataframe(_formatar_colunas_data(tabela_global).style.format("R$ {:,.2f}"), use_container_width=True)

    for conta in CONTAS:
        st.markdown(f"**{conta['nome']}**")
        tabela_conta = resultado[conta["nome"]].to_frame("SALDO DO DIA").T
        st.dataframe(_formatar_colunas_data(tabela_conta).style.format("R$ {:,.2f}"), use_container_width=True)

        chave_cat = conta["nome"] + "__categorias"
        if chave_cat in resultado:
            with st.expander("Ver por categoria"):
                st.dataframe(
                    _formatar_colunas_data(resultado[chave_cat]).style.format("R$ {:,.2f}"),
                    use_container_width=True,
                )
        else:
            st.caption("Quebra por categoria ainda não disponível para esta conta.")


CAMPOS_POSICAO = [
    ("AMOUNT", "Amount"),
    ("FEE_OU_COMMISSION", "Fee/Commission"),
    ("TARIFA", "Tarifa"),
    ("IMPOSTOS", "Impostos"),
    ("COMISSÃO PLATAFORMA", "Comissão Plataforma"),
    ("COMISSÃO COMERCIAL", "Comissão Comercial"),
    ("LUCRO FINAL", "Lucro Final"),
]


def _montar_tabela_posicao(_sb, tabela: str, coluna_valor_base: str) -> pd.DataFrame:
    resp = _sb.table(tabela).select("*").eq("STATUS", "PROCESSED").execute()
    df = pd.DataFrame(resp.data)
    if df.empty or "MERCHANT NAME" not in df.columns:
        return pd.DataFrame()

    df = df.rename(columns={coluna_valor_base: "FEE_OU_COMMISSION"})
    for col, _ in CAMPOS_POSICAO:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    agrupado = df.groupby("MERCHANT NAME").agg(
        Qtd=("MERCHANT NAME", "count"),
        **{col: (col, "sum") for col, _ in CAMPOS_POSICAO if col in df.columns},
    )

    total = agrupado.sum(numeric_only=True)
    total.name = "Total geral"
    agrupado = pd.concat([agrupado, total.to_frame().T])

    agrupado = agrupado.rename(columns=dict(CAMPOS_POSICAO))
    agrupado.index.name = "Cliente"
    return agrupado


def render_posicao_clientes():
    sb = get_supabase()

    st.markdown("**Cash-in**")
    tabela_in = _montar_tabela_posicao(sb, "cashin", "FEE")
    if tabela_in.empty:
        st.caption("Nenhuma transação PROCESSED encontrada em cashin.")
    else:
        st.dataframe(tabela_in.style.format("{:,.2f}"), use_container_width=True)

    st.markdown("**Cash-out**")
    tabela_out = _montar_tabela_posicao(sb, "cashout", "COMMISSION")
    if tabela_out.empty:
        st.caption("Nenhuma transação PROCESSED encontrada em cashout.")
    else:
        st.dataframe(tabela_out.style.format("{:,.2f}"), use_container_width=True)


def render_transacoes_x_bancos():
    st.info("🚧 Em desenvolvimento — Comparação diária das transações internas/BaaS contra os bancos.")


def render_tela_ajustes():
    """Ponto de entrada da Tela de Ajustes -- organiza as sub-telas em abas."""
    aba_extrato, aba_clientes, aba_bancos, aba_futuro = st.tabs(
        ["Extrato financeiro", "Posição de clientes", "Transações x bancos", "+ Em breve"]
    )

    with aba_extrato:
        render_extrato_global()

    with aba_clientes:
        render_posicao_clientes()

    with aba_bancos:
        render_transacoes_x_bancos()

    with aba_futuro:
        st.caption("Espaço reservado para as próximas telas.")


if __name__ == "__main__":
    render_extrato_global()
