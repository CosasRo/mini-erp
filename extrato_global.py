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


CAMPOS_CASHIN = [
    ("AMOUNT", "Amount"),
    ("FEE", "Fee"),
    ("NET VALUE", "Net Value"),
    ("SPLIT", "Split"),
    ("TARIFA", "Tarifa"),
    ("IMPOSTOS", "Impostos"),
    ("COMISSÃO PLATAFORMA", "Comissão Plataforma"),
    ("LUCRO INTERMEDIÁRIO", "Lucro Intermediário"),
    ("COMISSÃO COMERCIAL", "Comissão Comercial"),
    ("LUCRO FINAL", "Lucro Final"),
]

CAMPOS_CASHOUT = [
    ("AMOUNT", "Amount"),
    ("COMMISSION", "Commission"),
    ("NET VALUE", "Net Value"),
    ("PIX VALUE", "Pix Value"),
    ("TARIFA", "Tarifa"),
    ("IMPOSTOS", "Impostos"),
    ("COMISSÃO PLATAFORMA", "Comissão Plataforma"),
    ("LUCRO INTERMEDIÁRIO", "Lucro Intermediário"),
    ("COMISSÃO COMERCIAL", "Comissão Comercial"),
    ("LUCRO FINAL", "Lucro Final"),
]


def _montar_tabela_posicao(_sb, tabela: str, status_alvo: str, campos: list) -> pd.DataFrame:
    resp = _sb.table(tabela).select("*").eq("STATUS", status_alvo).execute()
    df = pd.DataFrame(resp.data)
    if df.empty or "MERCHANT NAME" not in df.columns:
        return pd.DataFrame()

    for col, _ in campos:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    agrupado = df.groupby("MERCHANT NAME").agg(
        Qtd=("MERCHANT NAME", "count"),
        **{col: (col, "sum") for col, _ in campos if col in df.columns},
    )
    agrupado["Qtd"] = agrupado["Qtd"].astype(int)

    total = agrupado.sum(numeric_only=True)
    total["Qtd"] = int(total["Qtd"])
    total.name = "Total geral"
    agrupado = pd.concat([agrupado, total.to_frame().T])

    agrupado = agrupado.rename(columns=dict(campos))
    agrupado.index.name = "Cliente"
    return agrupado


def _formatos_posicao(df: pd.DataFrame) -> dict:
    formatos = {"Qtd": "{:.0f}"}
    for col in df.columns:
        if col != "Qtd":
            formatos[col] = "{:,.2f}"
    return formatos


def render_posicao_clientes():
    sb = get_supabase()

    st.markdown("**Cash-in**")
    tabela_in = _montar_tabela_posicao(sb, "cashin", "PROCESSED", CAMPOS_CASHIN)
    if tabela_in.empty:
        st.caption("Nenhuma transação PROCESSED encontrada em cashin.")
    else:
        st.dataframe(tabela_in.style.format(_formatos_posicao(tabela_in)), use_container_width=True)

    st.markdown("**Cash-out**")
    tabela_out = _montar_tabela_posicao(sb, "cashout", "SUCCESSFULLY PROCESSED", CAMPOS_CASHOUT)
    if tabela_out.empty:
        st.caption("Nenhuma transação SUCCESSFULLY PROCESSED encontrada em cashout.")
    else:
        st.dataframe(tabela_out.style.format(_formatos_posicao(tabela_out)), use_container_width=True)


