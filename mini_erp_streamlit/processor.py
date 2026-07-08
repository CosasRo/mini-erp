"""
Mini ERP - Módulo de processamento dos arquivos
Contém toda a lógica de transformação dos dados brutos
"""

import math
import pandas as pd


# ============================================================
# BASE RELAT. - Parâmetros hardcoded (atualizados periodicamente)
# ============================================================

TARIFA_BANCOS = {
    "ASAAS": 0.06,
    "BANCO DO BRASIL": 0.08,
}

ALIQUOTA_IMPOSTOS = 0.1653  # 16.53%

COMISSAO_PLATAFORMA = {
    "CSO":         0.05,
    "MULTITCH":    0.05,
    "OXXY":        0.05,
    "SHOPPUB":     0.12,
    "IRROBA":      0.12,
    "COMPULETRA":  0.00,
    "DIRETO":      0.00,
    "SDR":         0.00,
    "RIFA WEB":    0.00,
    "RIFA 321":    0.00,
    "FASTCOMMERCE": 0.00,
    "FENOX":       0.00,
}

# Lista de merchants BAAS (carregada do Supabase ou hardcoded)
BAAS_SET = set()

# Comissionados comerciais: merchant -> (comercial, percentual)
COMISSIONADOS_COMERCIAL = {
    "VINICOLA LEONE DI VENEZIA LTDA": ("GABRIEL", 0.12),
    "ALESSANDRO W. S. PINTO": ("GABRIEL", 0.12),
}


def carregar_baas_do_supabase(sb):
    """Carrega lista BAAS do Supabase."""
    global BAAS_SET
    try:
        res = sb.table("cashin").select('"MERCHANT NAME","BAAS/BOLSÃO"').eq('"BAAS/BOLSÃO"', "BAAS").execute()
        BAAS_SET = {r["MERCHANT NAME"].strip().upper() for r in res.data if r.get("MERCHANT NAME")}
    except Exception:
        BAAS_SET = set()


def classificar_baas(merchant):
    if not isinstance(merchant, str):
        return "BOLSÃO"
    return "BAAS" if merchant.strip().upper() in BAAS_SET else "BOLSÃO"


def calcular_tarifa(banco_name, end_to_end=None, asaas_taxa_lookup=None):
    """Calcula tarifa: BASE RELAT. como base, Asaas como validação."""
    tarifa_calc = TARIFA_BANCOS.get(str(banco_name).strip().upper(), 0.0) if banco_name else 0.0
    if asaas_taxa_lookup and end_to_end:
        taxa_asaas = asaas_taxa_lookup.get(str(end_to_end).strip().upper())
        if taxa_asaas is not None and float(taxa_asaas) != tarifa_calc:
            return float(taxa_asaas)
    return tarifa_calc


def calcular_impostos(fee, tarifa, baas_bolsao):
    """BAAS=(FEE-TARIFA)*16.53% | BOLSÃO=FEE*16.53% | mínimo 0"""
    if not fee or pd.isna(fee):
        return 0.0
    base = max((fee - tarifa), 0) if baas_bolsao == "BAAS" else max(fee, 0)
    return round(base * ALIQUOTA_IMPOSTOS, 2)


def get_comissao_plataforma(platform_name):
    """Valor fixo em R$ por transação pelo PLATFORM NAME."""
    if not isinstance(platform_name, str):
        return 0.0
    return COMISSAO_PLATAFORMA.get(platform_name.strip().upper(), 0.0)


def get_comissionado_comercial(merchant):
    """Retorna (comercial, percentual) para o merchant."""
    if not isinstance(merchant, str):
        return "", 0.0
    return COMISSIONADOS_COMERCIAL.get(merchant.strip().upper(), ("", 0.0))


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


def limpar_registro(registro):
    """Prepara registro para inserção no Supabase."""
    limpo = {}
    for k, v in registro.items():
        if v is None:
            limpo[k] = None
        elif isinstance(v, float) and math.isnan(v):
            limpo[k] = None
        elif hasattr(v, "item"):
            limpo[k] = v.item()
        else:
            limpo[k] = v
    return limpo


