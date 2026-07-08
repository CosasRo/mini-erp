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

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Mini ERP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# CONEXÃO SUPABASE
# ============================================================
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
    """Faz upsert dos dados no Supabase em lotes."""
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
    """Carrega o extrato PIX Asaas e retorna os lookups."""
    df = pd.read_csv(arquivo_pix, encoding="utf-8", sep=None, engine="python", dtype=str)
    df["Data"] = df["Data"].str.slice(0, 10).str.strip()
    for col in ["Valor", "Valor da taxa"]:
        df[col] = df[col].astype(str).str.replace(",", ".", regex=False)
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop_duplicates(subset="Identificador fim a fim", keep="last")
    taxa_lookup = dict(zip(df["Identificador fim a fim"].str.strip().str.upper(), df["Valor da taxa"]))
    valor_lookup = dict(zip(df["Identificador fim a fim"].str.strip().str.upper(), df["Valor"]))
    return taxa_lookup, valor_lookup


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.image("https://img.icons8.com/color/96/combo-chart.png", width=60)
    st.title("Mini ERP")
    st.markdown("---")
    pagina = st.radio("Navegação", ["📤 Upload de Arquivos", "📊 Dashboard", "🔍 Consultar Dados"])
    st.markdown("---")
    st.caption("Versão 1.0 | Supabase + Streamlit")


# ============================================================
# PÁGINA: UPLOAD
# ============================================================
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

        # Identifica tipos
        mapa = {}
        for arq in arquivos:
            tipo = identificar_tipo(arq)
            mapa[arq.name] = (arq, tipo)

        # Mostra tabela de identificação
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

            # Carrega lookups do PIX Asaas se fornecido
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
                            # Processa conforme o tipo
                            if tipo == "cashin":
                                df, tabela, chave = processar_cashin(arq, asaas_taxa_lookup, asaas_valor_lookup)
                            elif tipo == "cashout":
                                df, tabela, chave = processar_cashout(arq, asaas_taxa_lookup, asaas_valor_lookup)
                            else:
                                df, tabela, chave = PROCESSADORES[tipo](arq)

                        st.success(f"✅ {len(df)} linhas processadas")

                        # Preview
                        st.dataframe(df.head(5), use_container_width=True)

                        # Upload
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


# ============================================================
# PÁGINA: DASHBOARD
# ============================================================
elif pagina == "📊 Dashboard":
    st.title("📊 Dashboard")
    sb = get_supabase()

    col1, col2, col3, col4 = st.columns(4)

    try:
        # CASH-IN
        res = sb.table("cashin").select("AMOUNT,FEE,STATUS").eq("STATUS", "PROCESSED").execute()
        df_ci = pd.DataFrame(res.data)
        res_lucro = sb.table("cashin").select("LUCRO FINAL").eq("STATUS", "PROCESSED").execute()
        df_lucro_ci = pd.DataFrame(res_lucro.data)
        with col1:
            st.metric("💰 CASH-IN Receita (FEE)", f"R$ {pd.to_numeric(df_ci['FEE'], errors='coerce').sum():,.2f}")
        with col2:
            lucro_ci = pd.to_numeric(df_lucro_ci.get("LUCRO FINAL", pd.Series()), errors='coerce').sum() if not df_lucro_ci.empty else 0
            st.metric("📈 CASH-IN Lucro Final", f"R$ {lucro_ci:,.2f}")

        # CASH-OUT
        res2 = sb.table("cashout").select("COMMISSION,STATUS").eq("STATUS", "SUCCESSFULLY PROCESSED").execute()
        df_co = pd.DataFrame(res2.data)
        res_lucro2 = sb.table("cashout").select("LUCRO FINAL").eq("STATUS", "SUCCESSFULLY PROCESSED").execute()
        df_lucro_co = pd.DataFrame(res_lucro2.data)
        with col3:
            st.metric("💸 CASH-OUT Receita", f"R$ {pd.to_numeric(df_co['COMMISSION'], errors='coerce').sum():,.2f}")
        with col4:
            lucro_co = pd.to_numeric(df_lucro_co.get("LUCRO FINAL", pd.Series()), errors='coerce').sum() if not df_lucro_co.empty else 0
            st.metric("📈 CASH-OUT Lucro Final", f"R$ {lucro_co:,.2f}")

    except Exception as e:
        st.error(f"Erro ao carregar dashboard: {e}")

    st.markdown("---")

    # Gráfico por status CASH-IN
    try:
        st.subheader("CASH-IN por Status")
        res3 = sb.table("cashin").select("STATUS").execute()
        df_status = pd.DataFrame(res3.data)["STATUS"].value_counts().reset_index()
        df_status.columns = ["Status", "Quantidade"]
        st.bar_chart(df_status.set_index("Status"))
    except Exception as e:
        st.error(f"Erro: {e}")


# ============================================================
# PÁGINA: CONSULTAR DADOS
# ============================================================
elif pagina == "🔍 Consultar Dados":
    st.title("🔍 Consultar Dados")
    sb = get_supabase()

    tipo = st.selectbox("Selecione a tabela", ["cashin", "cashout", "pagamentos", "cartao"])
    limite = st.slider("Quantidade de registros", 10, 1000, 100)

    if st.button("🔍 Consultar"):
        try:
            res = sb.table(tipo).select("*").limit(limite).execute()
            df = pd.DataFrame(res.data)
            st.success(f"{len(df)} registros encontrados")
            st.dataframe(df, use_container_width=True)

            # Download
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
