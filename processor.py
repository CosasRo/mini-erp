"""
Pago Express - Mini ERP
Processador de arquivos brutos
Parâmetros buscados do Supabase (Base Relat.)
"""

import pandas as pd
import streamlit as st
from supabase import create_client


# ============================================================
# CARREGA PARÂMETROS DO SUPABASE
# ============================================================

@st.cache_data(ttl=300)  # Cache de 5 minutos
def carregar_params(_sb):
    """
    Carrega todos os parâmetros da Base Relat. do Supabase.
    Cache de 5 minutos para evitar muitas chamadas ao banco.
    """
    params = {}

    # Bancos e tarifas
    bancos_data = _sb.table("bancos").select("*").eq("ativo", True).execute().data
    params["bancos"] = {
        b["nome_banco"].strip().upper(): {
            "cod_banco":  b["cod_banco"],
            "tarifa_in":  float(b["tarifa_in"] or 0),
            "tarifa_out": float(b["tarifa_out"] or 0),
        }
        for b in bancos_data
    }

    # Impostos — soma total das alíquotas ativas
    impostos_data = _sb.table("impostos").select("*").eq("ativo", True).execute().data
    params["impostos"] = {i["nome"]: float(i["aliquota"] or 0) for i in impostos_data}
    params["aliquota_total"] = sum(params["impostos"].values())

    # Comissionados Plataforma — lookup por PLATFORM NAME (nome do comercial)
    cp_data = _sb.table("comissionados_plataforma").select("*").eq("ativo", True).execute().data
    # Valor fixo por comercial (primeiro valor encontrado)
    valor_por_comercial = {}
    for r in cp_data:
        c = r["comercial"].strip().upper()
        if c not in valor_por_comercial:
            valor_por_comercial[c] = float(r["valor_fixo"] or 0)
    params["valor_por_comercial"] = valor_por_comercial

    # Comissionados Comercial — lookup por MERCHANT NAME
    cc_data = _sb.table("comissionados_comercial").select("*").eq("ativo", True).execute().data
    params["comissionados_comercial"] = {
        r["merchant"].strip().upper(): {
            "comercial":  r["comercial"],
            "percentual": float(r["percentual"] or 0),
        }
        for r in cc_data
    }

    # Lista BAAS
    baas_data = _sb.table("baas").select("merchant").eq("ativo", True).execute().data
    params["baas"] = {r["merchant"].strip().upper() for r in baas_data}

    return params


# ============================================================
# FUNÇÕES DE CÁLCULO
# ============================================================

def calcular_tarifa(banco_name, end_to_end, params, asaas_taxa_lookup=None):
    """
    Tarifa: calculada pela Base Relat., corrigida pelo Asaas se divergir.
    """
    tarifa_calc = 0.0
    if isinstance(banco_name, str):
        info = params["bancos"].get(banco_name.strip().upper())
        if info:
            tarifa_calc = info["tarifa_in"]

    if asaas_taxa_lookup and isinstance(end_to_end, str):
        taxa_asaas = asaas_taxa_lookup.get(end_to_end.strip().upper())
        if taxa_asaas is not None and float(taxa_asaas) != tarifa_calc:
            return float(taxa_asaas)

    return tarifa_calc


def classificar_baas(merchant, params):
    if not isinstance(merchant, str):
        return "BOLSÃO"
    return "BAAS" if merchant.strip().upper() in params["baas"] else "BOLSÃO"


def get_comissao_plataforma(platform_name, params):
    """Valor fixo em R$ por transação pelo PLATFORM NAME."""
    if not isinstance(platform_name, str):
        return 0.0
    return params["valor_por_comercial"].get(platform_name.strip().upper(), 0.0)


def get_comissionado_comercial(merchant, params):
    """Retorna (comercial, percentual) para o merchant."""
    if not isinstance(merchant, str):
        return "", 0.0
    info = params["comissionados_comercial"].get(merchant.strip().upper())
    if not info:
        return "", 0.0
    return info["comercial"], info["percentual"]


def calcular_impostos(fee, tarifa, baas_bolsao, params):
    """
    BAAS:   max(FEE - TARIFA, 0) × alíquota total
    BOLSÃO: max(FEE, 0) × alíquota total
    """
    if not fee or pd.isna(fee):
        return 0.0
    aliquota = params["aliquota_total"]
    base = max((fee - tarifa), 0) if baas_bolsao == "BAAS" else max(fee, 0)
    return round(base * aliquota, 2)


# ============================================================
# HELPERS
# ============================================================