# ============================================================
# PROCESSADORES POR TIPO
# ============================================================

def processar_cashin(arquivo, asaas_taxa_lookup=None, asaas_valor_lookup=None):
    """Processa arquivo bruto CASH-IN."""
    df = pd.read_excel(arquivo, sheet_name="CASH IN", dtype=str)
    df = limpar_datas(df, ["CREATION TIME", "EXPIRATION TIME", "PAYMENT TIME"])
    df = limpar_monetarios(df, ["AMOUNT", "FEE", "NET VALUE", "COMMISSION VALUE"])
    df = df.drop_duplicates(subset="UNIQUE ID", keep="last")
    df = df.rename(columns={"COMMISSION VALUE": "SPLIT"})
    df["ARQUIVO_ORIGEM"] = arquivo.name

    STATUS_CALCULAR = {"PROCESSED", "REVERSAL"}
    calc = df["STATUS"].isin(STATUS_CALCULAR)

    df["BAAS/BOLSÃO"] = df["MERCHANT NAME"].apply(classificar_baas)

    for col in ["TARIFA", "IMPOSTOS", "COMISSÃO PLATAFORMA", "LUCRO INTERMEDIÁRIO",
                "COMISSIONADO COMERCIAL", "COMISSÃO COMERCIAL", "LUCRO FINAL"]:
        df[col] = None

    df.loc[calc, "TARIFA"] = df.loc[calc].apply(
        lambda r: calcular_tarifa(r.get("BANK NAME"), r.get("END TO END ID"), asaas_taxa_lookup), axis=1)
    df.loc[calc, "COMISSÃO PLATAFORMA"] = df.loc[calc, "PLATFORM NAME"].apply(get_comissao_plataforma)
    df.loc[calc, "IMPOSTOS"] = df.loc[calc].apply(
        lambda r: calcular_impostos(r["FEE"], r["TARIFA"] or 0, r["BAAS/BOLSÃO"]), axis=1)
    df.loc[calc, "LUCRO INTERMEDIÁRIO"] = (
        df.loc[calc, "FEE"]
        .sub(df.loc[calc, "TARIFA"])
        .sub(df.loc[calc, "IMPOSTOS"])
        .sub(df.loc[calc, "COMISSÃO PLATAFORMA"].fillna(0))
    ).round(2)

    def get_com(row):
        comercial, perc = get_comissionado_comercial(row["MERCHANT NAME"])
        valor = round(row["LUCRO INTERMEDIÁRIO"] * perc, 2) if perc and not pd.isna(row["LUCRO INTERMEDIÁRIO"]) else 0.0
        return pd.Series({"COMISSIONADO COMERCIAL": comercial, "COMISSÃO COMERCIAL": valor})

    com = df.loc[calc].apply(get_com, axis=1)
    df.loc[calc, "COMISSIONADO COMERCIAL"] = com["COMISSIONADO COMERCIAL"].values
    df.loc[calc, "COMISSÃO COMERCIAL"] = com["COMISSÃO COMERCIAL"].values
    df.loc[calc, "LUCRO FINAL"] = (
        df.loc[calc, "LUCRO INTERMEDIÁRIO"].sub(df.loc[calc, "COMISSÃO COMERCIAL"].fillna(0))).round(2)

    def conciliar(row):
        if row["STATUS"] != "PROCESSED" or not asaas_valor_lookup:
            return None
        end_to_end = str(row.get("END TO END ID", "")).strip().upper()
        valor_banco = asaas_valor_lookup.get(end_to_end)
        if valor_banco is None:
            return "#N/D"
        diferenca = round(float(row.get("AMOUNT", 0)) - float(valor_banco), 2)
        return "CONCILIADO" if diferenca == 0 else diferenca

    df["CONCILIAÇÃO"] = df.apply(conciliar, axis=1)

    ordem = ["UNIQUE ID", "CREATION TIME", "EXPIRATION TIME", "PAYMENT TIME",
             "WALLET NUMBER", "WALLET NICKNAME", "MERCHANT NAME", "MERCHANT CODE",
             "COUNTRY CODE", "CURRENCY CODE", "BANK NAME", "BANK CODE", "END TO END ID",
             "SHOPPER NAME", "SHOPPER DOC", "VALID PAYER", "PAYER NAME", "PAYER DOC",
             "AMOUNT", "FEE", "NET VALUE", "SPLIT", "TARIFA", "IMPOSTOS",
             "COMISSÃO PLATAFORMA", "LUCRO INTERMEDIÁRIO", "COMISSÃO COMERCIAL", "LUCRO FINAL",
             "STATUS", "BAAS/BOLSÃO", "COMISSIONADO COMERCIAL", "CONCILIAÇÃO",
             "SALES ID", "PAYMENT ID", "CONCILIATION ID", "ORIGIN",
             "PLATFORM NAME", "PLATFORM DOC NUMBER", "ARQUIVO_ORIGEM"]
    ordem_final = [c for c in ordem if c in df.columns]
    df = df[ordem_final + [c for c in df.columns if c not in ordem_final]]
    return df, "cashin", "UNIQUE ID"


