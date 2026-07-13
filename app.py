"""
Pago Express - ERP
Sistema de Gestão Financeira
"""

import io
import math
import hashlib
import pandas as pd
import streamlit as st
from datetime import datetime, date
from collections import defaultdict
from supabase import create_client

from processor import (
    identificar_tipo, PROCESSADORES,
    processar_cashin, processar_cashout,
    processar_pagamentos, processar_cartao,
    identificar_extrato,
    processar_extrato_asaas, processar_bb_pix, processar_bb_adm,
    processar_clientes, formatar_cpf_cnpj
)

st.set_page_config(page_title="ERP Pago Express", page_icon="💳", layout="wide", initial_sidebar_state="expanded")

ROXO_ESCURO = "#3A2D58"
ROXO_MEDIO  = "#594A92"
AMARELO     = "#ECBD42"
CINZA_CLARO = "#C9C7C1"
BRANCO      = "#FFFFFF"
LOGO_URL    = "https://raw.githubusercontent.com/CosasRo/mini-erp/main/PG-RGBLogo%20Horizontal%20Padr%C3%A3o%20%402x.png"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] {{ font-family: 'Poppins', sans-serif; }}
    [data-testid="stSidebar"] {{ background-color: {ROXO_ESCURO} !important; }}
    [data-testid="stSidebar"] * {{ color: {BRANCO} !important; }}
    .stButton > button[kind="primary"] {{ background-color: {AMARELO} !important; color: {ROXO_ESCURO} !important; font-weight: 600 !important; border: none !important; border-radius: 8px !important; }}
    .stButton > button {{ border-radius: 8px !important; }}
    [data-testid="metric-container"] {{ background-color: {ROXO_ESCURO}22; border: 1px solid {ROXO_MEDIO}44; border-radius: 10px; padding: 12px; }}
    .divider {{ height: 3px; background: linear-gradient(90deg, {ROXO_MEDIO}, {AMARELO}); border-radius: 2px; margin: 16px 0; }}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_supabase():
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])


@st.cache_data(ttl=300)
def carregar_params(_sb):
    """Carrega parâmetros da Base Relat. do Supabase com cache de 5 minutos."""
    params = {}

    bancos_data = _sb.table("bancos").select("*").eq("ativo", True).execute().data
    params["bancos"] = {
        b["nome_banco"].strip().upper(): {
            "cod_banco":  b["cod_banco"],
            "tarifa_in":  float(b["tarifa_in"] or 0),
            "tarifa_out": float(b["tarifa_out"] or 0),
        }
        for b in bancos_data
    }

    impostos_data = _sb.table("impostos").select("*").eq("ativo", True).execute().data
    params["impostos"] = {i["nome"]: float(i["aliquota"] or 0) for i in impostos_data}
    params["aliquota_total"] = sum(params["impostos"].values())

    cp_data = _sb.table("comissionados_plataforma").select("*").eq("ativo", True).execute().data
    valor_por_comercial = {}
    for r in cp_data:
        c = r["comercial"].strip().upper()
        if c not in valor_por_comercial:
            valor_por_comercial[c] = float(r["valor_fixo"] or 0)
    params["valor_por_comercial"] = valor_por_comercial

    cc_data = _sb.table("comissionados_comercial").select("*").eq("ativo", True).execute().data
    params["comissionados_comercial"] = {
        r["merchant"].strip().upper(): {
            "comercial":  r["comercial"],
            "percentual": float(r["percentual"] or 0),
        }
        for r in cc_data
    }

    baas_data = _sb.table("baas").select("merchant").eq("ativo", True).execute().data
    params["baas"] = {r["merchant"].strip().upper() for r in baas_data}

    return params

def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()

def buscar_usuario(sb, usuario):
    try:
        res = sb.table("usuarios").select("*").eq("usuario", usuario.lower()).eq("ativo", True).execute()
        return res.data[0] if res.data else None
    except: return None

def listar_usuarios(sb):
    try: return sb.table("usuarios").select("id,usuario,nome,email,perfil,ativo,created_at").order("nome").execute().data
    except: return []

def criar_usuario(sb, usuario, nome, email, senha, perfil):
    try:
        sb.table("usuarios").insert({"usuario": usuario.lower(), "nome": nome, "email": email, "senha_hash": hash_senha(senha), "perfil": perfil, "ativo": True}).execute()
        return True, "Usuário criado com sucesso!"
    except Exception as e: return False, str(e)

def atualizar_usuario(sb, uid, nome, email, perfil, nova_senha=None):
    try:
        dados = {"nome": nome, "email": email, "perfil": perfil}
        if nova_senha: dados["senha_hash"] = hash_senha(nova_senha)
        sb.table("usuarios").update(dados).eq("id", uid).execute()
        return True, "Usuário atualizado!"
    except Exception as e: return False, str(e)

def desativar_usuario(sb, uid):
    try: sb.table("usuarios").update({"ativo": False}).eq("id", uid).execute(); return True, "Desativado!"
    except Exception as e: return False, str(e)

def reativar_usuario(sb, uid):
    try: sb.table("usuarios").update({"ativo": True}).eq("id", uid).execute(); return True, "Reativado!"
    except Exception as e: return False, str(e)

def registrar_log(sb, arquivo, tipo, total, enviados, erros, usuario, categoria="transacao"):
    status = "aceito" if erros == 0 else ("negado" if enviados == 0 else "processando")
    try:
        sb.table("log_uploads").insert({"arquivo": arquivo, "tipo": tipo, "total": total, "enviados": enviados, "erros": erros, "status": status, "usuario": usuario, "categoria": categoria}).execute()
    except: pass

def limpar_registro(r):
    return {k: (None if v is None or (isinstance(v, float) and math.isnan(v)) else (v.item() if hasattr(v, "item") else v)) for k, v in r.items()}

def upload_supabase(df, tabela, chave, sb):
    BATCH = 500
    recs = [limpar_registro(r) for r in df.to_dict("records")]
    total = len(recs); enviados = 0; erros = 0
    prog = st.progress(0); txt = st.empty()
    for i in range(0, total, BATCH):
        lote = recs[i:i+BATCH]
        try:
            if chave:
                sb.table(tabela).upsert(lote, on_conflict=chave).execute()
            else:
                sb.table(tabela).insert(lote).execute()
            enviados += len(lote)
        except Exception as e:
            erros += len(lote); st.error(f"Erro lote {i//BATCH+1}: {e}")
        prog.progress(min(enviados/total, 1.0)); txt.text(f"Enviando... {enviados}/{total}")
    prog.empty(); txt.empty()
    return enviados, erros

def carregar_pix_asaas(arquivo_pix):
    df = pd.read_csv(arquivo_pix, encoding="utf-8", sep=None, engine="python", dtype=str)
    df["Data"] = df["Data"].str.slice(0, 10).str.strip()
    for col in ["Valor", "Valor da taxa"]:
        df[col] = df[col].astype(str).str.replace(",", ".", regex=False)
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop_duplicates(subset="Identificador fim a fim", keep="last")
    df["ARQUIVO_ORIGEM"] = arquivo_pix.name
    taxa = dict(zip(df["Identificador fim a fim"].str.strip().str.upper(), df["Valor da taxa"]))
    valor = dict(zip(df["Identificador fim a fim"].str.strip().str.upper(), df["Valor"]))
    return taxa, valor, df

def logout():
    for k in ["logado","user_id","usuario","nome","email","perfil"]: st.session_state.pop(k, None)
    st.rerun()

def gerar_pdf_historico(df_logs):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=30, rightMargin=30, topMargin=40, bottomMargin=30)
    styles = getSampleStyleSheet()
    elements = [Paragraph("<b>Pago Express — Histórico de Uploads</b>", styles["Title"]), Spacer(1, 12)]
    data = [["Data/Hora", "Arquivo", "Tipo", "Enviados", "Erros", "Status", "Usuário"]]
    for _, row in df_logs.iterrows():
        data.append([str(row.get("data_upload",""))[:16].replace("T"," "), str(row.get("arquivo",""))[:40],
            str(row.get("tipo","")).upper(), str(row.get("enviados",0)), str(row.get("erros",0)),
            str(row.get("status","")).upper(), str(row.get("usuario",""))])
    t = Table(data, colWidths=[90,160,60,50,40,70,60])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#3A2D58")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTSIZE",(0,0),(-1,0),8), ("FONTSIZE",(0,1),(-1,-1),7),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f5f5f5")]),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
    ]))
    elements.append(t)
    doc.build(elements)
    buf.seek(0)
    return buf.read()