def limpar_datas(df, colunas):
    for col in colunas:
        if col in df.columns:
            df[col] = pd.to_datetime(
                df[col].astype(str).str.slice(0, 10),
                format="%Y-%m-%d", errors="coerce"
            ).dt.strftime("%d/%m/%Y").fillna("")
    return df


def limpar_monetarios(df, colunas):
    for col in colunas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def aplicar_calculos(df, params, col_fee, col_banco, col_end2end,
                     col_platform, col_merchant, col_status,
                     status_calcular, asaas_taxa_lookup=None, asaas_valor_lookup=None,
                     tipo="cashin"):
    """
    Aplica todas as colunas calculadas em um DataFrame.
    """
    calc = df[col_status].isin(status_calcular) & (df[col_fee].fillna(0) > 0)

    # BAAS/BOLSÃO
    df["BAAS/BOLSÃO"] = df[col_merchant].apply(lambda m: classificar_baas(m, params))

    # Inicializa colunas
    for col in ["TARIFA", "IMPOSTOS", "COMISSÃO PLATAFORMA", "LUCRO INTERMEDIÁRIO",
                "COMISSIONADO COMERCIAL", "COMISSÃO COMERCIAL", "LUCRO FINAL"]:
        df[col] = None

    # TARIFA
    df.loc[calc, "TARIFA"] = df.loc[calc].apply(
        lambda r: calcular_tarifa(r.get(col_banco), r.get(col_end2end), params, asaas_taxa_lookup), axis=1)

    # COMISSÃO PLATAFORMA
    df.loc[calc, "COMISSÃO PLATAFORMA"] = df.loc[calc, col_platform].apply(
        lambda p: get_comissao_plataforma(p, params) if pd.notna(p) else 0)

    # IMPOSTOS
    df.loc[calc, "IMPOSTOS"] = df.loc[calc].apply(
        lambda r: calcular_impostos(r[col_fee], r["TARIFA"] or 0, r["BAAS/BOLSÃO"], params), axis=1)

    # LUCRO INTERMEDIÁRIO
    df.loc[calc, "LUCRO INTERMEDIÁRIO"] = (
        df.loc[calc, col_fee]
        .sub(df.loc[calc, "TARIFA"])
        .sub(df.loc[calc, "IMPOSTOS"])
        .sub(df.loc[calc, "COMISSÃO PLATAFORMA"].fillna(0))
    ).round(2)

    # COMISSÃO COMERCIAL
    def get_com(row):
        comercial, perc = get_comissionado_comercial(row[col_merchant], params)
        valor = round(row["LUCRO INTERMEDIÁRIO"] * perc, 2) if perc and not pd.isna(row["LUCRO INTERMEDIÁRIO"]) else 0.0
        return pd.Series({"COMISSIONADO COMERCIAL": comercial, "COMISSÃO COMERCIAL": valor})

    com = df.loc[calc].apply(get_com, axis=1)
    df.loc[calc, "COMISSIONADO COMERCIAL"] = com["COMISSIONADO COMERCIAL"].values
    df.loc[calc, "COMISSÃO COMERCIAL"] = com["COMISSÃO COMERCIAL"].values

    # LUCRO FINAL
    df.loc[calc, "LUCRO FINAL"] = (
        df.loc[calc, "LUCRO INTERMEDIÁRIO"]
        .sub(df.loc[calc, "COMISSÃO COMERCIAL"].fillna(0))
    ).round(2)

    # CONCILIAÇÃO (só para CASH-IN e CASH-OUT)
    if asaas_valor_lookup and col_end2end in df.columns:
        status_conc = "PROCESSED" if tipo == "cashin" else "SUCCESSFULLY PROCESSED"
        def conciliar(row):
            if row[col_status] != status_conc:
                return None
            e2e = str(row.get(col_end2end, "")).strip().upper()
            valor_banco = asaas_valor_lookup.get(e2e)
            if valor_banco is None:
                return "#N/D"
            diff = round(float(row.get("AMOUNT", 0) or 0) - abs(float(valor_banco)), 2)
            return "CONCILIADO" if diff == 0 else diff
        df["CONCILIAÇÃO"] = df.apply(conciliar, axis=1)

    return df


# ============================================================
# PROCESSADORES POR TIPO
# ============================================================

