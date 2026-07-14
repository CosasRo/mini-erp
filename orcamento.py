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
        return ["color: #f2a5a5" if v < 0 else ("color: #8fd6a5" if v > 0 else "") for v in col]

    st.dataframe(
        tabela.style.format({"Orçado": "R$ {:,.2f}", "Realizado": "R$ {:,.2f}", "Diferença": "R$ {:,.2f}", "%": "{:.1%}"})
        .apply(_cor_diferenca, subset=["Diferença"]),
        use_container_width=True,
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


def render_compromissos_diarios():
    sb = get_supabase()

    col1, col2, col3 = st.columns(3)
    data_ini = col1.date_input("De", key="cd_ini", value=pd.Timestamp.today().replace(day=1))
    data_fim = col2.date_input("Até", key="cd_fim")
    centro_sel = col3.selectbox("Centro de Custo", ["Todos"] + CENTROS_CUSTO, key="cd_centro")

    df = _carregar_compromissos(sb, data_ini, data_fim, centro_sel)

    st.download_button(
        "⬇️ Baixar (Excel)",
        data=_gerar_excel(df),
        file_name="compromissos_diarios.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
