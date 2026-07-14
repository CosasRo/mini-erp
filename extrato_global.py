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
# Configuração das contas -- ajuste aqui se mudar nome de tabela/coluna,
# não precisa mexer no resto do arquivo.
# ---------------------------------------------------------------
CONTAS = [
    {"nome": "Banco do Brasil - Conta PIX (1160-6)", "tabela": "bb_pix"},
    {"nome": "Banco do Brasil - Conta ADM (1547-4)", "tabela": "bb_adm"},
    {"nome": "Asaas (PIX)", "tabela": "pix_asaas"},
]
COLUNA_DATA = "Data"
COLUNA_VALOR = "Valor R$"
COLUNA_CATEGORIA = "categoria"  # ainda não existe em todas as tabelas -- ok, é opcional


@st.cache_resource
def get_supabase():
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])


def _parse_data(serie: pd.Series) -> pd.Series:
    # a coluna Data vem como texto "dd/mm/aaaa" no bb_pix/bb_adm
    return pd.to_datetime(serie, format="%d/%m/%Y", errors="coerce").dt.date


def buscar_extrato_conta(_sb, tabela: str, data_ini, data_fim) -> pd.DataFrame:
    """Busca todos os lançamentos de uma conta (sem filtro de data no
    Supabase, porque a coluna Data é texto -- filtramos em pandas)."""
    resp = _sb.table(tabela).select("*").execute()
    df = pd.DataFrame(resp.data)
    if df.empty:
        return pd.DataFrame(columns=[COLUNA_DATA, COLUNA_VALOR])

    df[COLUNA_DATA] = _parse_data(df[COLUNA_DATA])
    df[COLUNA_VALOR] = pd.to_numeric(df[COLUNA_VALOR], errors="coerce").fillna(0.0)

    # remove linhas de saldo/controle que não são movimento real
    if "Historico" in df.columns:
        df = df[~df["Historico"].str.strip().str.upper().isin(["SALDO ANTERIOR", "S A L D O"])]

    return df


def montar_extrato(_sb, data_ini, data_fim):
    resultado = {}
    saldo_global = None

    for conta in CONTAS:
        df = buscar_extrato_conta(_sb, conta["tabela"], data_ini, data_fim)

        dias = pd.date_range(data_ini, data_fim, freq="D").date

        # saldo anterior = soma de tudo antes do período (auto, sem precisar
        # cadastrar saldo inicial em lugar nenhum)
        saldo_ini = df.loc[df[COLUNA_DATA] < data_ini, COLUNA_VALOR].sum() if not df.empty else 0.0

        movimento_dia = (
            df[(df[COLUNA_DATA] >= data_ini) & (df[COLUNA_DATA] <= data_fim)]
            .groupby(COLUNA_DATA)[COLUNA_VALOR]
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
        if COLUNA_CATEGORIA in df.columns and not df.empty:
            resultado[conta["nome"] + "__categorias"] = (
                df[(df[COLUNA_DATA] >= data_ini) & (df[COLUNA_DATA] <= data_fim)]
                .groupby([COLUNA_CATEGORIA, COLUNA_DATA])[COLUNA_VALOR]
                .sum()
                .unstack(COLUNA_DATA)
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