def processar_cashin(arquivo, asaas_taxa_lookup=None, asaas_valor_lookup=None, params=None):
    df = pd.read_excel(arquivo, sheet_name="CASH IN", dtype=str)
    df = limpar_datas(df, ["CREATION TIME", "EXPIRATION TIME", "PAYMENT TIME"])
    df = limpar_monetarios(df, ["AMOUNT", "FEE", "NET VALUE", "COMMISSION VALUE"])
    df = df.drop_duplicates(subset="UNIQUE ID", keep="last")
    df = df.rename(columns={"COMMISSION VALUE": "SPLIT"})
    df["ARQUIVO_ORIGEM"] = arquivo.name

    if params:
        df = aplicar_calculos(
            df, params,
            col_fee="FEE", col_banco="BANK NAME", col_end2end="END TO END ID",
            col_platform="PLATFORM NAME", col_merchant="MERCHANT NAME", col_status="STATUS",
            status_calcular={"PROCESSED", "REVERSAL"},
            asaas_taxa_lookup=asaas_taxa_lookup, asaas_valor_lookup=asaas_valor_lookup,
            tipo="cashin"
        )

    ordem = ["UNIQUE ID","CREATION TIME","EXPIRATION TIME","PAYMENT TIME",
             "WALLET NUMBER","WALLET NICKNAME","MERCHANT NAME","MERCHANT CODE",
             "COUNTRY CODE","CURRENCY CODE","BANK NAME","BANK CODE","END TO END ID",
             "SHOPPER NAME","SHOPPER DOC","VALID PAYER","PAYER NAME","PAYER DOC",
             "AMOUNT","FEE","NET VALUE","SPLIT","TARIFA","IMPOSTOS",
             "COMISSÃO PLATAFORMA","LUCRO INTERMEDIÁRIO","COMISSÃO COMERCIAL","LUCRO FINAL",
             "STATUS","BAAS/BOLSÃO","COMISSIONADO COMERCIAL","CONCILIAÇÃO",
             "SALES ID","PAYMENT ID","CONCILIATION ID","ORIGIN",
             "PLATFORM NAME","PLATFORM DOC NUMBER","ARQUIVO_ORIGEM"]
    ordem_final = [c for c in ordem if c in df.columns]
    df = df[ordem_final + [c for c in df.columns if c not in ordem_final]]
    return df, "cashin", "UNIQUE ID"


def processar_cashout(arquivo, asaas_taxa_lookup=None, asaas_valor_lookup=None, params=None):
    df = pd.read_excel(arquivo, sheet_name="CASH OUT", dtype=str)
    df = limpar_datas(df, ["CREATION TIME", "NOTIFICATION TIME"])
    df = limpar_monetarios(df, ["AMOUNT", "COMMISSION", "NET VALUE", "PIX VALUE"])
    df = df.drop_duplicates(subset="UNIQUE ID", keep="last")
    df["ARQUIVO_ORIGEM"] = arquivo.name

    if params:
        df = aplicar_calculos(
            df, params,
            col_fee="COMMISSION", col_banco="BANK NAME", col_end2end="END TO END ID",
            col_platform="PLATFORM NAME", col_merchant="MERCHANT NAME", col_status="STATUS",
            status_calcular={"SUCCESSFULLY PROCESSED", "PROCESSED WITH ERROR"},
            asaas_taxa_lookup=asaas_taxa_lookup, asaas_valor_lookup=asaas_valor_lookup,
            tipo="cashout"
        )

    ordem = ["UNIQUE ID","CREATION TIME","NOTIFICATION TIME","WALLET NUMBER","WALLET NICKNAME",
             "MERCHANT NAME","MERCHANT CODE","COUNTRY CODE","CURRENCY CODE",
             "BANK NAME","BANK CODE","END TO END ID","SHOPPER NAME","SHOPPER DOC NUMBER",
             "AMOUNT","COMMISSION","NET VALUE","PIX VALUE","PIX TYPE",
             "TARIFA","IMPOSTOS","COMISSÃO PLATAFORMA","LUCRO INTERMEDIÁRIO",
             "COMISSÃO COMERCIAL","LUCRO FINAL","STATUS","BAAS/BOLSÃO","COMISSIONADO COMERCIAL",
             "CONCILIAÇÃO","PAYMENT ID","MERCHANT REFERENCE","ORIGIN",
             "PLATFORM NAME","PLATFORM DOC NUMBER","ARQUIVO_ORIGEM"]
    ordem_final = [c for c in ordem if c in df.columns]
    df = df[ordem_final + [c for c in df.columns if c not in ordem_final]]
    return df, "cashout", "UNIQUE ID"


def processar_pagamentos(arquivo, params=None):
    df = pd.read_excel(arquivo, sheet_name="PAYMENT", dtype=str)
    df = limpar_datas(df, ["DUE DATE", "PAYMENT DATE"])
    df = limpar_monetarios(df, ["AMOUNT", "AMOUNT PAID", "FEE"])
    df = df.drop_duplicates(subset="UNIQUE ID", keep="last")
    df["ARQUIVO_ORIGEM"] = arquivo.name
    return df, "pagamentos", "UNIQUE ID"


