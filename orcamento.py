"""
Orçado x Realizado - Mini ERP PagoExpress
Duas abas: comparação mensal Orçado x Realizado por Centro de Custo (só
visualização), e Compromissos Diários (cadastro manual: incluir, editar,
excluir, filtrar e baixar).
"""

import io
import pandas as pd
import streamlit as st
from supabase import create_client

CENTROS_CUSTO = [
    "13º Salário", "Ações Trab. Hon./Indenização/Custas/Rescisão", "Acordos Jurídicos 2022",
    "Acordos Jurídicos 2022 Extra", "Adiantamento a Fornecedores", "Água e esgoto",
    "Aluguel de imóveis", "Aluguel equipamentos", "Anúncios", "Aquisição de Softwares",
    "Assessoria de Imprensa", "Assessoria e Consultoria", "Assistência Jurídica", "Auditoria",
    "Benfeitoria de Imóveis", "Bens de diminuto valor", "Brindes e Presentes",
    "Cartão de Crédito Corporativo", "Cartório", "Combustíveis e lubrificantes", "Comissões",
    "Condomínio", "Conduções/Táxi", "Conservação de móveis e objetos de decoração",
    "Consultoria - Prestadores de Serviços Internos", "Consultoria de Projetos", "Contabilidade",
    "Contingência", "Contribuição Assistencial", "Contribuição Sindical Patronal-Empresa",
    "Contribuições Sindicais-Empregados", "Copa e Cozinha", "Cópias e Reproduções", "CSLL",
    "Criação da Campanha", "Cursos e Treinamentos", "Custos Adicionais",
    "Despesas com financiamentos", "Despesas de viagens", "Dissídio", "Diversos Marketing",
    "Documentos legais", "Emolumentos e taxas", "Encargos Sociais FGTS", "Encargos Sociais INSS",
    "Energia Elétrica", "Equipamentos de Processamento de Dados", "Estacionamento", "Férias",
    "Ferramentas", "Eventos", "Fundo Fixo de Caixa", "Gráfica", "Gratificação Bonus",
    "Guarda de Documentos", "Higiene e Limpeza", "Imóveis", "Indenizações",
    "Infraestrutura Digital (Site, Softwares Próprios)", "Infrastrutura de Hardware e Telefonia",
    "Instalações", "IOF", "IPTU", "IRRF sobre salários - cód. 0561",
    "Juros - Caixa Economica Federal", "Lanches, refeições", "Livros, Jornais, revistas e TV",
    "Mala Direta", "Manutenção de equipamentos", "Manutenção de Hardware/Software - Informática",
    "COFINS", "Manutenção Predial", "Máquinas aparelhos e Equipamentos",
    "Marcas Direitos e Patentes", "Marketing Institucional", "Material de escritório",
    "Material de Limpeza", "Motoboy", "Móveis e Utensílios", "Multa de FGTS rescisão",
    "Outros Impostos e Taxas", "Parque Gráfico (Tonners e Papéis)", "Pedágio",
    "Pesquisa de Mercado", "Plano de Saude", "Pró Labore", "Programa de alimentação",
    "Distribuição de Dividendos", "IRPJ", "Reembolso diversos", "Salários e Ordenados",
    "Saude Ocupacional", "Segurança", "Seguro de imóvel", "PIS", "Seguro de vida",
    "Serviços de Terceiros", "Serviços de Terceiros - Personalização",
    "Serviços de Terceiros - PJ", "Site", "Softwares", "Tarifas Bancárias",
    "Tarifas Bancárias Extras", "Telefone fixo/fax/internet", "Telefonia Móvel", "Terrenos",
    "Trabalhista", "ISS", "Uniformes", "Vale Transporte/Fretado", "Veículos",
]

BANCOS = ["Banco do Brasil", "Bradesco", "Asaas"]


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


MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def _carregar_orcado_ano(_sb, ano: int) -> pd.DataFrame:
    resp = (
        _sb.table("orcamento")
        .select("centro_custo,mes,valor_orcado")
        .gte("mes", f"{ano}-01-01")
        .lte("mes", f"{ano}-12-31")
        .execute()
    )
    df = pd.DataFrame(resp.data)
    if df.empty:
        return pd.DataFrame(columns=["centro_custo", "mes", "valor_orcado"])
    df["mes"] = pd.to_datetime(df["mes"])
    df["valor_orcado"] = pd.to_numeric(df["valor_orcado"], errors="coerce").fillna(0.0)
    return df