def processar_cashout(arquivo, asaas_taxa_lookup=None, asaas_valor_lookup=None):
    """Processa arquivo bruto CASH-OUT."""
    df = pd.read_excel(arquivo, sheet_name="CASH OUT", dtype=str)
    df = limpar_datas(df, ["CREATION TIME", "NOTIFICATION TIME"])
    df = limpar_monetarios(df, ["AMOUNT", "COMMISSION", "NET VALUE", "PIX VALUE"])
    df = df.drop_duplicates(subset="UNIQUE ID", keep="last")
    df["ARQUIVO_ORIGEM"] = arquivo.name

    STATUS_CALCULAR = {"SUCCESSFULLY PROCESSED", "PROCESSED WITH ERROR"}
    calc = df["STATUS"].isin(STATUS_CALCULAR) & (df["COMMISSION"].fillna(0) > 0)

    df["BAAS/BOLSÃO"] = df["MERCHANT NAME"].apply(classificar_baas)

    for col in ["TARIFA", "IMPOSTOS", "COMISSÃO PLATAFORMA", "LUCRO INTERMEDIÁRIO",
                "COMISSIONADO COMERCIAL", "COMISSÃO COMERCIAL", "LUCRO FINAL"]:
        df[col] = None

    df.loc[calc, "TARIFA"] = df.loc[calc].apply(
        lambda r: calcular_tarifa(r.get("BANK NAME"), r.get("END TO END ID"), asaas_taxa_lookup), axis=1)
    df.loc[calc, "COMISSÃO PLATAFORMA"] = df.loc[calc, "PLATFORM NAME"].apply(get_comissao_plataforma)
    df.loc[calc, "IMPOSTOS"] = df.loc[calc].apply(
        lambda r: calcular_impostos(r["COMMISSION"], r["TARIFA"] or 0, r["BAAS/BOLSÃO"]), axis=1)
    df.loc[calc, "LUCRO INTERMEDIÁRIO"] = (
        df.loc[calc, "COMMISSION"]
        .sub(df.loc[calc, "TARIFA"])
        .sub(df.loc[calc, "IMPOSTOS"])
        .sub(df.loc[calc, "COMISSÃO PLATAFORMA"].fillna(0))
    ).round(2)

    def get_com(row):
        comercial, perc = get_comissionado_comercial(row["MERCHANT NAME"])
        valor = round(row["LUCRO INTERMEDIÁRIO"] * perc, 2) if perc and not pd.isna(row["LUCRO INTERMEDIÁRIO"]) else 0.0
        return pd.Series({"COMISSIONADO COMERCIAL": comercial, "COMISSÃO COMERCIAL": valor})

    com = df.loc[calc].apply(get_com, axis=1)
    df.loc[calc, "COMISSIONADO COMERCIAL"] = com["COMISSIONADO COMERCIAL"].values
    df.loc[calc, "COMISSÃO COMERCIAL"] = com["COMISSÃO COMERCIAL"].values
    df.loc[calc, "LUCRO FINAL"] = (
        df.loc[calc, "LUCRO INTERMEDIÁRIO"].sub(df.loc[calc, "COMISSÃO COMERCIAL"].fillna(0))).round(2)

    def conciliar(row):
        if row["STATUS"] != "SUCCESSFULLY PROCESSED" or not asaas_valor_lookup:
            return None
        end_to_end = str(row.get("END TO END ID", "")).strip().upper()
        valor_banco = asaas_valor_lookup.get(end_to_end)
        if valor_banco is None:
            return "#N/D"
        diferenca = round(float(row.get("AMOUNT", 0)) - abs(float(valor_banco)), 2)
        return "CONCILIADO" if diferenca == 0 else diferenca

    df["CONCILIAÇÃO"] = df.apply(conciliar, axis=1)

    ordem = ["UNIQUE ID", "CREATION TIME", "NOTIFICATION TIME", "WALLET NUMBER", "WALLET NICKNAME",
             "MERCHANT NAME", "MERCHANT CODE", "COUNTRY CODE", "CURRENCY CODE",
             "BANK NAME", "BANK CODE", "END TO END ID", "SHOPPER NAME", "SHOPPER DOC NUMBER",
             "AMOUNT", "COMMISSION", "NET VALUE", "PIX VALUE", "PIX TYPE",
             "TARIFA", "IMPOSTOS", "COMISSÃO PLATAFORMA", "LUCRO INTERMEDIÁRIO",
             "COMISSÃO COMERCIAL", "LUCRO FINAL", "STATUS", "BAAS/BOLSÃO", "COMISSIONADO COMERCIAL",
             "CONCILIAÇÃO", "PAYMENT ID", "MERCHANT REFERENCE", "ORIGIN",
             "PLATFORM NAME", "PLATFORM DOC NUMBER", "ARQUIVO_ORIGEM"]
    ordem_final = [c for c in ordem if c in df.columns]
    df = df[ordem_final + [c for c in df.columns if c not in ordem_final]]
    return df, "cashout", "UNIQUE ID"


