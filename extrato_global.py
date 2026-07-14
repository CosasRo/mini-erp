"""
Extrato Financeiro Global - Mini ERP PagoExpress
Consolida BB PIX (1160-6), BB ADM (1547-4) e Asaas por dia, mostrando
saldo do dia e saldo global.

FASE 1 (esta versão): calcula saldo diário e saldo global usando só
Data + Valor R$ de cada tabela -- funciona com os dados que já existem
hoje, sem precisar de nenhuma tabela nova no Supabase.

FASE 2 (próximo passo): quando a coluna "categoria" existir em bb_pix,
bb_adm e a Asaas estiver com o extrato classificado, é só preencher
COLUNA_CATEGORIA abaixo que a quebra por categoria (CASH-IN, TARIFA,
etc.) aparece automaticamente, igual na planilha.
"""

import pandas as pd
import streamlit as st
from supabase import create_client

# ---------------------------------------------------------------
# Configuração das contas -- cada tabela tem seus próprios nomes de
# coluna e formato de data, então cada conta traz sua própria config.
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


def _parse_data(serie: pd.Series, formato: str) -> pd.Series:
    return pd.to_datetime(serie, format=formato, errors="coerce").dt.date


def buscar_extrato_conta(_sb, conta: dict, data_ini, data_fim) -> pd.DataFrame:
    """Busca todos os lançamentos de uma conta (sem filtro de data no
    Supabase, porque a coluna Data é texto -- filtramos em pandas)."""
    col_data = conta["coluna_data"]
    col_valor = conta["coluna_valor"]

    resp = _sb.table(conta["tabela"]).select("*").execute()
    df = pd.DataFrame(resp.data)
    if df.empty or col_data not in df.columns or col_valor not in df.columns:
        return pd.DataFrame(columns=[col_data, col_valor])

    df[col_data] = _parse_data(df[col_data], conta["formato_data"])
    df[col_valor] = pd.to_numeric(df[col_valor], errors="coerce").fillna(0.0)

    # remove linhas de saldo/controle que não são movimento real
    if "Historico" in df.columns:
        df = df[~df["Historico"].astype(str).str.strip().str.upper().isin(["SALDO ANTERIOR", "S A L D O"])]

    return df


def montar_extrato(_sb, data_ini, data_fim):
    resultado = {}
    saldo_global = None
    dias = pd.date_range(data_ini, data_fim, freq="D").date

    for conta in CONTAS:
        col_data = conta["coluna_data"]
        col_valor = conta["coluna_valor"]
        col_cat = conta["coluna_categoria"]

        df = buscar_extrato_conta(_sb, conta, data_ini, data_fim)

        # saldo anterior = soma de tudo antes do período (auto, sem precisar
        # cadastrar saldo inicial em lugar nenhum)
        saldo_ini = df.loc[df[col_data] < data_ini, col_valor].sum() if not df.empty else 0.0

        movimento_dia = (
            df[(df[col_data] >= data_ini) & (df[col_data] <= data_fim)]
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
                df[(df[col_data] >= data_ini) & (df[col_data] <= data_fim)]
                .groupby([col_cat, col_data])[col_valor]
                .sum()
                .unstack(col_data)
                .reindex(columns=dias, fill_value=0.0)
            )

        saldo_global = serie_saldo if saldo_global is None else saldo_global.add(serie_saldo, fill_value=0.0)

    return resultado, saldo_global


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
    st.dataframe(saldo_global.to_frame("SALDO GLOBAL").T.style.format("R$ {:,.2f}"), use_container_width=True)

    for conta in CONTAS:
        st.markdown(f"**{conta['nome']}**")
        st.dataframe(
            resultado[conta["nome"]].to_frame("SALDO DO DIA").T.style.format("R$ {:,.2f}"),
            use_container_width=True,
        )
        chave_cat = conta["nome"] + "__categorias"
        if chave_cat in resultado:
            with st.expander("Ver por categoria"):
                st.dataframe(resultado[chave_cat].style.format("R$ {:,.2f}"), use_container_width=True)
        else:
            st.caption("Quebra por categoria ainda não disponível para esta conta.")


if __name__ == "__main__":
    render_extrato_global()
