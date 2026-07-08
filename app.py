"""
Pago Express - Mini ERP
Sistema de Gestão Financeira
"""

import io
import math
import hashlib
import pandas as pd
import streamlit as st
from supabase import create_client

from processor import (
    identificar_tipo, PROCESSADORES,
    processar_cashin, processar_cashout
)

# ============================================================
# CONFIGURAÇÃO
# ============================================================
st.set_page_config(
    page_title="ERP Pago Express",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded"
)

ROXO_ESCURO  = "#3A2D58"
ROXO_MEDIO   = "#594A92"
AMARELO      = "#ECBD42"
CINZA_CLARO  = "#C9C7C1"
BRANCO       = "#FFFFFF"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] {{ font-family: 'Poppins', sans-serif; }}
    [data-testid="stSidebar"] {{ background-color: {ROXO_ESCURO} !important; }}
    [data-testid="stSidebar"] * {{ color: {BRANCO} !important; }}
    .stButton > button[kind="primary"] {{
        background-color: {AMARELO} !important;
        color: {ROXO_ESCURO} !important;
        font-weight: 600 !important;
        border: none !important;
        border-radius: 8px !important;
    }}
    .stButton > button {{ border-radius: 8px !important; }}
    [data-testid="metric-container"] {{
        background-color: {ROXO_ESCURO}22;
        border: 1px solid {ROXO_MEDIO}44;
        border-radius: 10px;
        padding: 12px;
    }}
    .divider {{ height: 3px; background: linear-gradient(90deg, {ROXO_MEDIO}, {AMARELO}); border-radius: 2px; margin: 16px 0; }}
</style>
""", unsafe_allow_html=True)


# ============================================================
# SUPABASE
# ============================================================
@st.cache_resource
def get_supabase():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


def hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()


# ============================================================
# FUNÇÕES DE USUÁRIO (Supabase)
# ============================================================
def buscar_usuario(sb, usuario):
    try:
        res = sb.table("usuarios").select("*").eq("usuario", usuario.lower()).eq("ativo", True).execute()
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return None


def listar_usuarios(sb):
    try:
        res = sb.table("usuarios").select("id,usuario,nome,email,perfil,ativo,created_at").order("nome").execute()
        return res.data
    except Exception:
        return []


def criar_usuario(sb, usuario, nome, email, senha, perfil):
    try:
        sb.table("usuarios").insert({
            "usuario": usuario.lower(),
            "nome": nome,
            "email": email,
            "senha_hash": hash_senha(senha),
            "perfil": perfil,
            "ativo": True
        }).execute()
        return True, "Usuário criado com sucesso!"
    except Exception as e:
        return False, str(e)


def atualizar_usuario(sb, user_id, nome, email, perfil, nova_senha=None):
    try:
        dados = {"nome": nome, "email": email, "perfil": perfil}
        if nova_senha:
            dados["senha_hash"] = hash_senha(nova_senha)
        sb.table("usuarios").update(dados).eq("id", user_id).execute()
        return True, "Usuário atualizado com sucesso!"
    except Exception as e:
        return False, str(e)


def desativar_usuario(sb, user_id):
    try:
        sb.table("usuarios").update({"ativo": False}).eq("id", user_id).execute()
        return True, "Usuário desativado com sucesso!"
    except Exception as e:
        return False, str(e)


def reativar_usuario(sb, user_id):
    try:
        sb.table("usuarios").update({"ativo": True}).eq("id", user_id).execute()
        return True, "Usuário reativado com sucesso!"
    except Exception as e:
        return False, str(e)


# ============================================================
# TELA DE LOGIN
# ============================================================
def tela_login():
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown(f"""
        <div style="text-align:center; margin-bottom: 32px;">
            <div style="background:{ROXO_ESCURO}; border-radius:20px; padding:32px 40px; box-shadow:0 8px 32px rgba(58,45,88,0.4);">
                <div style="margin:8px 0;"><img src="https://raw.githubusercontent.com/CosasRo/mini-erp/main/PG-RGBLogo%20Horizontal%20Padr%C3%A3o%20%402x.png" width="180" style="filter: brightness(0) invert(1);"/></div>
                <div style="color:{AMARELO}; font-size:1.8rem; font-weight:700;">Pago Express</div>
                <div style="color:{CINZA_CLARO}; font-size:0.85rem; margin-top:4px;">Sistema de Gestão Financeira</div>
                <div style="height:3px; background:linear-gradient(90deg,{ROXO_MEDIO},{AMARELO}); border-radius:2px; margin:20px 0;"></div>
                <div style="color:{BRANCO}; font-size:1rem; font-weight:500;">Acesso ao Sistema</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        sb = get_supabase()
        usuario = st.text_input("👤 Usuário", placeholder="Digite seu usuário")
        senha = st.text_input("🔒 Senha", type="password", placeholder="Digite sua senha")

        if st.button("Entrar →", type="primary", use_container_width=True):
            if usuario and senha:
                user = buscar_usuario(sb, usuario)
                if user and user["senha_hash"] == hash_senha(senha):
                    st.session_state["logado"] = True
                    st.session_state["user_id"] = user["id"]
                    st.session_state["usuario"] = user["usuario"]
                    st.session_state["nome"] = user["nome"]
                    st.session_state["email"] = user.get("email", "")
                    st.session_state["perfil"] = user["perfil"]
                    st.rerun()
                else:
                    st.error("❌ Usuário ou senha incorretos")
            else:
                st.warning("⚠️ Preencha todos os campos")

        st.markdown(f'<div style="text-align:center; margin-top:24px; color:{CINZA_CLARO}; font-size:0.75rem;">© 2024 Pago Express. Todos os direitos reservados.</div>', unsafe_allow_html=True)


