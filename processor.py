# processor.py v2.0 - com extratos bancários
"""
Pago Express - Mini ERP
Processador de arquivos brutos
Parâmetros buscados do Supabase (Base Relat.)
"""

import pandas as pd


# ============================================================
# CARREGA PARÂMETROS DO SUPABASE
# ============================================================




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


# ============================================================
# PROCESSADORES DE EXTRATOS BANCÁRIOS
# ============================================================

def processar_extrato_asaas(arquivo):
    """Processa o extrato completo do Asaas (xlsx)."""
    try:
        df = pd.read_excel(arquivo, header=2, dtype=str)
    except Exception:
        df = pd.read_excel(arquivo, dtype=str)

    df = df.dropna(how="all")
    df = df[df.iloc[:, 0].notna()]

    # Converte valores numéricos
    for col in ["Valor", "Saldo"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ARQUIVO_ORIGEM"] = arquivo.name
    return df, "extrato_asaas", None


def processar_bb(arquivo, conta):
    """
    Processa extrato do Banco do Brasil.
    conta: '1160-6' (PIX) ou '1547-4' (ADM)
    tabela: 'bb_pix' ou 'bb_adm'
    """
    COLUNAS = ["Data", "observacao", "Data balancete", "Agencia Origem",
               "Lote", "Numero Documento", "Cod. Historico", "Historico",
               "Valor R$", "Inf.", "Detalhamento Hist."]

    try:
        df = pd.read_excel(arquivo, sheet_name="Extrato", header=2, dtype=str)
        df.columns = COLUNAS[:len(df.columns)]
    except Exception:
        df = pd.read_excel(arquivo, header=2, dtype=str)

    # Remove linhas vazias e saldos
    df = df.dropna(subset=["Data", "Historico"] if "Historico" in df.columns else ["Data"], how="any")
    df = df[df["Data"].astype(str).str.match(r"\d{2}/\d{2}/\d{4}", na=False)]

    # Converte valor
    if "Valor R$" in df.columns:
        df["Valor R$"] = (
            df["Valor R$"].astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        df["Valor R$"] = pd.to_numeric(df["Valor R$"], errors="coerce")
        # Débitos ficam negativos
        if "Inf." in df.columns:
            df.loc[df["Inf."] == "D", "Valor R$"] = df.loc[df["Inf."] == "D", "Valor R$"] * -1

    df["CONTA"] = conta
    df["ARQUIVO_ORIGEM"] = arquivo.name

    tabela = "bb_pix" if "1160" in conta else "bb_adm"
    return df, tabela, None


def processar_bb_pix(arquivo):
    return processar_bb(arquivo, "1160-6")


def processar_bb_adm(arquivo):
    return processar_bb(arquivo, "1547-4")


def identificar_extrato(arquivo):
    """Identifica o tipo de extrato bancário pelo nome do arquivo."""
    nome = arquivo.name.upper()
    if "ASAAS" in nome and ("EXTRATO" in nome or ".XLSX" in nome):
        return "extrato_asaas"
    elif "9894911606" in nome or "1160" in nome:
        return "bb_pix"
    elif "9894915474" in nome or "1547" in nome:
        return "bb_adm"
    elif "BB" in nome or "BRASIL" in nome:
        return "bb_pix"  # default BB
    return None



# ============================================================
# PROCESSADOR DE CLIENTES
# ============================================================

def limpar_cpf_cnpj(valor):
    """Remove formatação do CPF/CNPJ deixando só números."""
    if not isinstance(valor, str):
        return ""
    return ''.join(c for c in valor if c.isdigit())


def formatar_cpf_cnpj(valor):
    """Formata CPF/CNPJ para exibição."""
    nums = ''.join(c for c in str(valor) if c.isdigit())
    if len(nums) == 14:
        return f"{nums[:2]}.{nums[2:5]}.{nums[5:8]}/{nums[8:12]}-{nums[12:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}.{nums[3:6]}.{nums[6:9]}-{nums[9:]}"
    return valor


def processar_clientes(arquivo):
    """
    Processa arquivo de clientes da PagoExpress.
    - Padroniza CPF/CNPJ (só números) como chave única
    - Limpa telefone (só números)
    - Padroniza CEP (só números)
    - Mantém ATIVO como SIM/NÃO
    - Upsert por CPF/CNPJ: atualiza se existir, insere se novo
    """
    df = pd.read_excel(arquivo, dtype=str)
    df = df.fillna("")

    # Renomeia coluna CPF/CNPJ para CPFCNPJ (sem barra)
    if "CPF/CNPJ" in df.columns:
        df = df.rename(columns={"CPF/CNPJ": "CPFCNPJ"})

    # Padroniza CPFCNPJ — chave única, só números
    df["CPFCNPJ"] = df["CPFCNPJ"].apply(limpar_cpf_cnpj)

    # Remove linhas sem CPF/CNPJ válido
    df = df[df["CPFCNPJ"].astype(str).str.len() >= 11]

    # Remove duplicatas pelo CPFCNPJ (mantém o último)
    df = df.drop_duplicates(subset="CPFCNPJ", keep="last")

    # Padroniza telefone — só números
    df["TELEFONE"] = df["TELEFONE"].apply(
        lambda v: ''.join(c for c in str(v) if c.isdigit()) if v else "")

    # Padroniza CEP — só números
    df["CEP"] = df["CEP"].apply(
        lambda v: ''.join(c for c in str(v) if c.isdigit()) if v else "")

    # Padroniza ATIVO
    df["ATIVO"] = df["ATIVO"].str.strip().str.upper()
    df["ATIVO"] = df["ATIVO"].apply(lambda v: "SIM" if v in ["SIM","S","1","TRUE","YES"] else "NÃO")

    # Maiúsculas nos campos de texto principais
    for col in ["NOME FANTASIA", "RAZAO SOCIAL", "SEGMENTO", "CIDADE", "ESTADO", "PAIS", "PLATAFORMA", "BAIRRO"]:
        if col in df.columns:
            df[col] = df[col].str.strip().str.upper()

    df["ARQUIVO_ORIGEM"] = arquivo.name

    return df, "clientes", "CPFCNPJ"