def _carregar_realizado_ano(_sb, ano: int) -> pd.DataFrame:
    resp = (
        _sb.table("compromissos_diarios")
        .select("centro_custo,mes,valor")
        .gte("mes", f"{ano}-01-01")
        .lte("mes", f"{ano}-12-31")
        .execute()
    )
    df = pd.DataFrame(resp.data)
    if df.empty:
        return pd.DataFrame(columns=["centro_custo", "mes", "valor"])
    df["mes"] = pd.to_datetime(df["mes"])
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)
    return df.groupby(["centro_custo", "mes"], as_index=False)["valor"].sum()


HIERARQUIA = {
    "Pessoal": [
        "Pró Labore", "Salários e Ordenados", "Dissídio", "Férias", "Vale Transporte/Fretado",
        "Programa de alimentação", "13 Salário", "Ações Trab. Hon./Indenização/Custas/Rescisão",
        "Plano de Saude", "Encargos Sociais INSS", "Encargos Sociais FGTS", "Gratificação Bonus",
        "Multa de FGTS rescisão", "Contribuições Sindicais-Empregados", "Cursos e Treinamentos",
        "Consultoria - Prestadores de Serviços Internos", "Contribuição Assistencial",
        "IRRF sobre salários - cód. 0561", "Saude Ocupacional", "Trabalhista", "Uniformes",
        "Seguro de vida", "Distribuição de Dividendos", "Contingência",
    ],
    "Ocupação": [
        "Água e esgoto", "Aluguel de imóveis", "Condomínio",
        "Conservação de móveis e objetos de decoração", "Energia Elétrica", "IPTU",
        "Manutenção Predial", "Segurança", "Seguro de imóvel",
    ],
    "Operação do escritório": [
        "Bens de diminuto valor", "Contribuição Sindical Patronal-Empresa", "Copa e Cozinha",
        "Cópias e Reproduções", "Emolumentos e taxas", "Higiene e Limpeza",
        "Livros, Jornais, revistas e TV", "Material de escritório", "Material de Limpeza",
        "Parque Gráfico (Tonners e Papéis)", "Telefone fixo/fax/internet", "Telefonia Móvel",
    ],
    "Gastos gerais": [
        "Adiantamento a Fornecedores", "Aluguel equipamentos", "Cartão de Crédito Corporativo",
        "Combustíveis e lubrificantes", "Conduções/Táxi", "Despesas de viagens", "Estacionamento",
        "Fundo Fixo de Caixa", "Lanches, refeições", "Manutenção de equipamentos",
        "Reembolso diversos", "Outros Impostos e Taxas", "Comissões", "Pedágio", "COFINS",
        "CSLL", "IRPJ", "PIS", "ISS",
    ],
    "Marketing": [
        "Anúncios", "Assessoria de Imprensa", "Brindes e Presentes", "Criação da Campanha",
        "Diversos Marketing", "Eventos", "Gráfica", "Mala Direta", "Marketing Institucional", "Site",
    ],
    "Serviços prof. Contratados": [
        "Assessoria e Consultoria", "Assistência Jurídica", "Auditoria", "Cartório",
        "Consultoria de Projetos", "Contabilidade", "Indenizações", "Custos Adicionais",
        "Despesas com financiamentos", "Documentos legais", "Guarda de Documentos", "Motoboy",
        "Pesquisa de Mercado", "Serviços de Terceiros", "Serviços de Terceiros - Personalização",
        "Serviços de Terceiros - PJ",
    ],
    "Despesas bancárias": [
        "Tarifas Bancárias Extras", "IOF", "Juros - Caixa Economica Federal", "Tarifas Bancárias",
    ],
    "Investimentos": [
        "Aquisição de Softwares", "Infraestrutura Digital (Site, Softwares Próprios)",
        "Infrastrutura de Hardware e Telefonia", "Manutenção de Hardware/Software - Informática",
        "Softwares", "Benfeitoria de Imóveis", "Equipamentos de Processamento de Dados",
        "Ferramentas", "Imóveis", "Instalações", "Máquinas aparelhos e Equipamentos",
        "Marcas Direitos e Patentes", "Móveis e Utensílios", "Terrenos", "Veículos",
    ],
}


