"""
Orçado x Realizado - Mini ERP PagoExpress
Duas abas: comparação mensal Orçado x Realizado por Centro de Custo, e
a lista de Compromissos Diários que alimenta o Realizado.
"""

import pandas as pd
import streamlit as st
from supabase import create_client


@st.cache_resource
def get_supabase():
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])


def _primeiro_dia_mes(data) -> str:
    return pd.Timestamp(data).replace(day=1).strftime("%Y-%m-%d")


def _carregar_orcado(_sb, mes: str) -> pd.DataFrame:
    resp = _sb.table("orcamento").select("centro_custo,valor_orcado").eq("mes", mes).execute()
    df = pd.DataFrame(resp.data)
    if df.empty:
        return pd.DataFrame(columns=["centro_custo", "valor_orcado"])
    df["valor_orcado"] = pd.to_numeric(df["valor_orcado"], errors="coerce").fillna(0.0)
    return df


def _carregar_realizado(_sb, mes: str) -> pd.DataFrame:
    resp = _sb.table("compromissos_diarios").select("centro_custo,valor").eq("mes", mes).execute()
    df = pd.DataFrame(resp.data)
    if df.empty:
        return pd.DataFrame(columns=["centro_custo", "valor"])
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)
    return df.groupby("centro_custo", as_index=False)["valor"].sum()


def render_orcado_realizado():
    sb = get_supabase()

    mes_ref = st.date_input("Mês de referência", value=pd.Timestamp.today().replace(day=1))
    mes = _primeiro_dia_mes(mes_ref)

    orcado = _carregar_orcado(sb, mes)
    realizado = _carregar_realizado(sb, mes)

    if orcado.empty and realizado.empty:
        st.info("Nenhum dado de orçamento ou compromissos para este mês ainda.")
        return

    tabela = pd.merge(
        orcado.rename(columns={"valor_orcado": "Orçado"}),
        realizado.rename(columns={"valor": "Realizado"}),
        on="centro_custo",
        how="outer",
    ).fillna(0.0)

    tabela["%"] = tabela.apply(lambda r: (r["Realizado"] / r["Orçado"]) if r["Orçado"] else 0.0, axis=1)
    tabela["Diferença"] = tabela["Orçado"] - tabela["Realizado"]
    tabela = tabela.rename(columns={"centro_custo": "Centro de Custo"}).sort_values("Centro de Custo")

    total = tabela[["Orçado", "Realizado", "Diferença"]].sum()
    total["Centro de Custo"] = "Total geral"
    total["%"] = (total["Realizado"] / total["Orçado"]) if total["Orçado"] else 0.0
    tabela = pd.concat([tabela, total.to_frame().T], ignore_index=True)

    tabela = tabela.set_index("Centro de Custo")[["Orçado", "%", "Realizado", "Diferença"]]

    def _cor_diferenca(col):
        return [
            "color: #f2a5a5" if v < 0 else ("color: #8fd6a5" if v > 0 else "")
            for v in col
        ]

    st.dataframe(
        tabela.style.format({"Orçado": "R$ {:,.2f}", "Realizado": "R$ {:,.2f}", "Diferença": "R$ {:,.2f}", "%": "{:.1%}"})
        .apply(_cor_diferenca, subset=["Diferença"]),
        use_container_width=True,
    )


def render_compromissos_diarios():
    sb = get_supabase()

    col1, col2 = st.columns(2)
    data_ini = col1.date_input("De", key="cd_ini", value=pd.Timestamp.today().replace(day=1))
    data_fim = col2.date_input("Até", key="cd_fim")

    resp = (
        sb.table("compromissos_diarios")
        .select("*")
        .gte("data_pagamento", str(data_ini))
        .lte("data_pagamento", str(data_fim))
        .order("data_pagamento")
        .execute()
    )
    df = pd.DataFrame(resp.data)

    if df.empty:
        st.info("Nenhum compromisso encontrado no período.")
        return

    centros = sorted(df["centro_custo"].dropna().unique().tolist())
    centro_sel = st.selectbox("Centro de Custo", ["Todos"] + centros)
    if centro_sel != "Todos":
        df = df[df["centro_custo"] == centro_sel]

    colunas = ["data_pagamento", "banco", "agente", "centro_custo", "valor", "impostos", "saldo", "historico"]
    colunas = [c for c in colunas if c in df.columns]
    st.dataframe(df[colunas], use_container_width=True)

    st.caption(f"{len(df)} lançamento(s) — soma: R$ {df['valor'].sum():,.2f}")


def render_orcamento():
    aba_orcado, aba_compromissos = st.tabs(["Orçado e Realizado", "Compromissos Diários"])

    with aba_orcado:
        render_orcado_realizado()

    with aba_compromissos:
        render_compromissos_diarios()


if __name__ == "__main__":
    render_orcamento()