def mostrar_historico_uploads(sb):
    try:
        res = sb.table("log_uploads").select("*").order("data_upload", desc=True).limit(200).execute()
        logs = res.data
        if not logs:
            st.info("Nenhum upload registrado ainda.")
            return

        df_logs = pd.DataFrame(logs)
        df_logs["data_date"] = pd.to_datetime(df_logs["data_upload"], errors="coerce").dt.date

        # FILTROS
        st.markdown("#### 🔎 Filtros")
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            data_min = df_logs["data_date"].min() or date.today()
            data_max = df_logs["data_date"].max() or date.today()
            data_ini = st.date_input("De", value=data_min, key="hist_ini")
            data_fim = st.date_input("Até", value=data_max, key="hist_fim")
        with col_f2:
            tipos_disp = ["Todos"] + sorted(df_logs["tipo"].dropna().unique().tolist())
            tipo_sel = st.selectbox("Tipo de arquivo", tipos_disp, key="hist_tipo")
        with col_f3:
            status_sel = st.selectbox("Status", ["Todos", "aceito", "processando", "negado"], key="hist_status")

        mask = (df_logs["data_date"] >= data_ini) & (df_logs["data_date"] <= data_fim)
        if tipo_sel != "Todos": mask &= df_logs["tipo"] == tipo_sel
        if status_sel != "Todos": mask &= df_logs["status"] == status_sel
        df_filtrado = df_logs[mask].copy()

        # DOWNLOADS
        st.markdown("---")
        col_d1, col_d2, col_d3 = st.columns([2, 1, 1])
        with col_d1:
            st.markdown(f"**{len(df_filtrado)} registro(s) encontrado(s)**")
        with col_d2:
            buf_xl = io.BytesIO()
            with pd.ExcelWriter(buf_xl, engine="xlsxwriter") as w:
                df_filtrado.drop(columns=["data_date"], errors="ignore").to_excel(w, index=False, sheet_name="Histórico")
            st.download_button("📥 Excel", buf_xl.getvalue(), file_name="historico_uploads.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        with col_d3:
            try:
                pdf = gerar_pdf_historico(df_filtrado.drop(columns=["data_date"], errors="ignore"))
                st.download_button("📄 PDF", pdf, file_name="historico_uploads.pdf", mime="application/pdf", use_container_width=True)
            except Exception:
                st.info("PDF indisponível")

        st.markdown("---")
        if df_filtrado.empty:
            st.info("Nenhum registro encontrado com os filtros selecionados.")
            return

        STATUS_CONFIG = {
            "aceito":      {"cor": "#1a7a3a", "fundo": "#d4edda", "emoji": "🟢", "texto": "Aceito"},
            "processando": {"cor": "#856404", "fundo": "#fff3cd", "emoji": "🟡", "texto": "Em Processamento"},
            "negado":      {"cor": "#721c24", "fundo": "#f8d7da", "emoji": "🔴", "texto": "Negado"},
        }
        TIPO_EMOJI = {"cashin": "📥", "cashout": "📤", "pagamentos": "💳", "cartao": "💰", "pix_asaas": "📊", "extrato_asaas": "📋", "bb_pix": "🏦", "bb_adm": "🏦", "clientes": "👤"}

        def card_log(log):
            cfg = STATUS_CONFIG.get(log.get("status","processando"), STATUS_CONFIG["processando"])
            te = TIPO_EMOJI.get(log.get("tipo",""), "📄")
            ds = str(log.get("data_upload",""))[:16].replace("T"," ")
            return f"""<div style="display:flex;align-items:center;justify-content:space-between;background:#1e1e2e;border:1px solid #3A2D58;border-left:4px solid {cfg['cor']};border-radius:8px;padding:8px 12px;margin-bottom:6px;">
                <div style="display:flex;align-items:center;gap:10px;overflow:hidden;">
                    <span style="font-size:1.1rem;flex-shrink:0;">{te}</span>
                    <div style="overflow:hidden;">
                        <div style="color:#FFF;font-size:0.78rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{log.get('arquivo','')}</div>
                        <div style="color:#C9C7C1;font-size:0.68rem;">{ds} | {str(log.get('tipo','')).upper()} | {log.get('enviados',0)} linhas | {log.get('usuario','')}</div>
                    </div>
                </div>
                <div style="background:{cfg['fundo']};color:{cfg['cor']};border-radius:20px;padding:3px 10px;font-size:0.7rem;font-weight:600;white-space:nowrap;margin-left:8px;">{cfg['emoji']} {cfg['texto']}</div>
            </div>"""

        por_data = defaultdict(lambda: {"transacao": [], "pix_asaas": [], "extrato": [], "clientes": []})
        for _, row in df_filtrado.iterrows():
            data = str(row.get("data_upload",""))[:10]
            cat = row.get("categoria","transacao")
            if cat not in ("transacao", "pix_asaas", "extrato", "clientes"):
                cat = "transacao"
            por_data[data][cat].append(row.to_dict())

        for data, grupos in sorted(por_data.items(), reverse=True):
            try: data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")
            except: data_fmt = data
            st.markdown(f'<div style="color:{AMARELO};font-size:0.9rem;font-weight:600;margin:16px 0 8px 0;">📅 {data_fmt}</div>', unsafe_allow_html=True)
            col_t, col_p, col_e, col_c = st.columns(4)
            with col_t:
                st.markdown('<div style="color:#C9C7C1;font-size:0.75rem;margin-bottom:6px;">📁 Transações</div>', unsafe_allow_html=True)
                if grupos["transacao"]:
                    for log in grupos["transacao"]: st.markdown(card_log(log), unsafe_allow_html=True)
                else:
                    st.markdown('<div style="color:#555;font-size:0.75rem;padding:8px;">— Nenhum</div>', unsafe_allow_html=True)
            with col_p:
                st.markdown('<div style="color:#C9C7C1;font-size:0.75rem;margin-bottom:6px;">📊 PIX Asaas</div>', unsafe_allow_html=True)
                if grupos["pix_asaas"]:
                    for log in grupos["pix_asaas"]: st.markdown(card_log(log), unsafe_allow_html=True)
                else:
                    st.markdown('<div style="color:#555;font-size:0.75rem;padding:8px;">— Nenhum</div>', unsafe_allow_html=True)
            with col_e:
                st.markdown('<div style="color:#C9C7C1;font-size:0.75rem;margin-bottom:6px;">🏦 Extratos Bancários</div>', unsafe_allow_html=True)
                if grupos["extrato"]:
                    for log in grupos["extrato"]: st.markdown(card_log(log), unsafe_allow_html=True)
                else:
                    st.markdown('<div style="color:#555;font-size:0.75rem;padding:8px;">— Nenhum</div>', unsafe_allow_html=True)
            with col_c:
                st.markdown('<div style="color:#C9C7C1;font-size:0.75rem;margin-bottom:6px;">👤 Clientes</div>', unsafe_allow_html=True)
                if grupos["clientes"]:
                    for log in grupos["clientes"]: st.markdown(card_log(log), unsafe_allow_html=True)
                else:
                    st.markdown('<div style="color:#555;font-size:0.75rem;padding:8px;">— Nenhum</div>', unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Erro ao carregar histórico: {e}")




def buscar_todos(sb, tabela, limite=100000):
    """Busca todos os registros com paginação (Supabase limita 1000 por request)."""
    todos = []
    pagina = 0
    TAM = 1000
    while pagina * TAM < limite:
        inicio = pagina * TAM
        try:
            res = sb.table(tabela).select("*").range(inicio, inicio + TAM - 1).execute()
            if not res.data:
                break
            todos.extend(res.data)
            if len(res.data) < TAM:
                break
            pagina += 1
        except Exception as e:
            st.warning(f"Erro na página {pagina}: {e}")
            break
    return todos

def gerar_pdf_consulta(df, tipo, cfg):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=20, rightMargin=20, topMargin=30, bottomMargin=20)
    styles = getSampleStyleSheet()
    elements = [Paragraph(f"<b>Pago Express — {tipo.upper()} — Consulta de Dados</b>", styles["Title"]), Spacer(1, 8)]

    # Seleciona colunas principais para o PDF
    colunas_pdf = [c for c in df.columns if c in [
        cfg.get("data_col"), cfg.get("merchant_col"), cfg.get("plat_col"),
        cfg.get("status_col"), cfg.get("valor"), "FEE", "AMOUNT", "COMMISSION",
        "BAAS/BOLSÃO", "LUCRO FINAL", "CONCILIAÇÃO", "ARQUIVO_ORIGEM"
    ] and c in df.columns][:8]

    if not colunas_pdf:
        colunas_pdf = list(df.columns)[:8]

    df_pdf = df[colunas_pdf].head(200)
    data = [colunas_pdf] + [[str(v)[:20] if v is not None else "" for v in row] for row in df_pdf.values.tolist()]

    col_w = 750 // len(colunas_pdf)
    t = Table(data, colWidths=[col_w] * len(colunas_pdf))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#3A2D58")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTSIZE", (0,0), (-1,0), 7), ("FONTSIZE", (0,1), (-1,-1), 6),
        ("GRID", (0,0), (-1,-1), 0.3, colors.grey),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ]))
    elements.append(t)
    if len(df) > 200:
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(f"<i>* PDF limitado a 200 registros. Total: {len(df)}. Use o Excel para exportação completa.</i>", styles["Normal"]))
    doc.build(elements)
    buf.seek(0)
    return buf.read()