def render_orcado_realizado():
    sb = get_supabase()

    ano = st.selectbox("Ano", [2025, 2026, 2027], index=1)

    orcado = _carregar_orcado_ano(sb, ano)
    realizado = _carregar_realizado_ano(sb, ano)

    if orcado.empty and realizado.empty:
        st.info(f"Nenhum dado de orçamento ou compromissos para {ano} ainda.")
        return

    orc_por_centro_mes = orcado.set_index(["centro_custo", orcado["mes"].dt.month])["valor_orcado"]
    rea_por_centro_mes = realizado.set_index(["centro_custo", realizado["mes"].dt.month])["valor"]

    def _valor(serie, centro, mes_num):
        try:
            return float(serie.loc[(centro, mes_num)])
        except KeyError:
            return 0.0

    linhas = []  # (label, é_grupo, orc_por_mes[12], rea_por_mes[12])
    orc_geral = [0.0] * 12
    rea_geral = [0.0] * 12

    for grupo, leaves in HIERARQUIA.items():
        orc_grupo = [0.0] * 12
        rea_grupo = [0.0] * 12
        leaf_rows = []
        for leaf in leaves:
            orc_leaf = [_valor(orc_por_centro_mes, leaf, m) for m in range(1, 13)]
            rea_leaf = [_valor(rea_por_centro_mes, leaf, m) for m in range(1, 13)]
            if any(orc_leaf) or any(rea_leaf):
                leaf_rows.append((leaf, False, orc_leaf, rea_leaf))
            for i in range(12):
                orc_grupo[i] += orc_leaf[i]
                rea_grupo[i] += rea_leaf[i]
        linhas.append((grupo.upper(), True, orc_grupo, rea_grupo))
        linhas.extend(leaf_rows)
        for i in range(12):
            orc_geral[i] += orc_grupo[i]
            rea_geral[i] += rea_grupo[i]

    linhas.append(("DESPESAS OPERACIONAIS (TOTAL)", True, orc_geral, rea_geral))

    dados = {}
    for m_idx, m in enumerate(MESES_PT):
        dados[(m, "Orçado")] = [l[2][m_idx] for l in linhas]
        dados[(m, "Realizado")] = [l[3][m_idx] for l in linhas]
        dados[(m, "%")] = [
            (l[3][m_idx] / l[2][m_idx]) if l[2][m_idx] else 0.0 for l in linhas
        ]
    dados[("Total Ano", "Previsto")] = [sum(l[2]) for l in linhas]
    dados[("Total Ano", "Realizado")] = [sum(l[3]) for l in linhas]

    labels = [l[0] for l in linhas]
    eh_grupo = [l[1] for l in linhas]

    ordem = [c for m in MESES_PT for c in [(m, "Orçado"), (m, "%"), (m, "Realizado")]]
    ordem += [("Total Ano", "Previsto"), ("Total Ano", "Realizado")]
    tabela = pd.DataFrame(dados, index=labels)[ordem]
    tabela.columns = pd.MultiIndex.from_tuples(ordem)

    formatos = {}
    for m in MESES_PT:
        formatos[(m, "Orçado")] = "R$ {:,.0f}"
        formatos[(m, "Realizado")] = "R$ {:,.0f}"
        formatos[(m, "%")] = "{:.0%}"
    formatos[("Total Ano", "Previsto")] = "R$ {:,.0f}"
    formatos[("Total Ano", "Realizado")] = "R$ {:,.0f}"

    def _destacar_grupos(row):
        eh = eh_grupo[tabela.index.get_loc(row.name)] if row.name in tabela.index else False
        return ["font-weight: bold; background-color: #2a2140" if eh else "" for _ in row]

    st.dataframe(
        tabela.style.format(formatos).apply(_destacar_grupos, axis=1),
        use_container_width=True,
        height=600,
    )


# ---------------------------------------------------------------
# Compromissos Diários -- aqui sim tem filtro, download e CRUD completo
# ---------------------------------------------------------------
COLUNAS_COMPROMISSO = [
    "id", "banco", "data_pagamento", "documento", "parcela", "emissao",
    "agente", "centro_custo", "valor", "impostos", "saldo", "historico", "mes",
]