def processar_cartao(arquivo, params=None):
    df = pd.read_excel(arquivo, sheet_name="CARD", dtype=str)
    df = limpar_datas(df, ["CREATION TIME"])
    df = limpar_monetarios(df, ["AMOUNT", "CAPTURE AMOUNT", "FEE", "FEE VALUE"])
    df = df.drop_duplicates(subset="PAYMENT ID", keep="last")
    df["ARQUIVO_ORIGEM"] = arquivo.name

    if params:
        STATUS_CALCULAR = {"AUTHORIZED", "CREATED"}
        calc = df["STATUS"].isin(STATUS_CALCULAR) & (df["FEE VALUE"].fillna(0) > 0)
        df["BAAS/BOLSÃO"] = df["MERCHANT NAME"].apply(lambda m: classificar_baas(m, params))

        for col in ["IMPOSTOS","COMISSÃO PLATAFORMA","LUCRO INTERMEDIÁRIO",
                    "COMISSIONADO COMERCIAL","COMISSÃO COMERCIAL","LUCRO FINAL"]:
            df[col] = None

        df.loc[calc, "COMISSÃO PLATAFORMA"] = df.loc[calc, "PLATFORM NAME"].apply(
            lambda p: get_comissao_plataforma(p, params) if pd.notna(p) else 0)
        df.loc[calc, "IMPOSTOS"] = df.loc[calc].apply(
            lambda r: calcular_impostos(r["FEE VALUE"], 0, r["BAAS/BOLSÃO"], params), axis=1)
        df.loc[calc, "LUCRO INTERMEDIÁRIO"] = (
            df.loc[calc, "FEE VALUE"]
            .sub(df.loc[calc, "IMPOSTOS"])
            .sub(df.loc[calc, "COMISSÃO PLATAFORMA"].fillna(0))
        ).round(2)

        def get_com(row):
            comercial, perc = get_comissionado_comercial(row["MERCHANT NAME"], params)
            valor = round(row["LUCRO INTERMEDIÁRIO"] * perc, 2) if perc and not pd.isna(row["LUCRO INTERMEDIÁRIO"]) else 0.0
            return pd.Series({"COMISSIONADO COMERCIAL": comercial, "COMISSÃO COMERCIAL": valor})

        com = df.loc[calc].apply(get_com, axis=1)
        df.loc[calc, "COMISSIONADO COMERCIAL"] = com["COMISSIONADO COMERCIAL"].values
        df.loc[calc, "COMISSÃO COMERCIAL"] = com["COMISSÃO COMERCIAL"].values
        df.loc[calc, "LUCRO FINAL"] = (
            df.loc[calc, "LUCRO INTERMEDIÁRIO"]
            .sub(df.loc[calc, "COMISSÃO COMERCIAL"].fillna(0))
        ).round(2)

    ordem = ["PAYMENT ID","SALES ID","CREATION TIME","CARD","BRAND","TRANSACTION TYPE",
             "AUTHORISATION CODE","NSU","MERCHANT NAME","SHOPPER NAME","SHOPPER DOC",
             "AMOUNT","CAPTURE AMOUNT","FEE","FEE VALUE",
             "IMPOSTOS","COMISSÃO PLATAFORMA","LUCRO INTERMEDIÁRIO",
             "COMISSÃO COMERCIAL","LUCRO FINAL","STATUS","BAAS/BOLSÃO","COMISSIONADO COMERCIAL",
             "NUMBER INSTALLMENTS","PLATFORM NAME","PLATFORM DOC NUMBER","ARQUIVO_ORIGEM"]
    ordem_final = [c for c in ordem if c in df.columns]
    df = df[ordem_final + [c for c in df.columns if c not in ordem_final]]
    return df, "cartao", "PAYMENT ID"


def identificar_tipo(arquivo):
    nome = arquivo.name.upper()
    if "-IN" in nome or "CASHIN" in nome: return "cashin"
    elif "-OUT" in nome or "CASHOUT" in nome: return "cashout"
    elif "-PAG" in nome or "PAGAMENT" in nome: return "pagamentos"
    elif "-CART" in nome or "CART" in nome: return "cartao"
    try:
        xf = pd.ExcelFile(arquivo)
        sheets = [s.upper() for s in xf.sheet_names]
        if "CASH IN" in sheets: return "cashin"
        elif "CASH OUT" in sheets: return "cashout"
        elif "PAYMENT" in sheets: return "pagamentos"
        elif "CARD" in sheets: return "cartao"
    except: pass
    return None


PROCESSADORES = {
    "cashin":     processar_cashin,
    "cashout":    processar_cashout,
    "pagamentos": processar_pagamentos,
    "cartao":     processar_cartao,
}