# ============================================================
# HELPERS
# ============================================================
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


def logout():
    for key in ["logado", "user_id", "usuario", "nome", "email", "perfil"]:
        st.session_state.pop(key, None)
    st.rerun()


# ============================================================
# APP PRINCIPAL
# ============================================================

def registrar_log(sb, arquivo, tipo, total, enviados, erros, usuario):
    """Registra o resultado do upload no log_uploads."""
    if erros == 0:
        status = "aceito"
    elif enviados == 0:
        status = "negado"
    else:
        status = "processando"
    try:
        sb.table("log_uploads").insert({
            "arquivo": arquivo,
            "tipo": tipo,
            "total": total,
            "enviados": enviados,
            "erros": erros,
            "status": status,
            "usuario": usuario
        }).execute()
    except Exception as e:
        pass  # Log silencioso


def mostrar_historico_uploads(sb):
    """Mostra o histórico dos últimos uploads com badge colorido."""
    try:
        res = sb.table("log_uploads").select("*").order("data_upload", desc=True).limit(20).execute()
        logs = res.data
        if not logs:
            st.info("Nenhum upload registrado ainda.")
            return

        STATUS_CONFIG = {
            "aceito":      {"emoji": "🟢", "cor": "#1a7a3a", "fundo": "#d4edda", "texto": "Aceito"},
            "processando": {"emoji": "🟡", "cor": "#856404", "fundo": "#fff3cd", "texto": "Em Processamento"},
            "negado":      {"emoji": "🔴", "cor": "#721c24", "fundo": "#f8d7da", "texto": "Negado"},
        }

        TIPO_EMOJI = {
            "cashin": "📥", "cashout": "📤",
            "pagamentos": "💳", "cartao": "💰"
        }

        for log in logs:
            cfg = STATUS_CONFIG.get(log.get("status", "processando"), STATUS_CONFIG["processando"])
            tipo_emoji = TIPO_EMOJI.get(log.get("tipo", ""), "📄")
            data_str = log.get("data_upload", "")[:16].replace("T", " ") if log.get("data_upload") else ""
            arquivo = log.get("arquivo", "")
            tipo = log.get("tipo", "").upper()
            total = log.get("total", 0)
            enviados = log.get("enviados", 0)
            erros = log.get("erros", 0)
            usuario = log.get("usuario", "")

            st.markdown(f"""
            <div style="
                display: flex;
                align-items: center;
                justify-content: space-between;
                background: #1e1e2e;
                border: 1px solid #3A2D58;
                border-left: 4px solid {cfg['cor']};
                border-radius: 8px;
                padding: 10px 16px;
                margin-bottom: 8px;
            ">
                <div style="display:flex; align-items:center; gap:12px;">
                    <span style="font-size:1.3rem;">{tipo_emoji}</span>
                    <div>
                        <div style="color:#FFFFFF; font-size:0.85rem; font-weight:600;">{arquivo}</div>
                        <div style="color:#C9C7C1; font-size:0.75rem;">{data_str} &nbsp;|&nbsp; {tipo} &nbsp;|&nbsp; {total} linhas &nbsp;|&nbsp; por {usuario}</div>
                    </div>
                </div>
                <div style="
                    background: {cfg['fundo']};
                    color: {cfg['cor']};
                    border-radius: 20px;
                    padding: 4px 14px;
                    font-size: 0.75rem;
                    font-weight: 600;
                    white-space: nowrap;
                ">{cfg['emoji']} {cfg['texto']}</div>
            </div>
            """, unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Erro ao carregar histórico: {e}")

def app_principal():
    sb = get_supabase()
    nome = st.session_state.get("nome", "Usuário")
    email = st.session_state.get("email", "")
    perfil = st.session_state.get("perfil", "usuario")

    # SIDEBAR
    with st.sidebar:
        st.markdown(f"""
        <div style="text-align:center; padding:16px 0 8px 0;">
            <div style="margin:4px 0;"><img src="https://raw.githubusercontent.com/CosasRo/mini-erp/main/PG-RGBLogo%20Horizontal%20Padr%C3%A3o%20%402x.png" width="140" style="filter: brightness(0) invert(1);"/></div>
            <div style="color:{AMARELO}; font-size:1.1rem; font-weight:700;">Pago Express</div>
            <div style="color:{CINZA_CLARO}; font-size:0.7rem;">Sistema de Gestão</div>
        </div>
        <div style="height:2px; background:linear-gradient(90deg,{ROXO_MEDIO},{AMARELO}); margin:12px 0 20px 0; border-radius:2px;"></div>
        <div style="background:{ROXO_MEDIO}44; border-radius:8px; padding:10px 12px; margin-bottom:20px;">
            <div style="color:{AMARELO}; font-size:0.75rem;">Bem-vindo,</div>
            <div style="color:{BRANCO}; font-size:0.9rem; font-weight:600;">{nome}</div>
            <div style="color:{CINZA_CLARO}; font-size:0.7rem;">{email}</div>
            <div style="color:{CINZA_CLARO}; font-size:0.65rem; text-transform:uppercase; margin-top:2px;">{perfil}</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("**📋 MENU**")
        paginas = ["📊 Dashboard", "📤 Upload de Arquivos", "🔍 Consultar Dados"]
        if perfil == "admin":
            paginas.append("👥 Usuários")

        pagina = st.radio("", paginas, label_visibility="collapsed")
        st.markdown("<br>" * 6, unsafe_allow_html=True)
        st.markdown(f'<div style="color:{CINZA_CLARO}; font-size:0.7rem;">Versão 1.0</div>', unsafe_allow_html=True)
        if st.button("🚪 Sair", use_container_width=True):
            logout()

    # ============================================================
    # DASHBOARD
    # ============================================================
    if pagina == "📊 Dashboard":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">📊 Dashboard</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        try:
            col1, col2, col3, col4 = st.columns(4)
            res_ci = sb.table("cashin").select("FEE,STATUS").execute()
            df_ci = pd.DataFrame(res_ci.data)
            df_ci["FEE"] = pd.to_numeric(df_ci["FEE"], errors="coerce")
            df_ci_proc = df_ci[df_ci["STATUS"] == "PROCESSED"]
            with col1:
                st.metric("📥 CASH-IN Transações", f"{len(df_ci_proc):,}")
            with col2:
                st.metric("💰 CASH-IN Receita", f"R$ {df_ci_proc['FEE'].sum():,.2f}")

            res_co = sb.table("cashout").select("COMMISSION,STATUS").execute()
            df_co = pd.DataFrame(res_co.data)
            df_co["COMMISSION"] = pd.to_numeric(df_co["COMMISSION"], errors="coerce")
            df_co_proc = df_co[df_co["STATUS"] == "SUCCESSFULLY PROCESSED"]
            with col3:
                st.metric("📤 CASH-OUT Transações", f"{len(df_co_proc):,}")
            with col4:
                st.metric("💸 CASH-OUT Receita", f"R$ {df_co_proc['COMMISSION'].sum():,.2f}")
        except Exception as e:
            st.error(f"Erro ao carregar métricas: {e}")

        st.markdown("<br>", unsafe_allow_html=True)
        col_a, col_b = st.columns(2)
        with col_a:
            try:
                st.markdown(f'<h3 style="color:{ROXO_ESCURO};">CASH-IN por Status</h3>', unsafe_allow_html=True)
                df_s = pd.DataFrame(sb.table("cashin").select("STATUS").execute().data)
                c = df_s["STATUS"].value_counts().reset_index()
                c.columns = ["Status", "Quantidade"]
                st.bar_chart(c.set_index("Status"), color=ROXO_MEDIO)
            except Exception as e:
                st.error(f"Erro: {e}")
        with col_b:
            try:
                st.markdown(f'<h3 style="color:{ROXO_ESCURO};">CASH-OUT por Status</h3>', unsafe_allow_html=True)
                df_s2 = pd.DataFrame(sb.table("cashout").select("STATUS").execute().data)
                c2 = df_s2["STATUS"].value_counts().reset_index()
                c2.columns = ["Status", "Quantidade"]
                st.bar_chart(c2.set_index("Status"), color=AMARELO)
            except Exception as e:
                st.error(f"Erro: {e}")

        try:
            st.markdown(f'<h3 style="color:{ROXO_ESCURO};">Top 10 Merchants (CASH-IN Processado)</h3>', unsafe_allow_html=True)
            res_m = sb.table("cashin").select("*").eq("STATUS", "PROCESSED").execute()
            df_m = pd.DataFrame(res_m.data)
            df_m["FEE"] = pd.to_numeric(df_m["FEE"], errors="coerce")
            merchant_col = next((c for c in df_m.columns if "MERCHANT" in c.upper() and "CODE" not in c.upper()), None)
            if merchant_col:
                top = df_m.groupby(merchant_col)["FEE"].sum().sort_values(ascending=False).head(10).reset_index()
                top.columns = ["Merchant", "Receita (FEE)"]
                top["Receita (FEE)"] = top["Receita (FEE)"].apply(lambda x: f"R$ {x:,.2f}")
                st.dataframe(top, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Erro: {e}")

    # ============================================================
    # UPLOAD
    # ============================================================
    elif pagina == "📤 Upload de Arquivos":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">📤 Upload de Arquivos</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader("Arquivos de Transações")
            arquivos = st.file_uploader("Selecione os arquivos (.xlsx)", type=["xlsx"], accept_multiple_files=True)
        with col2:
            st.subheader("Extrato PIX Asaas")
            arquivo_pix = st.file_uploader("Selecione o CSV do PIX Asaas", type=["csv"])

        if arquivos:
            st.markdown("---")
            mapa = {arq.name: (arq, identificar_tipo(arq)) for arq in arquivos}
            EMOJIS = {"cashin": "📥", "cashout": "📤", "pagamentos": "💳", "cartao": "💰"}
            dados = [{"Arquivo": n, "Tipo": f"{EMOJIS.get(t,'❓')} {t.upper() if t else 'Não identificado'}"} for n, (_, t) in mapa.items()]
            st.dataframe(pd.DataFrame(dados), use_container_width=True, hide_index=True)

            if st.button("🚀 Processar e Enviar ao Banco", type="primary", use_container_width=True):
                asaas_taxa_lookup = asaas_valor_lookup = None
                if arquivo_pix:
                    asaas_taxa_lookup, asaas_valor_lookup = carregar_pix_asaas(arquivo_pix)
                    st.success(f"✅ PIX Asaas: {len(asaas_taxa_lookup)} transações")

                resultados = []
                for nome_arq, (arq, tipo) in mapa.items():
                    if not tipo:
                        continue
                    with st.expander(f"📄 {nome_arq}", expanded=True):
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
                            enviados, erros = upload_supabase(df, tabela, chave, sb)
                            if erros == 0:
                                st.success(f"✅ {enviados} registros enviados!")
                            else:
                                st.warning(f"⚠️ {enviados} enviados | {erros} erros")
                            registrar_log(sb, nome_arq, tipo, len(df), enviados, erros, st.session_state.get("usuario", ""))
                        resultados.append({"Arquivo": nome_arq, "Tipo": tipo, "Linhas": len(df), "Enviados": enviados, "Erros": erros})
                        except Exception as e:
                            st.error(f"❌ Erro: {e}")

                if resultados:
                    st.markdown("---")
                    st.dataframe(pd.DataFrame(resultados), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("📋 Histórico de Uploads")
        mostrar_historico_uploads(sb)

    # ============================================================
    # CONSULTAR DADOS
    # ============================================================
    elif pagina == "🔍 Consultar Dados":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">🔍 Consultar Dados</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        col1, col2 = st.columns(2)
        with col1:
            tipo = st.selectbox("Tabela", ["cashin", "cashout", "pagamentos", "cartao"])
        with col2:
            limite = st.slider("Quantidade de registros", 10, 1000, 100)

        if st.button("🔍 Consultar", type="primary"):
            try:
                res = sb.table(tipo).select("*").limit(limite).execute()
                df = pd.DataFrame(res.data)
                st.success(f"✅ {len(df)} registros encontrados")
                st.dataframe(df, use_container_width=True)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
                    df.to_excel(writer, index=False)
                st.download_button("📥 Baixar Excel", buffer.getvalue(),
                    file_name=f"{tipo}_consulta.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception as e:
                st.error(f"Erro: {e}")

    # ============================================================
    # USUÁRIOS (somente admin)
    # ============================================================
    elif pagina == "👥 Usuários" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">👥 Gestão de Usuários</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        aba = st.tabs(["📋 Lista de Usuários", "➕ Novo Usuário"])

        # --- LISTA ---
        with aba[0]:
            usuarios = listar_usuarios(sb)
            if not usuarios:
                st.info("Nenhum usuário encontrado.")
            else:
                for u in usuarios:
                    with st.expander(f"{'🟢' if u['ativo'] else '🔴'} {u['nome']} (@{u['usuario']}) — {u['perfil'].upper()}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            novo_nome  = st.text_input("Nome", value=u["nome"], key=f"nome_{u['id']}")
                            novo_email = st.text_input("Email", value=u.get("email", ""), key=f"email_{u['id']}")
                        with col2:
                            novo_perfil = st.selectbox("Perfil", ["usuario", "admin"],
                                index=0 if u["perfil"] == "usuario" else 1, key=f"perfil_{u['id']}")
                            nova_senha = st.text_input("Nova senha (deixe vazio para manter)", type="password", key=f"senha_{u['id']}")

                        col_a, col_b, col_c = st.columns(3)
                        with col_a:
                            if st.button("💾 Salvar", key=f"save_{u['id']}", type="primary"):
                                ok, msg = atualizar_usuario(sb, u["id"], novo_nome, novo_email, novo_perfil, nova_senha or None)
                                if ok:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                        with col_b:
                            if u["ativo"]:
                                if st.button("🔴 Desativar", key=f"deact_{u['id']}"):
                                    ok, msg = desativar_usuario(sb, u["id"])
                                    if ok:
                                        st.success(msg)
                                        st.rerun()
                            else:
                                if st.button("🟢 Reativar", key=f"react_{u['id']}"):
                                    ok, msg = reativar_usuario(sb, u["id"])
                                    if ok:
                                        st.success(msg)
                                        st.rerun()
                        with col_c:
                            st.caption(f"Criado em: {u.get('created_at','')[:10]}")

        # --- NOVO USUÁRIO ---
        with aba[1]:
            st.subheader("➕ Criar Novo Usuário")
            col1, col2 = st.columns(2)
            with col1:
                n_usuario = st.text_input("Usuário (login)")
                n_nome    = st.text_input("Nome completo")
            with col2:
                n_email  = st.text_input("Email")
                n_perfil = st.selectbox("Perfil", ["usuario", "admin"])
            n_senha   = st.text_input("Senha", type="password")
            n_senha2  = st.text_input("Confirmar senha", type="password")

            if st.button("➕ Criar Usuário", type="primary"):
                if not all([n_usuario, n_nome, n_senha]):
                    st.warning("⚠️ Preencha pelo menos usuário, nome e senha")
                elif n_senha != n_senha2:
                    st.error("❌ As senhas não coincidem")
                else:
                    ok, msg = criar_usuario(sb, n_usuario, n_nome, n_email, n_senha, n_perfil)
                    if ok:
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ Erro: {msg}")


# ============================================================
# CONTROLE DE SESSÃO
# ============================================================
if "logado" not in st.session_state:
    st.session_state["logado"] = False

if not st.session_state["logado"]:
    tela_login()
else:
    app_principal()