def tela_login():
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown(f"""
        <div style="text-align:center;margin-bottom:32px;">
            <div style="background:{ROXO_ESCURO};border-radius:20px;padding:32px 40px;box-shadow:0 8px 32px rgba(58,45,88,0.4);">
                <div style="margin-bottom:8px;"><img src="{LOGO_URL}" width="180" style="filter:brightness(0) invert(1);"/></div>
                <div style="color:{CINZA_CLARO};font-size:0.85rem;margin-top:4px;">Sistema de Gestão Financeira</div>
                <div style="height:3px;background:linear-gradient(90deg,{ROXO_MEDIO},{AMARELO});border-radius:2px;margin:20px 0;"></div>
                <div style="color:{BRANCO};font-size:1rem;font-weight:500;">Acesso ao Sistema</div>
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
                    for k, v in [("logado",True),("user_id",user["id"]),("usuario",user["usuario"]),("nome",user["nome"]),("email",user.get("email","")),("perfil",user["perfil"])]:
                        st.session_state[k] = v
                    st.rerun()
                else: st.error("❌ Usuário ou senha incorretos")
            else: st.warning("⚠️ Preencha todos os campos")
        st.markdown(f'<div style="text-align:center;margin-top:24px;color:{CINZA_CLARO};font-size:0.75rem;">© 2024 Pago Express. Todos os direitos reservados.</div>', unsafe_allow_html=True)


def app_principal():
    sb = get_supabase()
    nome = st.session_state.get("nome","Usuário")
    email = st.session_state.get("email","")
    perfil = st.session_state.get("perfil","usuario")

    with st.sidebar:
        st.markdown(f"""
        <div style="text-align:center;padding:16px 0 8px 0;">
            <div style="margin:4px 0;"><img src="{LOGO_URL}" width="140" style="filter:brightness(0) invert(1);"/></div>
        </div>
        <div style="height:2px;background:linear-gradient(90deg,{ROXO_MEDIO},{AMARELO});margin:12px 0 20px 0;border-radius:2px;"></div>
        <div style="background:{ROXO_MEDIO}44;border-radius:8px;padding:10px 12px;margin-bottom:20px;">
            <div style="color:{AMARELO};font-size:0.75rem;">Bem-vindo,</div>
            <div style="color:{BRANCO};font-size:0.9rem;font-weight:600;">{nome}</div>
            <div style="color:{CINZA_CLARO};font-size:0.7rem;">{email}</div>
            <div style="color:{CINZA_CLARO};font-size:0.65rem;text-transform:uppercase;margin-top:2px;">{perfil}</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("**📋 MENU**")
        paginas = ["📊 Dashboard", "📤 Upload de Arquivos", "📈 Análise de Dados"]
        if perfil == "admin":
            paginas.append("👥 Usuários")
            paginas.append("⚙️ Configuração de Cálculos")
            paginas.append("🔧 Tela de Ajustes")
            paginas.append("🔍 Auditoria")
            paginas.append("🧾 Emissão de Nota Fiscal")
            paginas.append("⚖️ Jurídico / Legal")
            paginas.append("📒 Contábil / Tributário")
            paginas.append("👤 Cadastro de Clientes")
        pagina = st.radio("", paginas, label_visibility="collapsed")
        st.markdown("<br>" * 6, unsafe_allow_html=True)
        st.markdown(f'<div style="color:{CINZA_CLARO};font-size:0.7rem;">Versão 1.0</div>', unsafe_allow_html=True)
        if st.button("🚪 Sair", use_container_width=True): logout()

    # DASHBOARD
    if pagina == "📊 Dashboard":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">📊 Dashboard</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        try:
            col1, col2, col3, col4 = st.columns(4)
            df_ci = pd.DataFrame(sb.table("cashin").select("FEE,STATUS").execute().data)
            df_ci["FEE"] = pd.to_numeric(df_ci["FEE"], errors="coerce")
            df_ci_p = df_ci[df_ci["STATUS"] == "PROCESSED"]
            with col1: st.metric("📥 CASH-IN Transações", f"{len(df_ci_p):,}")
            with col2: st.metric("💰 CASH-IN Receita", f"R$ {df_ci_p['FEE'].sum():,.2f}")
            df_co = pd.DataFrame(sb.table("cashout").select("COMMISSION,STATUS").execute().data)
            df_co["COMMISSION"] = pd.to_numeric(df_co["COMMISSION"], errors="coerce")
            df_co_p = df_co[df_co["STATUS"] == "SUCCESSFULLY PROCESSED"]
            with col3: st.metric("📤 CASH-OUT Transações", f"{len(df_co_p):,}")
            with col4: st.metric("💸 CASH-OUT Receita", f"R$ {df_co_p['COMMISSION'].sum():,.2f}")
        except Exception as e: st.error(f"Erro: {e}")

        st.markdown("<br>", unsafe_allow_html=True)
        col_a, col_b = st.columns(2)
        with col_a:
            try:
                st.markdown(f'<h3 style="color:{ROXO_ESCURO};">CASH-IN por Status</h3>', unsafe_allow_html=True)
                df_s = pd.DataFrame(sb.table("cashin").select("STATUS").execute().data)
                c = df_s["STATUS"].value_counts().reset_index(); c.columns = ["Status","Quantidade"]
                st.bar_chart(c.set_index("Status"), color=ROXO_MEDIO)
            except Exception as e: st.error(f"Erro: {e}")
        with col_b:
            try:
                st.markdown(f'<h3 style="color:{ROXO_ESCURO};">CASH-OUT por Status</h3>', unsafe_allow_html=True)
                df_s2 = pd.DataFrame(sb.table("cashout").select("STATUS").execute().data)
                c2 = df_s2["STATUS"].value_counts().reset_index(); c2.columns = ["Status","Quantidade"]
                st.bar_chart(c2.set_index("Status"), color=AMARELO)
            except Exception as e: st.error(f"Erro: {e}")

        try:
            st.markdown(f'<h3 style="color:{ROXO_ESCURO};">Top 10 Merchants (CASH-IN Processado)</h3>', unsafe_allow_html=True)
            df_m = pd.DataFrame(sb.table("cashin").select("*").eq("STATUS","PROCESSED").execute().data)
            df_m["FEE"] = pd.to_numeric(df_m["FEE"], errors="coerce")
            mc = next((c for c in df_m.columns if "MERCHANT" in c.upper() and "CODE" not in c.upper()), None)
            if mc:
                top = df_m.groupby(mc)["FEE"].sum().sort_values(ascending=False).head(10).reset_index()
                top.columns = ["Merchant","Receita (FEE)"]
                top["Receita (FEE)"] = top["Receita (FEE)"].apply(lambda x: f"R$ {x:,.2f}")
                st.dataframe(top, use_container_width=True, hide_index=True)
        except Exception as e: st.error(f"Erro: {e}")

    # UPLOAD
    elif pagina == "📤 Upload de Arquivos":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">📤 Upload de Arquivos</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader("Arquivos de Transações")
            arquivos = st.file_uploader("Selecione os arquivos (.xlsx)", type=["xlsx"], accept_multiple_files=True)
        with col2:
            st.subheader("🏦 Extratos Bancários")
            st.caption("PIX Asaas (.csv) | Extrato Asaas, BB PIX 1160-6, BB ADM 1547-4 (.xlsx)")
            arquivo_pix = st.file_uploader("PIX Asaas (.csv)", type=["csv"], key="pix_csv")
            extratos_bancarios = st.file_uploader(
                "Extratos bancários (.xlsx)",
                type=["xlsx"],
                accept_multiple_files=True,
                key="extratos_bancarios"
            )

        mapa = {}
        if arquivos:
            st.markdown("---")
            mapa = {arq.name: (arq, identificar_tipo(arq)) for arq in arquivos}
            EMOJIS = {"cashin":"📥","cashout":"📤","pagamentos":"💳","cartao":"💰"}
            dados = [{"Arquivo": n, "Tipo": f"{EMOJIS.get(t,'❓')} {t.upper() if t else 'Não identificado'}"} for n,(_, t) in mapa.items()]
            st.dataframe(pd.DataFrame(dados), use_container_width=True, hide_index=True)

        if arquivos or arquivo_pix or extratos_bancarios:
            st.markdown("---")
            if st.button("🚀 Processar e Enviar ao Banco", type="primary", use_container_width=True):
                asaas_taxa_lookup = asaas_valor_lookup = df_pix = None

                if arquivo_pix:
                    with st.spinner("Processando extrato PIX Asaas..."):
                        asaas_taxa_lookup, asaas_valor_lookup, df_pix = carregar_pix_asaas(arquivo_pix)
                    with st.expander(f"📊 {arquivo_pix.name}", expanded=True):
                        st.success(f"✅ {len(df_pix)} transações PIX carregadas")
                        st.dataframe(df_pix.head(5), use_container_width=True)
                        st.write("Enviando ao Supabase...")
                        env_pix, err_pix = upload_supabase(df_pix, "pix_asaas", "Identificador fim a fim", sb)
                        if err_pix == 0: st.success(f"✅ {env_pix} registros PIX Asaas enviados!")
                        else: st.warning(f"⚠️ {env_pix} enviados | {err_pix} erros")
                        registrar_log(sb, arquivo_pix.name, "pix_asaas", len(df_pix), env_pix, err_pix, st.session_state.get("usuario",""), categoria="pix_asaas")

                resultados = []
                for nome_arq, (arq, tipo) in mapa.items():
                    if not tipo: continue
                    with st.expander(f"📄 {nome_arq}", expanded=True):
                        try:
                            with st.spinner("Processando..."):
                                params = carregar_params(sb)
                                if tipo == "cashin": df, tabela, chave = processar_cashin(arq, asaas_taxa_lookup, asaas_valor_lookup, params)
                                elif tipo == "cashout": df, tabela, chave = processar_cashout(arq, asaas_taxa_lookup, asaas_valor_lookup, params)
                                elif tipo == "pagamentos": df, tabela, chave = processar_pagamentos(arq, params)
                                elif tipo == "cartao": df, tabela, chave = processar_cartao(arq, params)
                                else: df, tabela, chave = PROCESSADORES[tipo](arq)
                            st.success(f"✅ {len(df)} linhas processadas")
                            st.dataframe(df.head(5), use_container_width=True)
                            enviados, erros = upload_supabase(df, tabela, chave, sb)
                            registrar_log(sb, nome_arq, tipo, len(df), enviados, erros, st.session_state.get("usuario",""))
                            if erros == 0: st.success(f"✅ {enviados} registros enviados!")
                            else: st.warning(f"⚠️ {enviados} enviados | {erros} erros")
                            resultados.append({"Arquivo": nome_arq, "Tipo": tipo, "Linhas": len(df), "Enviados": enviados, "Erros": erros})
                        except Exception as e:
                            st.error(f"❌ Erro: {e}")
                            registrar_log(sb, nome_arq, tipo or "desconhecido", 0, 0, 1, st.session_state.get("usuario",""))

                if resultados:
                    st.markdown("---")
                    st.dataframe(pd.DataFrame(resultados), use_container_width=True, hide_index=True)

                # Processa extratos bancários
                if extratos_bancarios:
                    st.markdown("---")
                    st.subheader("🏦 Processando Extratos Bancários")
                    EXTRATO_PROC = {
                        "extrato_asaas": processar_extrato_asaas,
                        "bb_pix":        processar_bb_pix,
                        "bb_adm":        processar_bb_adm,
                    }
                    EXTRATO_LABEL = {
                        "extrato_asaas": "📊 Extrato Asaas",
                        "bb_pix":        "🏦 BB PIX (1160-6)",
                        "bb_adm":        "🏦 BB ADM (1547-4)",
                    }
                    for ext_arq in extratos_bancarios:
                        tipo_ext = identificar_extrato(ext_arq)
                        label = EXTRATO_LABEL.get(tipo_ext, "❓ Não identificado")
                        with st.expander(f"{label} — {ext_arq.name}", expanded=True):
                            if not tipo_ext:
                                st.warning("⚠️ Não foi possível identificar o tipo do extrato. Verifique o nome do arquivo.")
                                continue
                            try:
                                with st.spinner("Processando..."):
                                    df_ext, tabela_ext, _ = EXTRATO_PROC[tipo_ext](ext_arq)
                                st.success(f"✅ {len(df_ext)} linhas processadas")
                                st.dataframe(df_ext.head(5), use_container_width=True)
                                env_ext, err_ext = upload_supabase(df_ext, tabela_ext, None, sb) if tabela_ext else (0, 0)
                                if err_ext == 0:
                                    st.success(f"✅ {env_ext} registros enviados!")
                                else:
                                    st.warning(f"⚠️ {env_ext} enviados | {err_ext} erros")
                                registrar_log(sb, ext_arq.name, tipo_ext, len(df_ext), env_ext, err_ext,
                                    st.session_state.get("usuario",""), categoria="extrato")
                            except Exception as e:
                                st.error(f"❌ Erro: {e}")
                                registrar_log(sb, ext_arq.name, tipo_ext or "extrato", 0, 0, 1,
                                    st.session_state.get("usuario",""), categoria="extrato")

        # Upload de clientes
        st.markdown("---")
        st.subheader("👤 Cadastro de Clientes")
        arquivo_clientes = st.file_uploader(
            "Selecione o arquivo de clientes (.xlsx)",
            type=["xlsx"],
            key="upload_clientes",
            help="Arquivo de clientes da PagoExpress — atualiza mensalmente"
        )

        if arquivo_clientes:
            if st.button("📥 Processar Clientes", type="primary", key="btn_clientes"):
                with st.expander(f"👤 {arquivo_clientes.name}", expanded=True):
                    try:
                        with st.spinner("Processando clientes..."):
                            df_cli, tabela_cli, chave_cli = processar_clientes(arquivo_clientes)
                        st.success(f"✅ {len(df_cli)} clientes processados")
                        st.dataframe(df_cli.head(5), use_container_width=True)
                        env_cli, err_cli = upload_supabase(df_cli, tabela_cli, chave_cli, sb)
                        if err_cli == 0:
                            st.success(f"✅ {env_cli} clientes enviados!")
                        else:
                            st.warning(f"⚠️ {env_cli} enviados | {err_cli} erros")
                        registrar_log(sb, arquivo_clientes.name, "clientes", len(df_cli), env_cli, err_cli,
                            st.session_state.get("usuario",""), categoria="clientes")
                    except Exception as e:
                        st.error(f"❌ Erro: {e}")

        st.markdown("---")
        st.subheader("📋 Histórico de Uploads")
        mostrar_historico_uploads(sb)

    # CONSULTAR
    elif pagina == "📈 Análise de Dados":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">🔍 Consultar Dados</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        TABELAS_CONFIG = {
            "cashin":        {"label": "📥 CASH-IN",         "valor": "FEE",        "status_col": "STATUS",   "plat_col": "PLATFORM NAME", "merchant_col": "MERCHANT NAME", "data_col": "PAYMENT TIME"},
            "cashout":       {"label": "📤 CASH-OUT",        "valor": "COMMISSION", "status_col": "STATUS",   "plat_col": "PLATFORM NAME", "merchant_col": "MERCHANT NAME", "data_col": "CREATION TIME"},
            "pagamentos":    {"label": "💳 PAGAMENTOS",      "valor": "FEE",        "status_col": "STATUS",   "plat_col": "PLATFORM NAME", "merchant_col": "FANTASY NAME",  "data_col": "PAYMENT DATE"},
            "cartao":        {"label": "💰 CARTÃO",          "valor": "FEE VALUE",  "status_col": "STATUS",   "plat_col": "PLATFORM NAME", "merchant_col": "MERCHANT NAME", "data_col": "CREATION TIME"},
            "pix_asaas":     {"label": "📊 PIX ASAAS",      "valor": "Valor",      "status_col": "Situação", "plat_col": None,            "merchant_col": None,            "data_col": "Data"},
            "extrato_asaas": {"label": "📋 EXTRATO ASAAS",  "valor": "Valor",      "status_col": None,       "plat_col": None,            "merchant_col": None,            "data_col": "Data"},
            "bb_pix":        {"label": "🏦 BB PIX (1160-6)", "valor": "Valor R$",   "status_col": None,       "plat_col": None,            "merchant_col": None,            "data_col": "Data"},
            "bb_adm":        {"label": "🏦 BB ADM (1547-4)", "valor": "Valor R$",   "status_col": None,       "plat_col": None,            "merchant_col": None,            "data_col": "Data"},
        }

        # Seleção da tabela
        col_t, col_l = st.columns([2, 1])
        with col_t:
            tipo_opts = {v["label"]: k for k, v in TABELAS_CONFIG.items()}
            tipo_label = st.selectbox("Tabela", list(tipo_opts.keys()))
            tipo = tipo_opts[tipo_label]
            cfg = TABELAS_CONFIG[tipo]
        with col_l:
            limite = st.number_input("Máx. registros", min_value=100, max_value=100000, value=1000, step=1000)

        # Carrega dados com paginação
        try:
            with st.spinner(f"Carregando dados (pode demorar para grandes volumes)..."):
                registros = buscar_todos(sb, tipo, limite)
                df_raw = pd.DataFrame(registros)
                st.caption(f"✅ {len(df_raw):,} registros carregados")
        except Exception as e:
            st.error(f"Erro ao carregar: {e}")
            df_raw = pd.DataFrame()

        if not df_raw.empty:
            st.markdown("---")
            st.markdown("#### 🔎 Filtros")

            col_f1, col_f2, col_f3, col_f4 = st.columns(4)

            # Filtro período
            with col_f1:
                data_col = cfg["data_col"]
                if data_col and data_col in df_raw.columns:
                    col_raw = df_raw[data_col].astype(str)
                    if col_raw.str.match(r"\d{2}/\d{2}/\d{4}").sum() > 0:
                        datas_validas = pd.to_datetime(col_raw, format="%d/%m/%Y", errors="coerce").dropna()
                    else:
                        datas_validas = pd.to_datetime(col_raw.str.slice(0,10), format="%Y-%m-%d", errors="coerce").dropna()
                    if not datas_validas.empty:
                        d_min = datas_validas.min().date()
                        d_max = datas_validas.max().date()
                        d_ini = st.date_input("De", value=d_min, key="cons_ini")
                        d_fim = st.date_input("Até", value=d_max, key="cons_fim")
                    else:
                        from datetime import date
                        d_ini = st.date_input("De", value=date.today(), key="cons_ini")
                        d_fim = st.date_input("Até", value=date.today(), key="cons_fim")
                else:
                    from datetime import date
                    d_ini = st.date_input("De", value=date.today(), key="cons_ini")
                    d_fim = st.date_input("Até", value=date.today(), key="cons_fim")

            # Filtro status
            with col_f2:
                status_col = cfg["status_col"]
                if status_col and status_col in df_raw.columns:
                    status_opts = ["Todos"] + sorted(df_raw[status_col].dropna().unique().tolist())
                    status_sel = st.selectbox("Status", status_opts, key="cons_status")
                else:
                    status_sel = "Todos"
                    st.selectbox("Status", ["N/A"], disabled=True, key="cons_status")

            # Filtro plataforma
            with col_f3:
                plat_col = cfg["plat_col"]
                if plat_col and plat_col in df_raw.columns:
                    plat_opts = ["Todos"] + sorted(df_raw[plat_col].dropna().unique().tolist())
                    plat_sel = st.selectbox("Plataforma", plat_opts, key="cons_plat")
                else:
                    plat_sel = "Todos"
                    st.selectbox("Plataforma", ["N/A"], disabled=True, key="cons_plat")

            # Filtro merchant
            with col_f4:
                merch_col = cfg["merchant_col"]
                if merch_col and merch_col in df_raw.columns:
                    merch_opts = ["Todos"] + sorted(df_raw[merch_col].dropna().unique().tolist())
                    merch_sel = st.selectbox("Merchant", merch_opts, key="cons_merch")
                else:
                    merch_sel = "Todos"
                    st.selectbox("Merchant", ["N/A"], disabled=True, key="cons_merch")

            # Filtros extras: CONCILIAÇÃO e BAAS/BOLSÃO
            col_f5, col_f6 = st.columns(2)
            with col_f5:
                if "CONCILIAÇÃO" in df_raw.columns:
                    conc_sel = st.selectbox("Conciliação", ["Todos", "CONCILIADO", "#N/D"], key="cons_conc")
                else:
                    conc_sel = "Todos"
            with col_f6:
                if "BAAS/BOLSÃO" in df_raw.columns:
                    baas_opts = ["Todos", "BAAS", "BOLSÃO"]
                    baas_sel = st.selectbox("BAAS / BOLSÃO", baas_opts, key="cons_baas")
                else:
                    baas_sel = "Todos"

            # Pesquisa por campo
            st.markdown("#### 🔍 Pesquisa por campo")
            col_p1, col_p2 = st.columns([1, 2])
            with col_p1:
                campos_busca = [c for c in df_raw.columns if "ID" in c.upper() or "NAME" in c.upper() or "DOC" in c.upper()]
                campo_sel = st.selectbox("Campo", ["— Nenhum —"] + campos_busca, key="cons_campo")
            with col_p2:
                termo_busca = st.text_input("Valor a buscar", placeholder="Ex: E18236120...", key="cons_termo")

            # Aplica filtros
            df = df_raw.copy()

            if d_ini and d_fim and data_col and data_col in df.columns:
                # Tenta formato DD/MM/AAAA primeiro, depois AAAA-MM-DD
                col_str = df[data_col].astype(str)
                # Detecta formato predominante
                if col_str.str.match(r"\d{2}/\d{2}/\d{4}").sum() > col_str.str.match(r"\d{4}-\d{2}-\d{2}").sum():
                    datas = pd.to_datetime(col_str, format="%d/%m/%Y", errors="coerce").dt.date
                else:
                    datas = pd.to_datetime(col_str.str.slice(0,10), format="%Y-%m-%d", errors="coerce").dt.date
                mask = (datas >= d_ini) & (datas <= d_fim)
                # Se nenhum registro passar, mostra tudo (evita tela vazia por problema de formato)
                if mask.sum() > 0:
                    df = df[mask]

            if status_sel != "Todos" and status_col in df.columns:
                df = df[df[status_col] == status_sel]

            if plat_sel != "Todos" and plat_col in df.columns:
                df = df[df[plat_col] == plat_sel]

            if merch_sel != "Todos" and merch_col in df.columns:
                df = df[df[merch_col] == merch_sel]

            if campo_sel != "— Nenhum —" and termo_busca and campo_sel in df.columns:
                df = df[df[campo_sel].astype(str).str.contains(termo_busca, case=False, na=False)]

            if conc_sel != "Todos" and "CONCILIAÇÃO" in df.columns:
                df = df[df["CONCILIAÇÃO"].astype(str).str.strip().str.upper() == conc_sel.strip().upper()]

            if baas_sel != "Todos" and "BAAS/BOLSÃO" in df.columns:
                df = df[df["BAAS/BOLSÃO"] == baas_sel]

            # Cruza com dados do Asaas (END TO END ID) para CASH-IN e CASH-OUT
            if tipo in ["cashin", "cashout"] and "END TO END ID" in df.columns and tipo != "pix_asaas":
                try:
                    # Busca todos os registros do pix_asaas com select(*)
                    res_pix = sb.table("pix_asaas").select("*").limit(100000).execute()
                    df_pix_banco = pd.DataFrame(res_pix.data)
                    if not df_pix_banco.empty:
                        # Identifica a coluna chave (tem espaços no nome)
                        chave_pix = next((c for c in df_pix_banco.columns if "identificador" in c.lower() and "fim" in c.lower()), None)
                        if chave_pix:
                            df_pix_banco = df_pix_banco.rename(columns={
                                chave_pix: "END TO END ID",
                                "Valor": "BANCO_VALOR",
                                "Valor da taxa": "BANCO_TAXA",
                                "Situação": "BANCO_SITUACAO",
                                "Tipo": "BANCO_TIPO",
                                "Data": "BANCO_DATA"
                            })
                            # Normaliza para match
                            df["END TO END ID"] = df["END TO END ID"].astype(str).str.strip().str.upper()
                            df_pix_banco["END TO END ID"] = df_pix_banco["END TO END ID"].astype(str).str.strip().str.upper()
                            cols_pix = [c for c in ["END TO END ID","BANCO_VALOR","BANCO_TAXA","BANCO_SITUACAO","BANCO_TIPO","BANCO_DATA"] if c in df_pix_banco.columns]
                            df = df.merge(df_pix_banco[cols_pix], on="END TO END ID", how="left")
                            n_conc = df["BANCO_VALOR"].notna().sum() if "BANCO_VALOR" in df.columns else 0
                            if n_conc > 0:
                                st.info(f"📊 {n_conc} transações cruzadas com extrato Asaas")
                            else:
                                st.warning("⚠️ Nenhuma transação cruzada — suba o arquivo PIX Asaas do mesmo período na aba Upload")
                except Exception as e:
                    st.warning(f"Extrato Asaas não disponível: {e}")

            st.markdown("---")

            # RESUMO
            valor_col = cfg["valor"]
            if valor_col in df.columns:
                df[valor_col] = pd.to_numeric(df[valor_col], errors="coerce")
                col_r1, col_r2, col_r3, col_r4 = st.columns(4)
                with col_r1: st.metric("📋 Registros", f"{len(df):,}")
                with col_r2: st.metric("💰 Total", f"R$ {df[valor_col].sum():,.2f}")
                with col_r3: st.metric("📊 Média", f"R$ {df[valor_col].mean():,.2f}" if len(df) > 0 else "R$ 0,00")
                with col_r4: st.metric("🔝 Máximo", f"R$ {df[valor_col].max():,.2f}" if len(df) > 0 else "R$ 0,00")
            else:
                st.metric("📋 Registros encontrados", f"{len(df):,}")

            st.markdown("---")

            # Downloads
            col_d1, col_d2, col_d3 = st.columns([2, 1, 1])
            with col_d1:
                st.markdown(f"**{len(df)} registro(s) encontrado(s)**")
            with col_d2:
                buf_xl = io.BytesIO()
                with pd.ExcelWriter(buf_xl, engine="xlsxwriter") as w:
                    df.to_excel(w, index=False, sheet_name=tipo)
                st.download_button("📥 Excel", buf_xl.getvalue(),
                    file_name=f"{tipo}_consulta.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)
            with col_d3:
                try:
                    pdf = gerar_pdf_consulta(df, tipo, cfg)
                    st.download_button("📄 PDF", pdf,
                        file_name=f"{tipo}_consulta.pdf",
                        mime="application/pdf",
                        use_container_width=True)
                except Exception:
                    st.info("PDF indisponível")

            # Tabela
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum dado encontrado.")

    # USUÁRIOS
    elif pagina == "👥 Usuários" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">👥 Gestão de Usuários</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        aba = st.tabs(["📋 Lista de Usuários", "➕ Novo Usuário"])

        with aba[0]:
            usuarios = listar_usuarios(sb)
            if not usuarios: st.info("Nenhum usuário.")
            else:
                for u in usuarios:
                    with st.expander(f"{'🟢' if u['ativo'] else '🔴'} {u['nome']} (@{u['usuario']}) — {u['perfil'].upper()}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            nn = st.text_input("Nome", value=u["nome"], key=f"n_{u['id']}")
                            ne = st.text_input("Email", value=u.get("email",""), key=f"e_{u['id']}")
                        with col2:
                            np = st.selectbox("Perfil", ["usuario","admin"], index=0 if u["perfil"]=="usuario" else 1, key=f"p_{u['id']}")
                            ns = st.text_input("Nova senha", type="password", key=f"s_{u['id']}")
                        ca, cb, cc = st.columns(3)
                        with ca:
                            if st.button("💾 Salvar", key=f"sv_{u['id']}", type="primary"):
                                ok, msg = atualizar_usuario(sb, u["id"], nn, ne, np, ns or None)
                                st.success(msg) if ok else st.error(msg)
                                if ok: st.rerun()
                        with cb:
                            if u["ativo"]:
                                if st.button("🔴 Desativar", key=f"da_{u['id']}"):
                                    ok, msg = desativar_usuario(sb, u["id"])
                                    if ok: st.rerun()
                            else:
                                if st.button("🟢 Reativar", key=f"ra_{u['id']}"):
                                    ok, msg = reativar_usuario(sb, u["id"])
                                    if ok: st.rerun()
                        with cc: st.caption(f"Criado: {u.get('created_at','')[:10]}")

        with aba[1]:
            st.subheader("➕ Criar Novo Usuário")
            c1, c2 = st.columns(2)
            with c1: nu = st.text_input("Usuário"); nn2 = st.text_input("Nome completo")
            with c2: ne2 = st.text_input("Email"); np2 = st.selectbox("Perfil", ["usuario","admin"])
            ns2 = st.text_input("Senha", type="password"); ns3 = st.text_input("Confirmar senha", type="password")
            if st.button("➕ Criar Usuário", type="primary"):
                if not all([nu, nn2, ns2]): st.warning("⚠️ Preencha usuário, nome e senha")
                elif ns2 != ns3: st.error("❌ As senhas não coincidem")
                else:
                    ok, msg = criar_usuario(sb, nu, nn2, ne2, ns2, np2)
                    st.success(f"✅ {msg}") if ok else st.error(f"❌ {msg}")
                    if ok: st.rerun()



    # BASE RELAT.
    elif pagina == "⚙️ Configuração de Cálculos" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">⚙️ Base Relat.</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        aba = st.tabs(["🏦 Bancos", "📊 Impostos", "🤝 Com. Plataforma", "💼 Com. Comercial", "⭐ BAAS"])

        # ── ABA 1: BANCOS ──────────────────────────────────────
        with aba[0]:
            st.subheader("🏦 Bancos e Tarifas")
            bancos = sb.table("bancos").select("*").order("nome_banco").execute().data
            for b in bancos:
                with st.expander(f"{'🟢' if b['ativo'] else '🔴'} {b['nome_banco']} (Cód: {b['cod_banco']})"):
                    c1, c2, c3 = st.columns(3)
                    with c1: ti = st.number_input("Tarifa IN (R$)", value=float(b["tarifa_in"] or 0), step=0.01, format="%.4f", key=f"ti_{b['id']}")
                    with c2: to = st.number_input("Tarifa OUT (R$)", value=float(b["tarifa_out"] or 0), step=0.01, format="%.4f", key=f"to_{b['id']}")
                    with c3: at = st.checkbox("Ativo", value=b["ativo"], key=f"at_{b['id']}")
                    ca, cb = st.columns(2)
                    with ca:
                        if st.button("💾 Salvar", key=f"sb_{b['id']}", type="primary"):
                            sb.table("bancos").update({"tarifa_in": ti, "tarifa_out": to, "ativo": at}).eq("id", b["id"]).execute()
                            st.success("✅ Salvo!"); st.rerun()
                    with cb:
                        if st.button("🗑️ Excluir", key=f"db_{b['id']}"):
                            sb.table("bancos").delete().eq("id", b["id"]).execute()
                            st.success("✅ Excluído!"); st.rerun()

            st.markdown("---")
            st.subheader("➕ Novo Banco")
            c1, c2, c3, c4 = st.columns(4)
            with c1: nb_cod  = st.text_input("Código", key="nb_cod")
            with c2: nb_nome = st.text_input("Nome do Banco", key="nb_nome")
            with c3: nb_ti   = st.number_input("Tarifa IN", value=0.0, step=0.01, format="%.4f", key="nb_ti")
            with c4: nb_to   = st.number_input("Tarifa OUT", value=0.0, step=0.01, format="%.4f", key="nb_to")
            if st.button("➕ Adicionar Banco", type="primary"):
                if nb_cod and nb_nome:
                    sb.table("bancos").insert({"cod_banco": nb_cod, "nome_banco": nb_nome.upper(), "tarifa_in": nb_ti, "tarifa_out": nb_to}).execute()
                    st.success("✅ Banco adicionado!"); st.rerun()
                else: st.warning("⚠️ Preencha código e nome")

        # ── ABA 2: IMPOSTOS ────────────────────────────────────
        with aba[1]:
            st.subheader("📊 Alíquotas de Impostos")
            impostos = sb.table("impostos").select("*").order("nome").execute().data
            total = sum(float(i["aliquota"] or 0) for i in impostos if i["ativo"])
            st.info(f"📊 Alíquota total atual: **{total*100:.4f}%**")
            for imp in impostos:
                with st.expander(f"{'🟢' if imp['ativo'] else '🔴'} {imp['nome']} — {float(imp['aliquota'] or 0)*100:.2f}%"):
                    c1, c2 = st.columns(2)
                    with c1: aliq = st.number_input("Alíquota (%)", value=float(imp["aliquota"] or 0)*100, step=0.01, format="%.4f", key=f"aliq_{imp['id']}")
                    with c2: at_i = st.checkbox("Ativo", value=imp["ativo"], key=f"ati_{imp['id']}")
                    ca, cb = st.columns(2)
                    with ca:
                        if st.button("💾 Salvar", key=f"si_{imp['id']}", type="primary"):
                            sb.table("impostos").update({"aliquota": aliq/100, "ativo": at_i}).eq("id", imp["id"]).execute()
                            st.success("✅ Salvo!"); st.rerun()
                    with cb:
                        if st.button("🗑️ Excluir", key=f"di_{imp['id']}"):
                            sb.table("impostos").delete().eq("id", imp["id"]).execute()
                            st.success("✅ Excluído!"); st.rerun()

            st.markdown("---")
            st.subheader("➕ Novo Imposto")
            c1, c2 = st.columns(2)
            with c1: ni_nome = st.text_input("Nome (ex: ISS)", key="ni_nome")
            with c2: ni_aliq = st.number_input("Alíquota (%)", value=0.0, step=0.01, format="%.4f", key="ni_aliq")
            if st.button("➕ Adicionar Imposto", type="primary"):
                if ni_nome:
                    sb.table("impostos").insert({"nome": ni_nome.upper(), "aliquota": ni_aliq/100}).execute()
                    st.success("✅ Imposto adicionado!"); st.rerun()
                else: st.warning("⚠️ Preencha o nome")

        # ── ABA 3: COMISSIONADOS PLATAFORMA ────────────────────
        with aba[2]:
            st.subheader("🤝 Comissionados Plataforma")

            # Filtro por comercial
            cp_data = sb.table("comissionados_plataforma").select("*").order("comercial").execute().data
            comerciais_cp = ["Todos"] + sorted(set(r["comercial"] for r in cp_data if r["comercial"]))
            col_f1, col_f2 = st.columns(2)
            with col_f1: cp_filtro = st.selectbox("Filtrar por Comercial", comerciais_cp, key="cp_filtro")
            with col_f2: cp_busca = st.text_input("Buscar merchant", placeholder="Digite parte do nome...", key="cp_busca")

            cp_filtrado = [r for r in cp_data
                if (cp_filtro == "Todos" or r["comercial"] == cp_filtro)
                and (not cp_busca or cp_busca.upper() in r["merchant"].upper())]

            st.caption(f"{len(cp_filtrado)} registros")

            # Edição em lote por comercial
            if cp_filtrado:
                df_cp = pd.DataFrame(cp_filtrado)[["id","merchant","comercial","valor_fixo","ativo"]]
                df_cp["valor_fixo"] = pd.to_numeric(df_cp["valor_fixo"], errors="coerce").fillna(0)

                edited = st.data_editor(
                    df_cp,
                    column_config={
                        "id": st.column_config.NumberColumn("ID", disabled=True),
                        "merchant": st.column_config.TextColumn("Merchant"),
                        "comercial": st.column_config.TextColumn("Comercial"),
                        "valor_fixo": st.column_config.NumberColumn("Valor Fixo R$", format="R$ %.2f"),
                        "ativo": st.column_config.CheckboxColumn("Ativo"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    key="editor_cp"
                )

                if st.button("💾 Salvar Alterações", type="primary", key="save_cp"):
                    for _, row in edited.iterrows():
                        sb.table("comissionados_plataforma").update({
                            "merchant": str(row["merchant"]).upper(),
                            "comercial": row["comercial"],
                            "valor_fixo": float(row["valor_fixo"]),
                            "ativo": bool(row["ativo"])
                        }).eq("id", int(row["id"])).execute()
                    st.success("✅ Alterações salvas!"); st.rerun()

                if st.button("🗑️ Excluir Selecionados", key="del_cp"):
                    ids = edited[~edited["ativo"]]["id"].tolist()
                    for uid in ids:
                        sb.table("comissionados_plataforma").delete().eq("id", int(uid)).execute()
                    st.success(f"✅ {len(ids)} excluídos!"); st.rerun()

            st.markdown("---")
            st.subheader("➕ Novo Comissionado Plataforma")
            c1, c2, c3 = st.columns(3)
            with c1: ncp_m = st.text_input("Merchant", key="ncp_m")
            with c2: ncp_c = st.text_input("Comercial", key="ncp_c")
            with c3: ncp_v = st.number_input("Valor Fixo R$", value=0.0, step=0.01, format="%.2f", key="ncp_v")
            if st.button("➕ Adicionar", type="primary", key="add_cp"):
                if ncp_m and ncp_c:
                    sb.table("comissionados_plataforma").insert({"merchant": ncp_m.upper(), "comercial": ncp_c.upper(), "valor_fixo": ncp_v}).execute()
                    st.success("✅ Adicionado!"); st.rerun()
                else: st.warning("⚠️ Preencha merchant e comercial")

        # ── ABA 4: COMISSIONADOS COMERCIAL ─────────────────────
        with aba[3]:
            st.subheader("💼 Comissionados Comercial")
            cc_data = sb.table("comissionados_comercial").select("*").order("comercial").execute().data

            if cc_data:
                df_cc = pd.DataFrame(cc_data)[["id","merchant","comercial","percentual","ativo"]]
                df_cc["percentual"] = pd.to_numeric(df_cc["percentual"], errors="coerce").fillna(0)

                edited_cc = st.data_editor(
                    df_cc,
                    column_config={
                        "id": st.column_config.NumberColumn("ID", disabled=True),
                        "merchant": st.column_config.TextColumn("Merchant"),
                        "comercial": st.column_config.TextColumn("Comercial"),
                        "percentual": st.column_config.NumberColumn("Percentual (%)", format="%.2f%%"),
                        "ativo": st.column_config.CheckboxColumn("Ativo"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    key="editor_cc"
                )

                if st.button("💾 Salvar Alterações", type="primary", key="save_cc"):
                    for _, row in edited_cc.iterrows():
                        sb.table("comissionados_comercial").update({
                            "merchant": str(row["merchant"]).upper(),
                            "comercial": row["comercial"],
                            "percentual": float(row["percentual"]),
                            "ativo": bool(row["ativo"])
                        }).eq("id", int(row["id"])).execute()
                    st.success("✅ Salvo!"); st.rerun()

            st.markdown("---")
            st.subheader("➕ Novo Comissionado Comercial")
            c1, c2, c3 = st.columns(3)
            with c1: ncc_m = st.text_input("Merchant", key="ncc_m")
            with c2: ncc_c = st.text_input("Comercial", key="ncc_c")
            with c3: ncc_p = st.number_input("Percentual (%)", value=0.0, step=0.01, format="%.2f", key="ncc_p")
            if st.button("➕ Adicionar", type="primary", key="add_cc"):
                if ncc_m and ncc_c:
                    sb.table("comissionados_comercial").insert({"merchant": ncc_m.upper(), "comercial": ncc_c.upper(), "percentual": ncc_p}).execute()
                    st.success("✅ Adicionado!"); st.rerun()
                else: st.warning("⚠️ Preencha merchant e comercial")

        # ── ABA 5: BAAS ────────────────────────────────────────
        with aba[4]:
            st.subheader("⭐ Lista BAAS")
            baas_data = sb.table("baas").select("*").order("merchant").execute().data

            baas_busca = st.text_input("Buscar merchant", placeholder="Digite parte do nome...", key="baas_busca")
            baas_filtrado = [r for r in baas_data if not baas_busca or baas_busca.upper() in r["merchant"].upper()]
            st.caption(f"{len(baas_filtrado)} merchants BAAS")

            if baas_filtrado:
                df_baas = pd.DataFrame(baas_filtrado)[["id","merchant","ativo"]]
                edited_baas = st.data_editor(
                    df_baas,
                    column_config={
                        "id": st.column_config.NumberColumn("ID", disabled=True),
                        "merchant": st.column_config.TextColumn("Merchant"),
                        "ativo": st.column_config.CheckboxColumn("Ativo"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    key="editor_baas"
                )

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("💾 Salvar Alterações", type="primary", key="save_baas"):
                        for _, row in edited_baas.iterrows():
                            sb.table("baas").update({
                                "merchant": str(row["merchant"]).upper(),
                                "ativo": bool(row["ativo"])
                            }).eq("id", int(row["id"])).execute()
                        st.success("✅ Salvo!"); st.rerun()
                with c2:
                    if st.button("🗑️ Excluir Inativos", key="del_baas"):
                        ids = edited_baas[~edited_baas["ativo"]]["id"].tolist()
                        for uid in ids:
                            sb.table("baas").delete().eq("id", int(uid)).execute()
                        st.success(f"✅ {len(ids)} excluídos!"); st.rerun()

            st.markdown("---")
            st.subheader("➕ Novo BAAS")
            c1, c2 = st.columns([3, 1])
            with c1: nb_m = st.text_input("Merchant", key="nb_m")
            with c2: st.markdown("<br>", unsafe_allow_html=True)
            if st.button("➕ Adicionar BAAS", type="primary", key="add_baas"):
                if nb_m:
                    try:
                        sb.table("baas").insert({"merchant": nb_m.upper()}).execute()
                        st.success("✅ Adicionado!"); st.rerun()
                    except: st.error("❌ Merchant já existe na lista")
                else: st.warning("⚠️ Preencha o merchant")



    # TELA DE AJUSTES
    elif pagina == "🔧 Tela de Ajustes" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">🔧 Tela de Ajustes</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        st.info("🚧 Em desenvolvimento — Esta tela mostrará as divergências e diferenças encontradas no processamento dos uploads (conciliação, valores que não bateram, #N/D, etc.)")

    # AUDITORIA
    elif pagina == "🔍 Auditoria" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">🔍 Auditoria</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        st.info("🚧 Em desenvolvimento — Registro completo de todas as ações realizadas no sistema.")

    # EMISSÃO DE NOTA FISCAL
    elif pagina == "🧾 Emissão de Nota Fiscal" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">🧾 Emissão de Nota Fiscal</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        st.info("🚧 Em desenvolvimento — Emissão e controle de notas fiscais.")

    # JURÍDICO / LEGAL
    elif pagina == "⚖️ Jurídico / Legal" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">⚖️ Jurídico / Legal</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        aba_jur = st.tabs(["⚖️ Contencioso Cível", "📋 Consultivo Cível"])

        # ── ABA 1: CONTENCIOSO ─────────────────────────────────
        with aba_jur[0]:
            st.subheader("⚖️ Contencioso Cível")

            # Carrega processos
            try:
                proc_data = buscar_todos(sb, "juridico_contencioso", 10000)
                df_proc = pd.DataFrame(proc_data) if proc_data else pd.DataFrame()
            except Exception as e:
                st.error(f"Erro: {e}"); df_proc = pd.DataFrame()

            # MÉTRICAS
            if not df_proc.empty:
                df_proc["valor"] = pd.to_numeric(df_proc["valor"], errors="coerce").fillna(0)
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                with col_m1: st.metric("📋 Total Processos", f"{len(df_proc):,}")
                with col_m2: st.metric("💰 Valor Total", f"R$ {df_proc['valor'].sum():,.2f}")
                with col_m3: st.metric("⚠️ Possível", f"{(df_proc['possibilidade_perda']=='POSSÍVEL').sum()}")
                with col_m4: st.metric("✅ Ganhos", f"{(df_proc['possibilidade_perda']=='GANHO').sum()}")

                st.markdown("---")

                # FILTROS
                col_f1, col_f2, col_f3 = st.columns(3)
                with col_f1:
                    busca_p = st.text_input("🔍 Buscar por autor ou processo", key="jur_busca")
                with col_f2:
                    obj_opts = ["Todos"] + sorted(df_proc["objeto"].dropna().unique().tolist()) if "objeto" in df_proc.columns else ["Todos"]
                    obj_sel = st.selectbox("Objeto", obj_opts, key="jur_obj")
                with col_f3:
                    perda_opts = ["Todos", "POSSÍVEL", "REMOTO", "IMPROVÁVEL", "GANHO", "PERDIDO"]
                    perda_sel = st.selectbox("Possibilidade de Perda", perda_opts, key="jur_perda")

                df_p = df_proc.copy()
                if busca_p:
                    df_p = df_p[df_p["autor"].astype(str).str.contains(busca_p, case=False, na=False) |
                                df_p["processo"].astype(str).str.contains(busca_p, case=False, na=False)]
                if obj_sel != "Todos": df_p = df_p[df_p["objeto"] == obj_sel]
                if perda_sel != "Todos": df_p = df_p[df_p["possibilidade_perda"] == perda_sel]
                df_p = df_p.sort_values("autor", ignore_index=True)

                # Downloads
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
                        df_p.drop(columns=["id","created_at","updated_at"], errors="ignore").to_excel(w, index=False)
                    st.download_button("📥 Excel", buf.getvalue(), file_name="contencioso.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
                with col_d2:
                    try:
                        pdf_j = gerar_pdf_consulta(df_p, "contencioso", {"data_col":None,"merchant_col":"autor","plat_col":None,"status_col":"possibilidade_perda","valor":"valor"})
                        st.download_button("📄 PDF", pdf_j, file_name="contencioso.pdf", mime="application/pdf", use_container_width=True)
                    except: st.info("PDF indisponível")

                st.caption(f"{len(df_p)} processo(s)")

                # Tabela editável
                cols_show = ["autor","reu","processo","vara","objeto","valor","andamento","possibilidade_perda","escritorio"]
                cols_show = [c for c in cols_show if c in df_p.columns]
                edited_p = st.data_editor(
                    df_p[cols_show],
                    use_container_width=True, hide_index=True, key="editor_proc",
                    column_config={
                        "autor":               st.column_config.TextColumn("Autor"),
                        "reu":                 st.column_config.TextColumn("Réu"),
                        "processo":            st.column_config.TextColumn("Processo"),
                        "vara":                st.column_config.TextColumn("Vara"),
                        "objeto":              st.column_config.TextColumn("Objeto"),
                        "valor":               st.column_config.NumberColumn("Valor R$", format="R$ %.2f"),
                        "andamento":           st.column_config.TextColumn("Andamento", width="large"),
                        "possibilidade_perda": st.column_config.SelectboxColumn("Possibilidade", options=["POSSÍVEL","REMOTO","IMPROVÁVEL","GANHO","PERDIDO"]),
                        "escritorio":          st.column_config.TextColumn("Escritório"),
                    }
                )

                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    if st.button("💾 Salvar Alterações", type="primary", key="save_proc", use_container_width=True):
                        for i, row in edited_p.iterrows():
                            proc_id = df_p.iloc[i]["id"]
                            sb.table("juridico_contencioso").update({
                                "autor": row.get("autor",""), "reu": row.get("reu",""),
                                "processo": row.get("processo",""), "vara": row.get("vara",""),
                                "objeto": row.get("objeto",""), "valor": float(row.get("valor",0) or 0),
                                "andamento": row.get("andamento",""),
                                "possibilidade_perda": row.get("possibilidade_perda","POSSÍVEL"),
                                "escritorio": row.get("escritorio","")
                            }).eq("id", int(proc_id)).execute()
                        st.success("✅ Salvo!"); st.rerun()
                with col_s2:
                    if st.button("🗑️ Excluir Selecionados", key="del_proc", use_container_width=True):
                        st.warning("Selecione os processos e use o botão de exclusão.")

            st.markdown("---")
            st.subheader("➕ Novo Processo")
            c1, c2, c3 = st.columns(3)
            with c1:
                np_autor   = st.text_input("Autor", key="np_autor")
                np_reu     = st.text_input("Réu", value="Pago Express Intermediacao de Pagamentos Ltda", key="np_reu")
                np_proc    = st.text_input("Número do Processo", key="np_proc")
            with c2:
                np_vara    = st.text_input("Vara", key="np_vara")
                np_objeto  = st.text_input("Objeto", key="np_objeto")
                np_valor   = st.number_input("Valor R$", value=0.0, step=100.0, format="%.2f", key="np_valor")
            with c3:
                np_perda   = st.selectbox("Possibilidade de Perda", ["POSSÍVEL","REMOTO","IMPROVÁVEL","GANHO","PERDIDO"], key="np_perda")
                np_escrit  = st.text_input("Escritório", key="np_escrit")
                np_andamento = st.text_area("Andamento", key="np_andamento", height=100)

            if st.button("➕ Adicionar Processo", type="primary", key="add_proc"):
                if np_autor and np_proc:
                    try:
                        sb.table("juridico_contencioso").insert({
                            "autor": np_autor, "reu": np_reu, "processo": np_proc,
                            "vara": np_vara, "objeto": np_objeto, "valor": np_valor,
                            "andamento": np_andamento, "possibilidade_perda": np_perda,
                            "escritorio": np_escrit
                        }).execute()
                        st.success("✅ Processo adicionado!"); st.rerun()
                    except Exception as e: st.error(f"❌ Erro: {e}")
                else: st.warning("⚠️ Preencha pelo menos Autor e Número do Processo")

        # ── ABA 2: CONSULTIVO ──────────────────────────────────
        with aba_jur[1]:
            st.subheader("📋 Consultivo Cível — Marcas e Patentes")

            try:
                cons_data = buscar_todos(sb, "juridico_consultivo", 10000)
                df_cons = pd.DataFrame(cons_data) if cons_data else pd.DataFrame()
            except Exception as e:
                st.error(f"Erro: {e}"); df_cons = pd.DataFrame()

            if not df_cons.empty:
                # Downloads
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    buf2 = io.BytesIO()
                    with pd.ExcelWriter(buf2, engine="xlsxwriter") as w:
                        df_cons.drop(columns=["id","created_at","updated_at"], errors="ignore").to_excel(w, index=False)
                    st.download_button("📥 Excel", buf2.getvalue(), file_name="consultivo.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
                with col_d2:
                    try:
                        pdf_c = gerar_pdf_consulta(df_cons, "consultivo", {"data_col":"data_notificacao","merchant_col":"notificante","plat_col":None,"status_col":"status","valor":None})
                        st.download_button("📄 PDF", pdf_c, file_name="consultivo.pdf", mime="application/pdf", use_container_width=True)
                    except: st.info("PDF indisponível")

                st.caption(f"{len(df_cons)} notificação(ões)")
                cols_c = ["notificante","notificada","objeto","data_notificacao","escritorio","status","observacao"]
                cols_c = [c for c in cols_c if c in df_cons.columns]
                edited_c = st.data_editor(
                    df_cons[cols_c],
                    use_container_width=True, hide_index=True, key="editor_cons",
                    column_config={
                        "notificante":       st.column_config.TextColumn("Notificante"),
                        "notificada":        st.column_config.TextColumn("Notificada"),
                        "objeto":            st.column_config.TextColumn("Objeto"),
                        "data_notificacao":  st.column_config.TextColumn("Data"),
                        "escritorio":        st.column_config.TextColumn("Escritório"),
                        "status":            st.column_config.SelectboxColumn("Status", options=["ATIVO","ENCERRADO","AGUARDANDO"]),
                        "observacao":        st.column_config.TextColumn("Observação", width="large"),
                    }
                )
                if st.button("💾 Salvar Alterações", type="primary", key="save_cons", use_container_width=True):
                    for i, row in edited_c.iterrows():
                        cons_id = df_cons.iloc[i]["id"]
                        sb.table("juridico_consultivo").update({
                            "notificante": row.get("notificante",""), "notificada": row.get("notificada",""),
                            "objeto": row.get("objeto",""), "data_notificacao": row.get("data_notificacao",""),
                            "escritorio": row.get("escritorio",""), "status": row.get("status","ATIVO"),
                            "observacao": row.get("observacao","")
                        }).eq("id", int(cons_id)).execute()
                    st.success("✅ Salvo!"); st.rerun()

            st.markdown("---")
            st.subheader("➕ Nova Notificação")
            c1, c2 = st.columns(2)
            with c1:
                nc_not   = st.text_input("Notificante", key="nc_not")
                nc_notd  = st.text_input("Notificada", value="Pago Express Intermediacao de Pagamentos Ltda", key="nc_notd")
                nc_obj   = st.text_input("Objeto", key="nc_obj")
            with c2:
                nc_data  = st.text_input("Data (DD/MM/AAAA)", key="nc_data")
                nc_escr  = st.text_input("Escritório", key="nc_escr")
                nc_obs   = st.text_area("Observação", key="nc_obs", height=100)

            if st.button("➕ Adicionar Notificação", type="primary", key="add_cons"):
                if nc_not and nc_obj:
                    try:
                        sb.table("juridico_consultivo").insert({
                            "notificante": nc_not, "notificada": nc_notd,
                            "objeto": nc_obj, "data_notificacao": nc_data,
                            "escritorio": nc_escr, "observacao": nc_obs
                        }).execute()
                        st.success("✅ Notificação adicionada!"); st.rerun()
                    except Exception as e: st.error(f"❌ Erro: {e}")
                else: st.warning("⚠️ Preencha pelo menos Notificante e Objeto")

    # CONTÁBIL / TRIBUTÁRIO
    elif pagina == "📒 Contábil / Tributário" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">📒 Contábil / Tributário</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        aba_cont = st.tabs(["📋 Controle de Fechamento", "📦 Pacote para Contabilidade"])

        MESES_NOMES = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
        COLUNAS_FECHAMENTO = ["relatorio_mensal","extratos_mensais","documentacao_mensal","balanco","balancete","razao","nf_emitidas","apuracao_impostos"]
        COLUNAS_LABELS = ["Relatório Mensal","Extratos Mensais","Documentação Mensal","Balanço","Balancete","Razão","NF Emitidas","Apuração de Impostos"]
        STATUS_OPTS = ["", "REALIZADO", "PENDENTE", "ENVIADO"]

        # ── ABA 1: CONTROLE DE FECHAMENTO ──────────────────────
        with aba_cont[0]:
            st.subheader("📋 Controle de Fechamento Contábil")

            # Seletor de ano
            ano_sel = st.selectbox("Ano", list(range(2024, 2030)), index=2, key="cont_ano")

            # Garante que os 12 meses existem no banco
            try:
                for m in range(1, 13):
                    sb.table("contabil_fechamento").upsert({"ano": ano_sel, "mes": m}, on_conflict="ano,mes").execute()
            except Exception:
                pass

            # Carrega dados do ano
            try:
                fech_data = sb.table("contabil_fechamento").select("*").eq("ano", ano_sel).order("mes").execute().data
                df_fech = pd.DataFrame(fech_data)
            except Exception as e:
                st.error(f"Erro: {e}"); df_fech = pd.DataFrame()

            if not df_fech.empty:
                # Monta tabela visual
                st.markdown("---")

                # Header
                header_cols = st.columns([1] + [1.5]*8 + [2])
                header_cols[0].markdown("**Mês**")
                for i, label in enumerate(COLUNAS_LABELS):
                    header_cols[i+1].markdown(f"**{label}**")
                header_cols[-1].markdown("**Notas**")

                st.markdown("---")

                # Linhas por mês
                for _, row in df_fech.iterrows():
                    mes_idx = int(row["mes"]) - 1
                    mes_nome = f"{MESES_NOMES[mes_idx]}/{str(ano_sel)[2:]}"
                    cols = st.columns([1] + [1.5]*8 + [2])
                    cols[0].markdown(f"**{mes_nome}**")

                    novos_valores = {}
                    for i, col_key in enumerate(COLUNAS_FECHAMENTO):
                        val_atual = str(row.get(col_key, "") or "")
                        # Cor do badge
                        if val_atual == "REALIZADO":
                            cor = "#1a7a3a"; fundo = "#d4edda"
                        elif val_atual == "PENDENTE":
                            cor = "#856404"; fundo = "#fff3cd"
                        elif val_atual == "ENVIADO":
                            cor = "#0c4a6e"; fundo = "#bae6fd"
                        else:
                            cor = "#555"; fundo = "#2a2a3a"

                        novo = cols[i+1].selectbox(
                            "", STATUS_OPTS,
                            index=STATUS_OPTS.index(val_atual) if val_atual in STATUS_OPTS else 0,
                            key=f"fech_{ano_sel}_{row['mes']}_{col_key}",
                            label_visibility="collapsed"
                        )
                        novos_valores[col_key] = novo

                    nova_nota = cols[-1].text_input(
                        "", value=str(row.get("notas","") or ""),
                        key=f"nota_{ano_sel}_{row['mes']}",
                        label_visibility="collapsed"
                    )
                    novos_valores["notas"] = nova_nota

                    # Salva automaticamente se mudou
                    mudou = any(novos_valores[k] != str(row.get(k,"") or "") for k in novos_valores)
                    if mudou:
                        try:
                            sb.table("contabil_fechamento").update(novos_valores).eq("id", int(row["id"])).execute()
                        except Exception:
                            pass

                st.markdown("---")

                # Download Excel
                df_export = df_fech.copy()
                df_export["mes"] = df_export["mes"].apply(lambda m: f"{MESES_NOMES[int(m)-1]}/{str(ano_sel)[2:]}")
                df_export.columns = [c.replace("_"," ").upper() if c not in ["id","ano","created_at","updated_at"] else c for c in df_export.columns]
                buf_xl = io.BytesIO()
                with pd.ExcelWriter(buf_xl, engine="xlsxwriter") as w:
                    df_export.drop(columns=["id","ano","created_at","updated_at"], errors="ignore").to_excel(w, index=False, sheet_name=f"Fechamento {ano_sel}")
                st.download_button("📥 Baixar Excel", buf_xl.getvalue(),
                    file_name=f"fechamento_contabil_{ano_sel}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        # ── ABA 2: PACOTE PARA CONTABILIDADE ───────────────────
        with aba_cont[1]:
            st.subheader("📦 Gerar Pacote para Contabilidade")
            st.markdown("Selecione o mês de referência e os arquivos disponíveis no banco para gerar um pacote ZIP.")

            col_a1, col_a2 = st.columns(2)
            with col_a1:
                pac_ano = st.selectbox("Ano de referência", list(range(2024, 2030)), index=2, key="pac_ano")
            with col_a2:
                pac_mes = st.selectbox("Mês de referência", list(range(1,13)),
                    format_func=lambda m: MESES_NOMES[m-1], key="pac_mes")

            st.markdown("---")
            st.markdown("#### Selecione os arquivos para incluir no pacote:")

            # Busca arquivos disponíveis no log_uploads do mês
            try:
                import calendar
                from datetime import datetime as dt
                primeiro_dia = f"{pac_ano}-{pac_mes:02d}-01"
                ultimo_dia = f"{pac_ano}-{pac_mes:02d}-{calendar.monthrange(pac_ano, pac_mes)[1]:02d}"
                logs_mes = sb.table("log_uploads").select("*").gte("data_upload", primeiro_dia).lte("data_upload", ultimo_dia + "T23:59:59").execute().data
            except Exception:
                logs_mes = []

            arquivos_sel = []
            if logs_mes:
                st.caption(f"{len(logs_mes)} arquivo(s) encontrado(s) em {MESES_NOMES[pac_mes-1]}/{str(pac_ano)[2:]}")
                for log in logs_mes:
                    label = f"{log.get('arquivo','')} ({log.get('tipo','').upper()} — {str(log.get('data_upload',''))[:10]})"
                    if st.checkbox(label, key=f"pac_{log['id']}"):
                        arquivos_sel.append(log.get("arquivo",""))
            else:
                st.info(f"Nenhum arquivo encontrado em {MESES_NOMES[pac_mes-1]}/{str(pac_ano)[2:]}.")

            pac_notas = st.text_area("Observações", key="pac_notas", height=80)

            if st.button("📦 Gerar Pacote ZIP", type="primary", disabled=len(arquivos_sel)==0):
                try:
                    import zipfile
                    buf_zip = io.BytesIO()
                    with zipfile.ZipFile(buf_zip, "w") as zf:
                        # Adiciona um manifesto
                        ts = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M")
                        usr = st.session_state.get("nome","")
                        mes_ref = MESES_NOMES[pac_mes-1]
                        manifesto = f"Pacote Contabilidade - {mes_ref}/{pac_ano}\n"
                        manifesto += f"Gerado em: {ts}\n"
                        manifesto += f"Por: {usr}\n\n"
                        manifesto += "Arquivos incluídos:\n"
                        for arq in arquivos_sel:
                            manifesto += f"  - {arq}\n"
                        if pac_notas:
                            manifesto += f"\nObservacoes: {pac_notas}\n"
                        zf.writestr("MANIFESTO.txt", manifesto)

                    # Registra no banco
                    sb.table("contabil_pacotes").insert({
                        "ano": pac_ano,
                        "mes": pac_mes,
                        "arquivos": ", ".join(arquivos_sel),
                        "gerado_por": st.session_state.get("nome",""),
                        "notas": pac_notas
                    }).execute()

                    buf_zip.seek(0)
                    nome_zip = f"contabilidade_{MESES_NOMES[pac_mes-1]}_{pac_ano}.zip"
                    st.download_button("📥 Baixar ZIP", buf_zip.getvalue(),
                        file_name=nome_zip, mime="application/zip", use_container_width=True)
                    st.success("✅ Pacote gerado e registrado!")
                except Exception as e:
                    st.error(f"❌ Erro: {e}")

            # Histórico de pacotes
            st.markdown("---")
            st.subheader("📋 Histórico de Pacotes Gerados")
            try:
                hist_pac = sb.table("contabil_pacotes").select("*").order("created_at", desc=True).limit(20).execute().data
                if hist_pac:
                    df_hist = pd.DataFrame(hist_pac)
                    df_hist["mes"] = df_hist["mes"].apply(lambda m: MESES_NOMES[int(m)-1])
                    df_hist["created_at"] = df_hist["created_at"].astype(str).str[:16].str.replace("T"," ")
                    df_hist = df_hist.rename(columns={"ano":"Ano","mes":"Mês","arquivos":"Arquivos","gerado_por":"Gerado por","notas":"Notas","created_at":"Data/Hora"})
                    st.dataframe(df_hist[["Data/Hora","Ano","Mês","Arquivos","Gerado por","Notas"]], use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhum pacote gerado ainda.")
            except Exception as e:
                st.error(f"Erro: {e}")

    # CADASTRO DE CLIENTES
    elif pagina == "👤 Cadastro de Clientes" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">👤 Cadastro de Clientes</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        # Carrega clientes
        try:
            todos = buscar_todos(sb, "clientes", 100000)
            df_cli = pd.DataFrame(todos)
        except Exception as e:
            st.error(f"Erro ao carregar clientes: {e}")
            df_cli = pd.DataFrame()

        if not df_cli.empty:
            st.caption(f"Total: {len(df_cli)} clientes")

            # FILTROS
            st.markdown("#### 🔎 Filtros")
            col_f1, col_f2, col_f3, col_f4 = st.columns(4)
            with col_f1:
                busca = st.text_input("Buscar por nome ou CNPJ/CPF", placeholder="Digite...", key="cli_busca")
            with col_f2:
                plat_opts = ["Todos"] + sorted(df_cli["PLATAFORMA"].dropna().unique().tolist()) if "PLATAFORMA" in df_cli.columns else ["Todos"]
                plat_sel = st.selectbox("Plataforma", plat_opts, key="cli_plat")
            with col_f3:
                seg_opts = ["Todos"] + sorted(df_cli["SEGMENTO"].dropna().unique().tolist()) if "SEGMENTO" in df_cli.columns else ["Todos"]
                seg_sel = st.selectbox("Segmento", seg_opts, key="cli_seg")
            with col_f4:
                ativo_sel = st.selectbox("Status", ["Todos", "SIM", "NÃO"], key="cli_ativo")

            # Aplica filtros
            df_show = df_cli.copy()
            if busca:
                mask = (
                    df_show["NOME FANTASIA"].astype(str).str.contains(busca, case=False, na=False) |
                    df_show["RAZAO SOCIAL"].astype(str).str.contains(busca, case=False, na=False) |
                    df_show["CPFCNPJ"].astype(str).str.contains(busca, case=False, na=False)
                )
                df_show = df_show[mask]
            if plat_sel != "Todos":
                df_show = df_show[df_show["PLATAFORMA"] == plat_sel]
            if seg_sel != "Todos":
                df_show = df_show[df_show["SEGMENTO"] == seg_sel]
            if ativo_sel != "Todos":
                df_show = df_show[df_show["ATIVO"] == ativo_sel]

            # Ordena alfabeticamente
            df_show = df_show.sort_values("NOME FANTASIA", ignore_index=True)

            st.markdown(f"**{len(df_show)} cliente(s) encontrado(s)**")

            # Downloads
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                buf_xl = io.BytesIO()
                with pd.ExcelWriter(buf_xl, engine="xlsxwriter") as w:
                    df_show.drop(columns=["id","created_at","updated_at","ARQUIVO_ORIGEM"], errors="ignore").to_excel(w, index=False, sheet_name="Clientes")
                st.download_button("📥 Excel", buf_xl.getvalue(),
                    file_name="clientes.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)
            with col_d2:
                try:
                    pdf_cli = gerar_pdf_consulta(df_show, "clientes", {"data_col": None, "merchant_col": "NOME FANTASIA", "plat_col": "PLATAFORMA", "status_col": "ATIVO", "valor": None})
                    st.download_button("📄 PDF", pdf_cli, file_name="clientes.pdf", mime="application/pdf", use_container_width=True)
                except Exception:
                    st.info("PDF indisponível")

            st.markdown("---")

            # Lista de clientes clicáveis para editar
            cols_show = ["NOME FANTASIA","RAZAO SOCIAL","CPFCNPJ","TELEFONE","EMAIL","PLATAFORMA","SEGMENTO","CIDADE","ESTADO","ATIVO"]
            cols_show = [c for c in cols_show if c in df_show.columns]

            # Formata CPF/CNPJ para exibição
            df_display = df_show[cols_show].copy()
            df_display["CPFCNPJ"] = df_display["CPFCNPJ"].apply(formatar_cpf_cnpj)

            # Edição inline
            st.markdown("#### 📋 Lista de Clientes")
            st.caption("Edite diretamente na tabela e clique em Salvar Alterações")

            edited_cli = st.data_editor(
                df_display,
                use_container_width=True,
                hide_index=True,
                key="editor_clientes",
                column_config={
                    "NOME FANTASIA":  st.column_config.TextColumn("Nome Fantasia"),
                    "RAZAO SOCIAL":   st.column_config.TextColumn("Razão Social"),
                    "CPFCNPJ":       st.column_config.TextColumn("CPF/CNPJ", disabled=True),
                    "TELEFONE":       st.column_config.TextColumn("Telefone"),
                    "EMAIL":          st.column_config.TextColumn("Email"),
                    "PLATAFORMA":     st.column_config.TextColumn("Plataforma"),
                    "SEGMENTO":       st.column_config.TextColumn("Segmento"),
                    "CIDADE":         st.column_config.TextColumn("Cidade"),
                    "ESTADO":         st.column_config.TextColumn("Estado"),
                    "ATIVO":          st.column_config.SelectboxColumn("Ativo", options=["SIM","NÃO"]),
                }
            )

            col_s1, col_s2 = st.columns(2)
            with col_s1:
                if st.button("💾 Salvar Alterações", type="primary", key="save_cli", use_container_width=True):
                    erros_save = 0
                    for i, row in edited_cli.iterrows():
                        cnpj_limpo = ''.join(c for c in str(row["CPFCNPJ"]) if c.isdigit())
                        try:
                            sb.table("clientes").update({
                                "NOME FANTASIA": row.get("NOME FANTASIA",""),
                                "RAZAO SOCIAL":  row.get("RAZAO SOCIAL",""),
                                "TELEFONE":      row.get("TELEFONE",""),
                                "EMAIL":         row.get("EMAIL",""),
                                "PLATAFORMA":    row.get("PLATAFORMA",""),
                                "SEGMENTO":      row.get("SEGMENTO",""),
                                "CIDADE":        row.get("CIDADE",""),
                                "ESTADO":        row.get("ESTADO",""),
                                "ATIVO":         row.get("ATIVO","SIM"),
                            }).eq("CPFCNPJ", cnpj_limpo).execute()
                        except Exception:
                            erros_save += 1
                    if erros_save == 0:
                        st.success("✅ Alterações salvas!")
                        st.rerun()
                    else:
                        st.warning(f"⚠️ {erros_save} erros ao salvar")
            with col_s2:
                if st.button("🗑️ Inativar Selecionados", key="del_cli", use_container_width=True):
                    inativos = edited_cli[edited_cli["ATIVO"] == "NÃO"]["CPFCNPJ"].tolist()
                    for cnpj in inativos:
                        cnpj_limpo = ''.join(c for c in str(cnpj) if c.isdigit())
                        sb.table("clientes").update({"ATIVO": "NÃO"}).eq("CPFCNPJ", cnpj_limpo).execute()
                    st.success(f"✅ {len(inativos)} cliente(s) inativado(s)!")
                    st.rerun()
        else:
            st.info("Nenhum cliente cadastrado. Faça o upload do arquivo na aba **📤 Upload de Arquivos**.")

if "logado" not in st.session_state: st.session_state["logado"] = False
if not st.session_state["logado"]: tela_login()
else: app_principal()