def _carregar_compromissos(_sb, data_ini, data_fim, centro_sel) -> pd.DataFrame:
    query = (
        _sb.table("compromissos_diarios")
        .select("*")
        .gte("data_pagamento", str(data_ini))
        .lte("data_pagamento", str(data_fim))
        .order("data_pagamento")
    )
    if centro_sel != "Todos":
        query = query.eq("centro_custo", centro_sel)
    resp = query.execute()
    df = pd.DataFrame(resp.data)
    if df.empty:
        df = pd.DataFrame(columns=COLUNAS_COMPROMISSO)
    for col in COLUNAS_COMPROMISSO:
        if col not in df.columns:
            df[col] = None

    df = df[COLUNAS_COMPROMISSO].copy()
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
    for col in ("data_pagamento", "emissao", "mes"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ("valor", "impostos", "saldo"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("banco", "documento", "parcela", "agente", "centro_custo", "historico"):
        df[col] = df[col].astype("object").where(df[col].notna(), None)

    return df


def _gerar_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.drop(columns=["id"], errors="ignore").to_excel(writer, index=False, sheet_name="Compromissos")
    return buf.getvalue()


def _gerar_pdf(df: pd.DataFrame) -> bytes:
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=20, rightMargin=20, topMargin=30, bottomMargin=20)
    styles = getSampleStyleSheet()
    elementos = [Paragraph("<b>Pago Express — Compromissos Diários</b>", styles["Title"]), Spacer(1, 10)]

    colunas = ["data_pagamento", "banco", "agente", "centro_custo", "valor", "impostos", "saldo", "historico"]
    colunas = [c for c in colunas if c in df.columns]
    dados = [colunas]
    for _, linha in df.iterrows():
        dados.append([
            linha[c].strftime("%d/%m/%Y") if c == "data_pagamento" and pd.notna(linha[c]) else
            (f"{linha[c]:,.2f}" if c in ("valor", "impostos", "saldo") and pd.notna(linha[c]) else str(linha[c] or ""))
            for c in colunas
        ])

    tabela = Table(dados, repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6c3fa8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    elementos.append(tabela)
    doc.build(elementos)
    return buf.getvalue()


def render_compromissos_diarios():
    sb = get_supabase()

    col1, col2, col3 = st.columns(3)
    data_ini = col1.date_input("De", key="cd_ini", value=pd.Timestamp.today().replace(day=1))
    data_fim = col2.date_input("Até", key="cd_fim")
    centro_sel = col3.selectbox("Centro de Custo", ["Todos"] + CENTROS_CUSTO, key="cd_centro")

    df = _carregar_compromissos(sb, data_ini, data_fim, centro_sel)

    col_dl1, col_dl2 = st.columns(2)
    col_dl1.download_button(
        "⬇️ Baixar (Excel)",
        data=_gerar_excel(df),
        file_name="compromissos_diarios.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=df.empty,
    )
    col_dl2.download_button(
        "⬇️ Baixar (PDF)",
        data=_gerar_pdf(df) if not df.empty else b"",
        file_name="compromissos_diarios.pdf",
        mime="application/pdf",
        disabled=df.empty,
    )

    st.caption("Edite valores direto na tabela, use a última linha para incluir um novo compromisso, ou marque a caixinha e aperte Delete para excluir uma linha. Depois clique em Salvar.")

    editado = st.data_editor(
        df,
        key="editor_compromissos",
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "id": st.column_config.NumberColumn("id", disabled=True),
            "banco": st.column_config.SelectboxColumn("Banco", options=BANCOS),
            "data_pagamento": st.column_config.DateColumn("Data Pagamento"),
            "documento": st.column_config.TextColumn("Documento"),
            "parcela": st.column_config.TextColumn("Parcela"),
            "emissao": st.column_config.DateColumn("Emissão"),
            "agente": st.column_config.TextColumn("Agente"),
            "centro_custo": st.column_config.SelectboxColumn("Centro de Custo", options=CENTROS_CUSTO),
            "valor": st.column_config.NumberColumn("Valor", format="R$ %.2f"),
            "impostos": st.column_config.NumberColumn("Impostos", format="R$ %.2f"),
            "saldo": st.column_config.NumberColumn("Saldo", format="R$ %.2f"),
            "historico": st.column_config.TextColumn("Histórico"),
            "mes": st.column_config.DateColumn("Mês"),
        },
    )

    if st.button("💾 Salvar alterações"):
        ids_originais = set(df["id"].dropna().astype(int)) if not df.empty else set()
        ids_editados = set(editado["id"].dropna().astype(int)) if "id" in editado.columns else set()

        removidos = ids_originais - ids_editados
        for id_remover in removidos:
            sb.table("compromissos_diarios").delete().eq("id", int(id_remover)).execute()

        for _, linha in editado.iterrows():
            registro = linha.to_dict()
            id_linha = registro.pop("id", None)

            for campo in ("data_pagamento", "emissao", "mes"):
                if pd.notna(registro.get(campo)):
                    registro[campo] = pd.Timestamp(registro[campo]).strftime("%Y-%m-%d")
                else:
                    registro[campo] = None

            if not registro.get("centro_custo"):
                continue  # linha em branco, ignora

            if pd.notna(id_linha):
                sb.table("compromissos_diarios").update(registro).eq("id", int(id_linha)).execute()
            else:
                sb.table("compromissos_diarios").insert(registro).execute()

        st.success("Alterações salvas.")
        st.cache_data.clear()
        st.rerun()

    st.caption(f"{len(df)} lançamento(s) — soma: R$ {df['valor'].sum():,.2f}" if not df.empty else "")


def render_orcamento():
    aba_orcado, aba_compromissos = st.tabs(["Orçado e Realizado", "Compromissos Diários"])

    with aba_orcado:
        render_orcado_realizado()

    with aba_compromissos:
        render_compromissos_diarios()


if __name__ == "__main__":
    render_orcamento()