def _carregar_interno(_sb, tabela: str, status_alvo: str, coluna_data: str) -> pd.DataFrame:
    resp = _sb.table(tabela).select("*").eq("STATUS", status_alvo).execute()
    df = pd.DataFrame(resp.data)
    if df.empty:
        return df

    df["data"] = pd.to_datetime(df[coluna_data], errors="coerce").dt.normalize()
    df["AMOUNT"] = pd.to_numeric(df["AMOUNT"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["data"])

    bank = df.get("BANK NAME", "").astype(str).str.upper()
    baas_bolsao = df.get("BAAS/BOLSÃO", "").astype(str).str.upper()

    df["_eh_bb"] = bank.str.contains("BRASIL") | bank.str.contains("BB")
    df["_eh_asaas"] = bank.str.contains("ASAAS")
    df["_eh_bolsao"] = df["_eh_asaas"] & (baas_bolsao == "BOLSÃO")
    df["_eh_baas"] = df["_eh_asaas"] & (baas_bolsao == "BAAS")

    return df


def _carregar_pix_asaas_classificado(_sb) -> pd.DataFrame:
    """pix_asaas é sempre Bolsão -- BAAS vem de outro extrato (extrato_pix),
    que por enquanto não entra nessa comparação."""
    resp = _sb.table("pix_asaas").select("*").execute()
    pix = pd.DataFrame(resp.data)
    if pix.empty or "Data" not in pix.columns or "Valor" not in pix.columns:
        return pd.DataFrame()

    pix["data"] = pd.to_datetime(pix["Data"], format="%Y-%m-%d", errors="coerce").dt.normalize()
    pix["Valor"] = pd.to_numeric(pix["Valor"], errors="coerce").fillna(0.0)
    pix["_eh_bolsao"] = True
    pix["_eh_baas"] = False

    return pix.dropna(subset=["data"])


def _qtd_soma_por_dia(df: pd.DataFrame, mascara, col_valor: str, dias) -> pd.DataFrame:
    if df.empty:
        base = pd.DataFrame({"data": dias})
        base["qtd"] = 0
        base["soma"] = 0.0
        return base

    filtrado = df[mascara] if mascara is not None else df
    agrupado = filtrado.groupby("data").agg(qtd=(col_valor, "count"), soma=(col_valor, "sum")).reset_index()
    base = pd.DataFrame({"data": dias}).merge(agrupado, on="data", how="left").fillna(0.0)
    base["qtd"] = base["qtd"].astype(int)
    return base


def _montar_cashin(_sb, data_ini, data_fim) -> pd.DataFrame:
    dias = pd.date_range(pd.Timestamp(data_ini), pd.Timestamp(data_fim), freq="D")

    interno = _carregar_interno(_sb, "cashin", "PROCESSED", "PAYMENT TIME")
    pix = _carregar_pix_asaas_classificado(_sb)

    bb_int = _qtd_soma_por_dia(interno, interno["_eh_bb"] if not interno.empty else None, "AMOUNT", dias)
    bolsao_int = _qtd_soma_por_dia(interno, interno["_eh_bolsao"] if not interno.empty else None, "AMOUNT", dias)
    baas_int = _qtd_soma_por_dia(interno, interno["_eh_baas"] if not interno.empty else None, "AMOUNT", dias)

    bolsao_bco = _qtd_soma_por_dia(pix, pix["_eh_bolsao"] if not pix.empty else None, "Valor", dias)
    baas_bco = _qtd_soma_por_dia(pix, pix["_eh_baas"] if not pix.empty else None, "Valor", dias)

    tabela = pd.DataFrame({"Data": dias})
    tabela["Qt Tx Geral"] = bb_int["qtd"] + bolsao_int["qtd"] + baas_int["qtd"]
    tabela["BB Qtd (sistema)"] = bb_int["qtd"]
    tabela["BB Soma (sistema)"] = bb_int["soma"]
    tabela["Asaas Bolsão Qtd (sistema)"] = bolsao_int["qtd"]
    tabela["Asaas Bolsão Soma (sistema)"] = bolsao_int["soma"]
    tabela["Asaas BAAS Qtd (sistema)"] = baas_int["qtd"]
    tabela["Asaas BAAS Soma (sistema)"] = baas_int["soma"]
    tabela["BB Qtd (banco)"] = 0  # BB ainda não classificado -- fica 0 por enquanto
    tabela["BB Soma (banco)"] = 0.0
    tabela["Asaas Bolsão Qtd (banco)"] = bolsao_bco["qtd"]
    tabela["Asaas Bolsão Soma (banco)"] = bolsao_bco["soma"]
    tabela["Asaas BAAS Qtd (banco)"] = baas_bco["qtd"]
    tabela["Asaas BAAS Soma (banco)"] = baas_bco["soma"]
    tabela["Diferença Qtd"] = (
        tabela["Qt Tx Geral"] - tabela["BB Qtd (banco)"] - tabela["Asaas Bolsão Qtd (banco)"] - tabela["Asaas BAAS Qtd (banco)"]
    )
    tabela["Diferença Valor"] = (
        tabela["BB Soma (sistema)"] + tabela["Asaas Bolsão Soma (sistema)"] + tabela["Asaas BAAS Soma (sistema)"]
        - tabela["BB Soma (banco)"] - tabela["Asaas Bolsão Soma (banco)"] - tabela["Asaas BAAS Soma (banco)"]
    )

    tabela["_tem_dado"] = (
        tabela["Qt Tx Geral"] + tabela["BB Qtd (banco)"] + tabela["Asaas Bolsão Qtd (banco)"] + tabela["Asaas BAAS Qtd (banco)"]
    ) > 0

    tabela["Data"] = tabela["Data"].dt.strftime("%d/%m/%Y")
    return tabela.set_index("Data")


def _montar_cashout(_sb, data_ini, data_fim) -> pd.DataFrame:
    dias = pd.date_range(pd.Timestamp(data_ini), pd.Timestamp(data_fim), freq="D")

    interno = _carregar_interno(_sb, "cashout", "SUCCESSFULLY PROCESSED", "CREATION TIME")
    pix = _carregar_pix_asaas_classificado(_sb)

    bolsao_int = _qtd_soma_por_dia(interno, interno["_eh_bolsao"] if not interno.empty else None, "AMOUNT", dias)
    baas_int = _qtd_soma_por_dia(interno, interno["_eh_baas"] if not interno.empty else None, "AMOUNT", dias)

    bolsao_bco = _qtd_soma_por_dia(pix, pix["_eh_bolsao"] if not pix.empty else None, "Valor", dias)
    baas_bco = _qtd_soma_por_dia(pix, pix["_eh_baas"] if not pix.empty else None, "Valor", dias)

    tabela = pd.DataFrame({"Data": dias})
    tabela["Qt Tx Geral"] = bolsao_int["qtd"] + baas_int["qtd"]
    tabela["Asaas Bolsão Qtd (sistema)"] = bolsao_int["qtd"]
    tabela["Asaas Bolsão Soma (sistema)"] = bolsao_int["soma"]
    tabela["Asaas BAAS Qtd (sistema)"] = baas_int["qtd"]
    tabela["Asaas BAAS Soma (sistema)"] = baas_int["soma"]
    tabela["Asaas Bolsão Qtd (banco)"] = bolsao_bco["qtd"]
    tabela["Asaas Bolsão Soma (banco)"] = bolsao_bco["soma"]
    tabela["Asaas BAAS Qtd (banco)"] = baas_bco["qtd"]
    tabela["Asaas BAAS Soma (banco)"] = baas_bco["soma"]
    tabela["Diferença Qtd"] = tabela["Qt Tx Geral"] - tabela["Asaas Bolsão Qtd (banco)"] - tabela["Asaas BAAS Qtd (banco)"]
    tabela["Diferença Valor"] = (
        tabela["Asaas Bolsão Soma (sistema)"] + tabela["Asaas BAAS Soma (sistema)"]
        - tabela["Asaas Bolsão Soma (banco)"] - tabela["Asaas BAAS Soma (banco)"]
    )

    tabela["_tem_dado"] = (
        tabela["Qt Tx Geral"] + tabela["Asaas Bolsão Qtd (banco)"] + tabela["Asaas BAAS Qtd (banco)"]
    ) > 0

    tabela["Data"] = tabela["Data"].dt.strftime("%d/%m/%Y")
    return tabela.set_index("Data")


def _agrupar_colunas(tabela: pd.DataFrame, grupos: dict) -> pd.DataFrame:
    """grupos: {nome_coluna: nome_do_grupo} -- monta cabeçalho de duas linhas."""
    tabela = tabela.copy()
    tabela.columns = pd.MultiIndex.from_tuples([(grupos.get(c, ""), c) for c in tabela.columns])
    return tabela


def _cor_diferenca(serie_valores: pd.Series, tem_dado: pd.Series):
    """Verde = bateu (diferença 0), Azul = sem dado nenhum dos dois lados,
    Vermelho = diferença de verdade."""
    cores = []
    for dif, dado in zip(serie_valores, tem_dado):
        if not dado:
            cores.append("background-color: #1d3a5f; color: #9cc3f2")  # azul -- sem dados
        elif abs(dif) < 0.005:
            cores.append("background-color: #1d4a2e; color: #8fd6a5")  # verde -- bateu
        else:
            cores.append("background-color: #4a1d1d; color: #f2a5a5")  # vermelho -- divergência
    return cores


def _aplicar_estilo(tabela: pd.DataFrame, tem_dado: pd.Series, formatos: dict):
    formatos_tuplas = {(g, c): formatos[c] for g, c in tabela.columns if c in formatos}
    estilo = tabela.style.format(formatos_tuplas)
    for col_grupo, col_nome in tabela.columns:
        if col_nome in ("Diferença Qtd", "Diferença Valor"):
            estilo = estilo.apply(lambda s: _cor_diferenca(s, tem_dado), subset=[(col_grupo, col_nome)])
    return estilo


def render_transacoes_x_bancos():
    st.caption("BAAS ainda não é comparado de verdade contra o banco (fica pra uma próxima fase) — por enquanto só mostra a diferença.")

    sb = get_supabase()

    col1, col2 = st.columns(2)
    data_ini = col1.date_input("De", key="txb_ini")
    data_fim = col2.date_input("Até", key="txb_fim")
    if data_ini > data_fim:
        st.error("Data inicial maior que a data final.")
        return

    formatos_num = lambda cols: {
        c: ("{:.0f}" if "Qtd" in c or c == "Qt Tx Geral" else "{:,.2f}") for c in cols
    }

    grupos_cashin = {
        "Qt Tx Geral": "Geral",
        "BB Qtd (sistema)": "Sistema", "BB Soma (sistema)": "Sistema",
        "Asaas Bolsão Qtd (sistema)": "Sistema", "Asaas Bolsão Soma (sistema)": "Sistema",
        "Asaas BAAS Qtd (sistema)": "Sistema", "Asaas BAAS Soma (sistema)": "Sistema",
        "BB Qtd (banco)": "Banco", "BB Soma (banco)": "Banco",
        "Asaas Bolsão Qtd (banco)": "Banco", "Asaas Bolsão Soma (banco)": "Banco",
        "Asaas BAAS Qtd (banco)": "Banco", "Asaas BAAS Soma (banco)": "Banco",
        "Diferença Qtd": "Diferença", "Diferença Valor": "Diferença",
    }
    grupos_cashout = {
        "Qt Tx Geral": "Geral",
        "Asaas Bolsão Qtd (sistema)": "Sistema", "Asaas Bolsão Soma (sistema)": "Sistema",
        "Asaas BAAS Qtd (sistema)": "Sistema", "Asaas BAAS Soma (sistema)": "Sistema",
        "Asaas Bolsão Qtd (banco)": "Banco", "Asaas Bolsão Soma (banco)": "Banco",
        "Asaas BAAS Qtd (banco)": "Banco", "Asaas BAAS Soma (banco)": "Banco",
        "Diferença Qtd": "Diferença", "Diferença Valor": "Diferença",
    }

    st.markdown("**Cash-in**")
    tab_in = _montar_cashin(sb, data_ini, data_fim)
    tem_dado_in = tab_in.pop("_tem_dado")
    formatos_in = formatos_num(tab_in.columns)
    tab_in = _agrupar_colunas(tab_in, grupos_cashin)
    st.dataframe(_aplicar_estilo(tab_in, tem_dado_in, formatos_in), use_container_width=True)

    st.markdown("**Cash-out**")
    tab_out = _montar_cashout(sb, data_ini, data_fim)
    tem_dado_out = tab_out.pop("_tem_dado")
    formatos_out = formatos_num(tab_out.columns)
    tab_out = _agrupar_colunas(tab_out, grupos_cashout)
    st.dataframe(_aplicar_estilo(tab_out, tem_dado_out, formatos_out), use_container_width=True)


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