def processar_pagamentos(arquivo):
    """Processa arquivo bruto PAGAMENTOS."""
    df = pd.read_excel(arquivo, sheet_name="PAYMENT", dtype=str)
    df = limpar_datas(df, ["DUE DATE", "PAYMENT DATE"])
    df = limpar_monetarios(df, ["AMOUNT", "AMOUNT PAID", "FEE"])
    df = df.drop_duplicates(subset="UNIQUE ID", keep="last")
    df["ARQUIVO_ORIGEM"] = arquivo.name
    return df, "pagamentos", "UNIQUE ID"


def processar_cartao(arquivo):
    """Processa arquivo bruto CARTÃO."""
    df = pd.read_excel(arquivo, sheet_name="CARD", dtype=str)
    df = limpar_datas(df, ["CREATION TIME"])
    df = limpar_monetarios(df, ["AMOUNT", "CAPTURE AMOUNT", "FEE", "FEE VALUE"])
    df = df.drop_duplicates(subset="PAYMENT ID", keep="last")
    df["ARQUIVO_ORIGEM"] = arquivo.name

    STATUS_CALCULAR = {"AUTHORIZED", "CREATED"}
    calc = df["STATUS"].isin(STATUS_CALCULAR) & (df["FEE VALUE"].fillna(0) > 0)

    df["BAAS/BOLSÃO"] = df["MERCHANT NAME"].apply(classificar_baas)

    for col in ["IMPOSTOS", "COMISSÃO PLATAFORMA", "LUCRO INTERMEDIÁRIO",
                "COMISSIONADO COMERCIAL", "COMISSÃO COMERCIAL", "LUCRO FINAL"]:
        df[col] = None

    df.loc[calc, "COMISSÃO PLATAFORMA"] = df.loc[calc, "PLATFORM NAME"].apply(get_comissao_plataforma)
    df.loc[calc, "IMPOSTOS"] = df.loc[calc].apply(
        lambda r: calcular_impostos(r["FEE VALUE"], 0, r["BAAS/BOLSÃO"]), axis=1)
    df.loc[calc, "LUCRO INTERMEDIÁRIO"] = (
        df.loc[calc, "FEE VALUE"]
        .sub(df.loc[calc, "IMPOSTOS"])
        .sub(df.loc[calc, "COMISSÃO PLATAFORMA"].fillna(0))
    ).round(2)

    def get_com(row):
        comercial, perc = get_comissionado_comercial(row["MERCHANT NAME"])
        valor = round(row["LUCRO INTERMEDIÁRIO"] * perc, 2) if perc and not pd.isna(row["LUCRO INTERMEDIÁRIO"]) else 0.0
        return pd.Series({"COMISSIONADO COMERCIAL": comercial, "COMISSÃO COMERCIAL": valor})

    com = df.loc[calc].apply(get_com, axis=1)
    df.loc[calc, "COMISSIONADO COMERCIAL"] = com["COMISSIONADO COMERCIAL"].values
    df.loc[calc, "COMISSÃO COMERCIAL"] = com["COMISSÃO COMERCIAL"].values
    df.loc[calc, "LUCRO FINAL"] = (
        df.loc[calc, "LUCRO INTERMEDIÁRIO"].sub(df.loc[calc, "COMISSÃO COMERCIAL"].fillna(0))).round(2)

    ordem = ["PAYMENT ID", "SALES ID", "CREATION TIME", "CARD", "BRAND", "TRANSACTION TYPE",
             "AUTHORISATION CODE", "NSU", "MERCHANT NAME", "SHOPPER NAME", "SHOPPER DOC",
             "AMOUNT", "CAPTURE AMOUNT", "FEE", "FEE VALUE",
             "IMPOSTOS", "COMISSÃO PLATAFORMA", "LUCRO INTERMEDIÁRIO",
             "COMISSÃO COMERCIAL", "LUCRO FINAL", "STATUS", "BAAS/BOLSÃO", "COMISSIONADO COMERCIAL",
             "NUMBER INSTALLMENTS", "PLATFORM NAME", "PLATFORM DOC NUMBER", "ARQUIVO_ORIGEM"]
    ordem_final = [c for c in ordem if c in df.columns]
    df = df[ordem_final + [c for c in df.columns if c not in ordem_final]]
    return df, "cartao", "PAYMENT ID"


def identificar_tipo(arquivo):
    """Identifica o tipo do arquivo pelo nome ou conteúdo."""
    nome = arquivo.name.upper()
    if "-IN" in nome or "CASHIN" in nome:
        return "cashin"
    elif "-OUT" in nome or "CASHOUT" in nome:
        return "cashout"
    elif "-PAG" in nome or "PAGAMENT" in nome:
        return "pagamentos"
    elif "-CART" in nome or "CART" in nome:
        return "cartao"
    # Tenta identificar pelo conteúdo
    try:
        xf = pd.ExcelFile(arquivo)
        sheets = [s.upper() for s in xf.sheet_names]
        if "CASH IN" in sheets:
            return "cashin"
        elif "CASH OUT" in sheets:
            return "cashout"
        elif "PAYMENT" in sheets:
            return "pagamentos"
        elif "CARD" in sheets:
            return "cartao"
    except Exception:
        pass
    return None


PROCESSADORES = {
    "cashin":     processar_cashin,
    "cashout":    processar_cashout,
    "pagamentos": processar_pagamentos,
    "cartao":     processar_cartao,
}
