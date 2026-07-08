"""
Mini ERP - Interface Web (Streamlit)
Upload, processamento e envio ao Supabase
"""

import io
import math
import pandas as pd
import streamlit as st
from supabase import create_client

from processor import (
    identificar_tipo, PROCESSADORES,
    processar_cashin, processar_cashout
)

st.set_page_config(
    page_title="Mini ERP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

@st.cache_resource
def get_supabase():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


def limpar_registro(registro):
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


def upload_supabase(df, tabela, chave, sb):
    BATCH_SIZE = 500
    registros = [limpar_registro(r) for r in df.to_dict("records")]
    total = len(registros)
    enviados = 0
    erros = 0
    progresso = st.progress(0)
    status_text = st.empty()

    for i in range(0, total, BATCH_SIZE):
        lote = registros[i:i + BATCH_SIZE]
        try:
            sb.table(tabela).upsert(lote, on_conflict=chave).execute()
            enviados += len(lote)
        except Exception as e:
            erros += len(lote)
            st.error(f"Erro no lote {i//BATCH_SIZE + 1}: {e}")
        progresso.progress(min(enviados / total, 1.0))
        status_text.text(f"Enviando... {enviados}/{total} registros")

    progresso.empty()
    status_text.empty()
    return enviados, erros


def carregar_pix_asaas(arquivo_pix):
    df = pd.read_csv(arquivo_pix, encoding="utf-8", sep=None, engine="python", dtype=str)
    df["Data"] = df["Data"].str.slice(0, 10).str.strip()
    for col in ["Valor", "Valor da taxa"]:
        df[col] = df[col].astype(str).str.replace(",", ".", regex=False)
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop_duplicates(subset="Identificador fim a fim", keep="last")
    taxa_lookup = dict(zip(df["Identificador fim a fim"].str.strip().str.upper(), df["Valor da taxa"]))
    valor_lookup = dict(zip(df["Identificador fim a fim"].str.strip().str.upper(), df["Valor"]))
    return taxa_lookup, valor_lookup


# SIDEBAR
with st.sidebar:
    st.image("https://img.icons8.com/color/96/combo-chart.png", width=60)
    st.title("Mini ERP")
    st.markdown("---")
    pagina = st.radio("Navegação", ["📤 Upload de Arquivos", "📊 Dashboard", "🔍 Consultar Dados"])
    st.markdown("---")
    st.caption("Versão 1.0 | Supabase + Streamlit")


# PÁGINA: UPLOAD
if pagina == "📤 Upload de Arquivos":
    st.title("📤 Upload de Arquivos")
    st.markdown("Faça o upload dos arquivos brutos. O sistema identifica automaticamente o tipo de cada arquivo, processa e envia ao banco de dados.")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Arquivos de Transações")
        arquivos = st.file_uploader(
            "Selecione os arquivos (.xlsx)",
            type=["xlsx"],
            accept_multiple_files=True,
            help="Você pode selecionar vários arquivos de uma vez: CASH-IN, CASH-OUT, PAGAMENTOS, CARTÃO"
        )

    with col2:
        st.subheader("Extrato PIX Asaas (opcional)")
        arquivo_pix = st.file_uploader(
            "Selecione o CSV do PIX Asaas",
            type=["csv"],
            help="Usado para validar tarifas e conciliação"
        )

    if arquivos:
        st.markdown("---")
        st.subheader("📋 Arquivos detectados")

        mapa = {}
        for arq in arquivos:
            tipo = identificar_tipo(arq)
            mapa[arq.name] = (arq, tipo)

        dados_tabela = []
        for nome, (arq, tipo) in mapa.items():
            emoji = {"cashin": "📥", "cashout": "📤", "pagamentos": "💳", "cartao": "💰"}.get(tipo, "❓")
            status = f"{emoji} {tipo.upper()}" if tipo else "❓ Não identificado"
            dados_tabela.append({"Arquivo": nome, "Tipo Identificado": status})
        st.dataframe(pd.DataFrame(dados_tabela), use_container_width=True, hide_index=True)

        nao_identificados = [n for n, (_, t) in mapa.items() if t is None]
        if nao_identificados:
            st.warning(f"⚠️ Arquivos não identificados: {', '.join(nao_identificados)}")

        st.markdown("---")

        if st.button("🚀 Processar e Enviar ao Banco", type="primary", use_container_width=True):
            sb = get_supabase()

            asaas_taxa_lookup = None
            asaas_valor_lookup = None
            if arquivo_pix:
                with st.spinner("Carregando extrato PIX Asaas..."):
                    asaas_taxa_lookup, asaas_valor_lookup = carregar_pix_asaas(arquivo_pix)
                st.success(f"✅ Extrato PIX Asaas carregado: {len(asaas_taxa_lookup)} transações")

            resultados = []
            for nome, (arq, tipo) in mapa.items():
                if tipo is None:
                    continue

                with st.expander(f"📄 Processando: {nome}", expanded=True):
                    try:
                        with st.spinner("Processando..."):
                            if tipo == "cashin":
                                df, tabela, chave = processar_cashin(arq, asaas_taxa_lookup, asaas_valor_lookup)
                            elif tipo == "cashout":
                                df, tabela, chave = processar_cashout(arq, asaas_taxa_lookup, asaas_valor_lookup)
                            else:
                                df, tabela, chave = PROCESSADORES[tipo](arq)

                        st.success(f"✅ {len(df)} linhas processadas")
                        st.dataframe(df.head(5), use_container_width=True)

                        st.write("Enviando ao Supabase...")
                        enviados, erros = upload_supabase(df, tabela, chave, sb)

                        if erros == 0:
                            st.success(f"✅ {enviados} registros enviados com sucesso!")
                        else:
                            st.warning(f"⚠️ {enviados} enviados | {erros} erros")

                        resultados.append({"arquivo": nome, "tipo": tipo, "linhas": len(df), "enviados": enviados, "erros": erros})

                    except Exception as e:
                        st.error(f"❌ Erro ao processar {nome}: {e}")

            if resultados:
                st.markdown("---")
                st.subheader("✅ Resumo do processamento")
                st.dataframe(pd.DataFrame(resultados), use_container_width=True, hide_index=True)


# PÁGINA: DASHBOARD
elif pagina == "📊 Dashboard":
    st.title("📊 Dashboard")
    sb = get_supabase()

    try:
        # Busca todos os campos de uma vez e filtra em Python
        col1, col2, col3, col4 = st.columns(4)

        res_ci = sb.table("cashin").select("FEE,STATUS").execute()
        df_ci = pd.DataFrame(res_ci.data)
        df_ci["FEE"] = pd.to_numeric(df_ci["FEE"], errors="coerce")
        df_ci_proc = df_ci[df_ci["STATUS"] == "PROCESSED"]

        with col1:
            st.metric("💰 CASH-IN Transações", f"{len(df_ci_proc):,}")
        with col2:
            st.metric("💰 CASH-IN Receita (FEE)", f"R$ {df_ci_proc['FEE'].sum():,.2f}")

        res_co = sb.table("cashout").select("COMMISSION,STATUS").execute()
        df_co = pd.DataFrame(res_co.data)
        df_co["COMMISSION"] = pd.to_numeric(df_co["COMMISSION"], errors="coerce")
        df_co_proc = df_co[df_co["STATUS"] == "SUCCESSFULLY PROCESSED"]

        with col3:
            st.metric("💸 CASH-OUT Transações", f"{len(df_co_proc):,}")
        with col4:
            st.metric("💸 CASH-OUT Receita", f"R$ {df_co_proc['COMMISSION'].sum():,.2f}")

    except Exception as e:
        st.error(f"Erro ao carregar métricas: {e}")

    st.markdown("---")

    try:
        st.subheader("CASH-IN por Status")
        df_status = pd.DataFrame(sb.table("cashin").select("STATUS").execute().data)
        contagem = df_status["STATUS"].value_counts().reset_index()
        contagem.columns = ["Status", "Quantidade"]
        st.bar_chart(contagem.set_index("Status"))
    except Exception as e:
        st.error(f"Erro no gráfico: {e}")

    try:
        st.subheader("CASH-OUT por Status")
        df_status2 = pd.DataFrame(sb.table("cashout").select("STATUS").execute().data)
        contagem2 = df_status2["STATUS"].value_counts().reset_index()
        contagem2.columns = ["Status", "Quantidade"]
        st.bar_chart(contagem2.set_index("Status"))
    except Exception as e:
        st.error(f"Erro no gráfico: {e}")


# PÁGINA: CONSULTAR DADOS
elif pagina == "🔍 Consultar Dados":
    st.title("🔍 Consultar Dados")
    sb = get_supabase()

    col1, col2 = st.columns(2)
    with col1:
        tipo = st.selectbox("Selecione a tabela", ["cashin", "cashout", "pagamentos", "cartao"])
    with col2:
        limite = st.slider("Quantidade de registros", 10, 1000, 100)

    if st.button("🔍 Consultar"):
        try:
            res = sb.table(tipo).select("*").limit(limite).execute()
            df = pd.DataFrame(res.data)
            st.success(f"{len(df)} registros encontrados")
            st.dataframe(df, use_container_width=True)

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
                df.to_excel(writer, index=False)
            st.download_button(
                "📥 Baixar Excel",
                buffer.getvalue(),
                file_name=f"{tipo}_consulta.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        except Exception as e:
            st.error(f"Erro: {e}")
