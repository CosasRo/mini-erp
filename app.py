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
    processar_clientes, formatar_cpf_cnpj,
    atualizar_auditoria
)
from extrato_global import render_tela_ajustes

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
        render_tela_ajustes()

    # AUDITORIA
    elif pagina == "🔍 Auditoria" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">🔍 Auditoria</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        MESES_NOMES_AUD = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]

        # FILTROS
        st.markdown("#### 🔎 Filtros")
        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        with col_f1:
            anos_disp = list(range(2024, 2028))
            ano_ini = st.selectbox("Ano início", anos_disp, index=0, key="aud_ano_ini")
        with col_f2:
            mes_ini = st.selectbox("Mês início", list(range(1,13)),
                format_func=lambda m: MESES_NOMES_AUD[m-1], index=0, key="aud_mes_ini")
        with col_f3:
            ano_fim = st.selectbox("Ano fim", anos_disp, index=2, key="aud_ano_fim")
        with col_f4:
            mes_fim = st.selectbox("Mês fim", list(range(1,13)),
                format_func=lambda m: MESES_NOMES_AUD[m-1], index=5, key="aud_mes_fim")

        col_f5, col_f6 = st.columns(2)
        with col_f5:
            busca_cli = st.text_input("🔍 Buscar cliente", key="aud_busca")
        with col_f6:
            status_aud = st.selectbox("Status", ["Todos", "ALERTA", "ATENCAO", "NORMAL"], key="aud_status")

        if st.button("🔍 Carregar Dados", type="primary", key="aud_load"):
            with st.spinner("Carregando dados..."):
                try:
                    # Busca dados do período
                    res = sb.table("auditoria_fluxo").select("*").gte("ano", ano_ini).lte("ano", ano_fim).order("cliente").order("ano").order("mes").execute()
                    df_aud = pd.DataFrame(res.data)

                    # Filtra período exato
                    df_aud = df_aud[
                        ((df_aud["ano"] > ano_ini) | ((df_aud["ano"] == ano_ini) & (df_aud["mes"] >= mes_ini))) &
                        ((df_aud["ano"] < ano_fim) | ((df_aud["ano"] == ano_fim) & (df_aud["mes"] <= mes_fim)))
                    ]

                    if busca_cli:
                        df_aud = df_aud[df_aud["cliente"].str.contains(busca_cli, case=False, na=False)]
                    if status_aud != "Todos":
                        df_aud = df_aud[df_aud["status"] == status_aud]

                    if df_aud.empty:
                        st.info("Nenhum dado encontrado.")
                    else:
                        # MÉTRICAS
                        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                        with col_m1: st.metric("👥 Clientes", f"{df_aud['cliente'].nunique():,}")
                        with col_m2: st.metric("🔴 Alertas", f"{(df_aud['status']=='ALERTA').sum():,}")
                        with col_m3: st.metric("🟡 Atenção", f"{(df_aud['status']=='ATENCAO').sum():,}")
                        with col_m4: st.metric("✅ Normal", f"{(df_aud['status']=='NORMAL').sum():,}")

                        st.markdown("---")

                        # MONTA TABELA PIVÔ — clientes x meses
                        for col_num in ["sdo_ini","in_mes","out_mes","sdo_mes"]:
                            df_aud[col_num] = pd.to_numeric(df_aud[col_num], errors="coerce").fillna(0)

                        # Gera colunas de mês
                        meses_periodo = []
                        a, m = ano_ini, mes_ini
                        while (a < ano_fim) or (a == ano_fim and m <= mes_fim):
                            meses_periodo.append((a, m))
                            m += 1
                            if m > 12: m = 1; a += 1

                        # Monta pivot por cliente
                        clientes_lista = sorted(df_aud["cliente"].unique().tolist())
                        STATUS_COR = {
                            "NORMAL":  ("#d4edda", "#1a7a3a", "✅"),
                            "ATENCAO": ("#fff3cd", "#856404", "🟡"),
                            "ALERTA":  ("#f8d7da", "#721c24", "🔴"),
                        }

                        # Cabeçalho da tabela
                        n_meses = len(meses_periodo)
                        header_html = '<table style="width:100%;border-collapse:collapse;font-size:0.72rem;">' 
                        header_html += '<thead><tr><th style="text-align:left;padding:4px 8px;background:#3A2D58;color:#fff;position:sticky;left:0;">Cliente</th>'
                        for a, m in meses_periodo:
                            label = f"{MESES_NOMES_AUD[m-1]}/{str(a)[2:]}"
                            header_html += f'<th colspan="3" style="text-align:center;padding:4px;background:#594A92;color:#fff;">{label}</th>'
                        header_html += '</tr><tr><th style="background:#3A2D58;position:sticky;left:0;"></th>'
                        for _ in meses_periodo:
                            header_html += '<th style="background:#2a2a3a;color:#C9C7C1;padding:2px 4px;font-size:0.65rem;">IN</th>'
                            header_html += '<th style="background:#2a2a3a;color:#C9C7C1;padding:2px 4px;font-size:0.65rem;">OUT</th>'
                            header_html += '<th style="background:#2a2a3a;color:#C9C7C1;padding:2px 4px;font-size:0.65rem;">SDO</th>'
                        header_html += '</tr></thead><tbody>'

                        # Linhas por cliente
                        rows_html = ""
                        for cli in clientes_lista:
                            df_cli = df_aud[df_aud["cliente"] == cli]
                            rows_html += f'<tr><td style="padding:3px 8px;font-weight:600;color:#fff;background:#1e1e2e;position:sticky;left:0;white-space:nowrap;">{cli}</td>'
                            for a, m in meses_periodo:
                                row_m = df_cli[(df_cli["ano"]==a) & (df_cli["mes"]==m)]
                                if row_m.empty:
                                    rows_html += '<td style="color:#555;text-align:right;padding:2px 4px;">—</td>'
                                    rows_html += '<td style="color:#555;text-align:right;padding:2px 4px;">—</td>'
                                    rows_html += '<td style="color:#555;text-align:right;padding:2px 4px;">—</td>'
                                else:
                                    r = row_m.iloc[0]
                                    in_v  = r["in_mes"]
                                    out_v = r["out_mes"]
                                    sdo_v = r["sdo_mes"]
                                    st_v  = r["status"]
                                    cor_bg, cor_txt, emoji = STATUS_COR.get(st_v, ("#1e1e2e","#fff",""))

                                    def fmt(v):
                                        if abs(v) >= 1000000: return f"{v/1000000:.1f}M"
                                        if abs(v) >= 1000: return f"{v/1000:.0f}K"
                                        return f"{v:.0f}"

                                    rows_html += f'<td style="text-align:right;padding:2px 4px;color:#C9C7C1;">{fmt(in_v)}</td>'
                                    rows_html += f'<td style="text-align:right;padding:2px 4px;color:#C9C7C1;">{fmt(out_v)}</td>'
                                    rows_html += f'<td style="text-align:right;padding:2px 4px;background:{cor_bg};color:{cor_txt};font-weight:600;">{emoji}{fmt(sdo_v)}</td>'
                            rows_html += '</tr>'

                        table_html = header_html + rows_html + '</tbody></table>'
                        st.markdown(f'<div style="overflow-x:auto;max-height:600px;overflow-y:auto;">{table_html}</div>', unsafe_allow_html=True)

                        st.markdown("---")

                        # Downloads
                        col_d1, col_d2, col_d3 = st.columns(3)
                        with col_d1:
                            buf_xl = io.BytesIO()
                            with pd.ExcelWriter(buf_xl, engine="xlsxwriter") as w:
                                df_aud.drop(columns=["id","created_at"], errors="ignore").to_excel(w, index=False, sheet_name="Auditoria")
                            st.download_button("📥 Excel Completo", buf_xl.getvalue(),
                                file_name="auditoria_fluxo.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True)
                        with col_d2:
                            # Apenas alertas para PDF
                            df_alertas = df_aud[df_aud["status"] == "ALERTA"].copy()
                            if not df_alertas.empty:
                                df_alertas["mes_label"] = df_alertas.apply(lambda r: f"{MESES_NOMES_AUD[int(r['mes'])-1]}/{str(int(r['ano']))[2:]}", axis=1)
                                try:
                                    pdf_aud = gerar_pdf_consulta(df_alertas[["cliente","mes_label","in_mes","out_mes","sdo_mes","status"]],
                                        "auditoria", {"data_col":None,"merchant_col":"cliente","plat_col":None,"status_col":"status","valor":"sdo_mes"})
                                    st.download_button("📄 PDF Alertas", pdf_aud,
                                        file_name="alertas_auditoria.pdf", mime="application/pdf", use_container_width=True)
                                except: st.info("PDF indisponível")
                        with col_d3:
                            # Relatório mensal para análise superior
                            import datetime
                            mes_ref = datetime.datetime.now().month
                            ano_ref = datetime.datetime.now().year
                            df_rel = df_aud[(df_aud["ano"]==ano_ref) & (df_aud["mes"]==mes_ref)].copy()
                            if not df_rel.empty:
                                df_rel["mes_label"] = df_rel["mes"].apply(lambda m: MESES_NOMES_AUD[int(m)-1])
                                buf_rel = io.BytesIO()
                                with pd.ExcelWriter(buf_rel, engine="xlsxwriter") as w:
                                    # Resumo geral
                                    resumo = pd.DataFrame({
                                        "Métrica": ["Total Clientes","Alertas 🔴","Atenção 🟡","Normal ✅"],
                                        "Quantidade": [
                                            df_rel["cliente"].nunique(),
                                            (df_rel["status"]=="ALERTA").sum(),
                                            (df_rel["status"]=="ATENCAO").sum(),
                                            (df_rel["status"]=="NORMAL").sum(),
                                        ]
                                    })
                                    resumo.to_excel(w, index=False, sheet_name="Resumo")
                                    # Lista de alertas
                                    df_rel[df_rel["status"]=="ALERTA"].drop(columns=["id","created_at"], errors="ignore").to_excel(w, index=False, sheet_name="Alertas")
                                    # Todos os clientes
                                    df_rel.drop(columns=["id","created_at"], errors="ignore").to_excel(w, index=False, sheet_name="Todos")
                                st.download_button("📊 Relatório Mensal", buf_rel.getvalue(),
                                    file_name=f"relatorio_auditoria_{MESES_NOMES_AUD[mes_ref-1]}_{ano_ref}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    use_container_width=True)
                            else:
                                st.info("Sem dados do mês atual")

                except Exception as e:
                    st.error(f"Erro: {e}")

    # EMISSÃO DE NOTA FISCAL
    elif pagina == "🧾 Emissão de Nota Fiscal" and perfil == "admin":
        st.markdown(f'<h1 style="color:{ROXO_ESCURO};">🧾 Emissão de Nota Fiscal</h1>', unsafe_allow_html=True)
        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

        aba_nf = st.tabs(["📋 Preparar Notas", "🚀 Emitir", "📊 Histórico"])

        MESES_NF = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]

        # Constantes da NFS-e
        CNPJ_PRESTADOR      = "46698944000185"
        INSCRICAO_MUNICIPAL = "73456039"
        CODIGO_SERVICO      = "06303"
        ALIQUOTA_ISS        = "2.00"
        CODIGO_MUNICIPIO    = "3550308"

        # ── ABA 1: PREPARAR NOTAS ──────────────────────────────
        with aba_nf[0]:
            st.subheader("📋 Preparar Notas Fiscais")
            st.markdown("Selecione o mês de referência para buscar os valores de FEE (CASH-IN + CASH-OUT) por cliente.")

            col_p1, col_p2 = st.columns(2)
            with col_p1:
                nf_ano = st.selectbox("Ano", list(range(2024, 2028)), index=2, key="nf_ano")
            with col_p2:
                nf_mes = st.selectbox("Mês", list(range(1,13)),
                    format_func=lambda m: MESES_NF[m-1], index=5, key="nf_mes")

            mes_ref = f"{MESES_NF[nf_mes-1]}/{nf_ano}"

            if st.button("🔍 Buscar dados do mês", type="primary", key="nf_buscar"):
                with st.spinner("Buscando transações..."):
                    try:
                        # Busca CASH-IN processado do mês
                        res_in = sb.table("cashin").select("*").eq("STATUS","PROCESSED").execute()
                        df_in = pd.DataFrame(res_in.data)

                        # Busca CASH-OUT do mês
                        res_out = sb.table("cashout").select("*").eq("STATUS","SUCCESSFULLY PROCESSED").execute()
                        df_out = pd.DataFrame(res_out.data)

                        # Filtra pelo mês/ano selecionado
                        def filtrar_mes(df, col_data, ano, mes):
                            if df.empty or col_data not in df.columns:
                                return pd.DataFrame()
                            datas = pd.to_datetime(df[col_data], dayfirst=True, errors="coerce")
                            mask = (datas.dt.year == ano) & (datas.dt.month == mes)
                            return df[mask]

                        df_in_mes  = filtrar_mes(df_in,  "PAYMENT TIME",    nf_ano, nf_mes)
                        df_out_mes = filtrar_mes(df_out, "NOTIFICATION TIME", nf_ano, nf_mes)

                        # Agrupa por merchant com CNPJ do PLATFORM DOC NUMBER
                        fee_in = pd.DataFrame()
                        if not df_in_mes.empty and "MERCHANT NAME" in df_in_mes.columns:
                            df_in_mes["FEE"] = pd.to_numeric(df_in_mes["FEE"], errors="coerce").fillna(0)
                            # Pega CNPJ do PLATFORM DOC NUMBER
                            doc_col = "PLATFORM DOC NUMBER" if "PLATFORM DOC NUMBER" in df_in_mes.columns else None
                            if doc_col:
                                fee_in = df_in_mes.groupby("MERCHANT NAME").agg(
                                    FEE_IN=("FEE","sum"),
                                    CNPJ_IN=(doc_col, "first")
                                ).reset_index()
                                fee_in.columns = ["MERCHANT","FEE_IN","CNPJ_IN"]
                            else:
                                fee_in = df_in_mes.groupby("MERCHANT NAME")["FEE"].sum().reset_index()
                                fee_in.columns = ["MERCHANT","FEE_IN"]
                                fee_in["CNPJ_IN"] = ""

                        fee_out = pd.DataFrame()
                        if not df_out_mes.empty and "MERCHANT NAME" in df_out_mes.columns:
                            df_out_mes["COMMISSION"] = pd.to_numeric(df_out_mes["COMMISSION"], errors="coerce").fillna(0)
                            doc_col_out = "PLATFORM DOC NUMBER" if "PLATFORM DOC NUMBER" in df_out_mes.columns else None
                            if doc_col_out:
                                fee_out = df_out_mes.groupby("MERCHANT NAME").agg(
                                    FEE_OUT=("COMMISSION","sum"),
                                    CNPJ_OUT=(doc_col_out, "first")
                                ).reset_index()
                                fee_out.columns = ["MERCHANT","FEE_OUT","CNPJ_OUT"]
                            else:
                                fee_out = df_out_mes.groupby("MERCHANT NAME")["COMMISSION"].sum().reset_index()
                                fee_out.columns = ["MERCHANT","FEE_OUT"]
                                fee_out["CNPJ_OUT"] = ""

                        # Mescla IN + OUT
                        if not fee_in.empty and not fee_out.empty:
                            df_nf = fee_in.merge(fee_out, on="MERCHANT", how="outer").fillna("")
                        elif not fee_in.empty:
                            df_nf = fee_in.copy(); df_nf["FEE_OUT"] = 0; df_nf["CNPJ_OUT"] = ""
                        elif not fee_out.empty:
                            df_nf = fee_out.copy(); df_nf["FEE_IN"] = 0; df_nf["CNPJ_IN"] = ""
                        else:
                            st.warning("Nenhuma transação encontrada para o período.")
                            df_nf = pd.DataFrame()

                        if not df_nf.empty:
                            # Usa CNPJ do IN, se não tiver usa do OUT
                            df_nf["FEE_IN"]  = pd.to_numeric(df_nf.get("FEE_IN",  0), errors="coerce").fillna(0)
                            df_nf["FEE_OUT"] = pd.to_numeric(df_nf.get("FEE_OUT", 0), errors="coerce").fillna(0)
                            df_nf["CNPJ_IN"]  = df_nf.get("CNPJ_IN",  "").fillna("").astype(str)
                            df_nf["CNPJ_OUT"] = df_nf.get("CNPJ_OUT", "").fillna("").astype(str)
                            df_nf["CPF ou CNPJ"] = df_nf.apply(
                                lambda r: r["CNPJ_IN"] if r["CNPJ_IN"].strip() else r["CNPJ_OUT"], axis=1)
                            # Limpa formatação
                            df_nf["CPF ou CNPJ"] = df_nf["CPF ou CNPJ"].str.replace(r"[.\-/]","",regex=True).str.strip()

                            # Se ainda sem CNPJ, busca no cadastro de clientes
                            sem_cnpj = df_nf["CPF ou CNPJ"].str.len() < 11
                            if sem_cnpj.any():
                                clientes_res = sb.table("clientes").select("*").execute()
                                df_cli = pd.DataFrame(clientes_res.data)
                                if not df_cli.empty:
                                    df_cli["NF_KEY"] = df_cli["NOME FANTASIA"].astype(str).str.strip().str.upper()
                                    df_nf["NF_KEY"]  = df_nf["MERCHANT"].astype(str).str.strip().str.upper()
                                    df_nf = df_nf.merge(df_cli[["NF_KEY","CPFCNPJ","RAZAO SOCIAL"]], on="NF_KEY", how="left")
                                    df_nf.loc[sem_cnpj, "CPF ou CNPJ"] = df_nf.loc[sem_cnpj, "CPFCNPJ"].fillna("")
                                    df_nf["RAZAO SOCIAL COMPLETA"] = df_nf.get("RAZAO SOCIAL","").fillna("")

                            df_nf["VALOR_TOTAL"] = df_nf["FEE_IN"] + df_nf["FEE_OUT"]
                            df_nf = df_nf[df_nf["VALOR_TOTAL"] > 0].sort_values("MERCHANT")

                            # Alerta clientes sem CNPJ
                            sem_doc = df_nf[df_nf["CPF ou CNPJ"].str.len() < 11]
                            if not sem_doc.empty:
                                st.warning(f"⚠️ {len(sem_doc)} cliente(s) sem CPF/CNPJ — verifique o Cadastro de Clientes: {', '.join(sem_doc['MERCHANT'].tolist()[:5])}")

                            df_nf = df_nf.rename(columns={
                                "MERCHANT":    "Razão Social / Nome",
                                "FEE_IN":      "FEE IN",
                                "FEE_OUT":     "FEE OUT",
                                "VALOR_TOTAL": "Valor",
                            })
                            cols_show = ["Razão Social / Nome","CPF ou CNPJ","FEE IN","FEE OUT","Valor"]
                            cols_show = [c for c in cols_show if c in df_nf.columns]
                            df_nf = df_nf[cols_show]

                            st.success(f"✅ {len(df_nf)} clientes encontrados — Total: R$ {df_nf['Valor'].sum():,.2f}")
                            sem_cnpj_final = (df_nf["CPF ou CNPJ"].str.len() < 11).sum()
                            if sem_cnpj_final > 0:
                                st.error(f"❌ {sem_cnpj_final} cliente(s) ainda sem CPF/CNPJ — complete o cadastro antes de emitir")
                            st.session_state["df_nf_preparado"] = df_nf
                            st.session_state["nf_mes_ref"] = mes_ref

                    except Exception as e:
                        st.error(f"Erro: {e}")

            # Mostra tabela preparada
            if "df_nf_preparado" in st.session_state:
                df_edit = st.session_state["df_nf_preparado"]
                st.markdown(f"**Referência: {st.session_state.get('nf_mes_ref','')}**")

                edited_nf = st.data_editor(
                    df_edit,
                    use_container_width=True, hide_index=True, key="editor_nf",
                    column_config={
                        "Razão Social / Nome": st.column_config.TextColumn("Razão Social / Nome"),
                        "CPF ou CNPJ":         st.column_config.TextColumn("CPF ou CNPJ"),
                        "FEE IN":              st.column_config.NumberColumn("FEE IN", format="R$ %.2f"),
                        "FEE OUT":             st.column_config.NumberColumn("FEE OUT", format="R$ %.2f"),
                        "Valor":               st.column_config.NumberColumn("Valor Total", format="R$ %.2f"),
                    }
                )

                col_e1, col_e2 = st.columns(2)
                with col_e1:
                    # Download Excel no formato do motor
                    df_export = edited_nf[["Razão Social / Nome","CPF ou CNPJ","Valor"]].copy()
                    df_export["Status"] = ""
                    buf_nf = io.BytesIO()
                    with pd.ExcelWriter(buf_nf, engine="xlsxwriter") as w:
                        df_export.to_excel(w, index=False, sheet_name="NFS-e")
                    st.download_button("📥 Baixar Excel (formato motor)",
                        buf_nf.getvalue(),
                        file_name=f"PagoExpress_NF_{st.session_state.get('nf_mes_ref','').replace('/','_')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True)
                with col_e2:
                    if st.button("✅ Confirmar e Salvar para Emissão", type="primary", use_container_width=True, key="nf_confirmar"):
                        try:
                            # Salva no banco como pendentes
                            for _, row in edited_nf.iterrows():
                                if float(row.get("Valor",0) or 0) <= 0:
                                    continue
                                sb.table("nfse_emissoes").insert({
                                    "mes_referencia": st.session_state.get("nf_mes_ref",""),
                                    "razao_social":   str(row.get("Razão Social / Nome","")),
                                    "cpfcnpj":        str(row.get("CPF ou CNPJ","") or "").replace(".","").replace("-","").replace("/",""),
                                    "valor":          float(row.get("Valor",0) or 0),
                                    "status":         "PENDENTE",
                                    "emitido_por":    st.session_state.get("nome","")
                                }).execute()
                            st.success("✅ Notas salvas! Vá para a aba Emitir.")
                            del st.session_state["df_nf_preparado"]
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro: {e}")

        # ── ABA 2: EMITIR ──────────────────────────────────────
        with aba_nf[1]:
            st.subheader("🚀 Emitir Notas Fiscais")

            # Lista pendentes
            try:
                pend = sb.table("nfse_emissoes").select("*").eq("status","PENDENTE").order("razao_social").execute().data
                df_pend = pd.DataFrame(pend) if pend else pd.DataFrame()
            except Exception as e:
                st.error(f"Erro: {e}"); df_pend = pd.DataFrame()

            if df_pend.empty:
                st.info("Nenhuma nota pendente de emissão. Prepare as notas na aba anterior.")
            else:
                st.warning(f"⚠️ {len(df_pend)} nota(s) pendente(s) de emissão")
                st.dataframe(df_pend[["razao_social","cpfcnpj","valor","mes_referencia"]],
                    use_container_width=True, hide_index=True)

                st.markdown("---")
                st.markdown("#### ⚙️ Configuração para Emissão")

                col_c1, col_c2 = st.columns(2)
                with col_c1:
                    st.info("🔐 Certificado digital configurado via Streamlit Secrets")
                    st.caption("Para renovar o certificado, atualize os secrets: nfse_cert_b64 e nfse_cert_senha")
                    # Verifica se está configurado
                    cert_ok = "nfse_cert_b64" in st.secrets and "nfse_cert_senha" in st.secrets
                    if cert_ok:
                        st.success("✅ Certificado configurado e pronto para uso")
                    else:
                        st.error("❌ Certificado não configurado — adicione nfse_cert_b64 e nfse_cert_senha nos Secrets do Streamlit")
                with col_c2:
                    mes_atual = pd.Timestamp.now().strftime("%B/%Y")
                    descricao_nf = st.text_area("📝 Descrição da NF", height=100, key="desc_nf",
                        value=f"Prestacao de servicos de intermediacao de pagamentos. Cash-In e Cash-Out referente ao mes de {mes_atual}. Valor debitado diretamente no repasse, nao havendo necessidade de pagamento.")
                    st.caption("⚠️ A emissão conecta diretamente com a Prefeitura de SP. Edite a descrição conforme necessário.")

                if st.button("🚀 EMITIR NOTAS FISCAIS", type="primary", use_container_width=True, key="btn_emitir"):
                    if not cert_ok:
                        st.error("❌ Configure o certificado nos Secrets antes de emitir!")
                    else:
                        try:
                            import os, re, time, random, base64, tempfile
                            from lxml import etree
                            from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
                            from cryptography.hazmat.backends import default_backend
                            from cryptography.hazmat.primitives import hashes
                            from cryptography.hazmat.primitives.asymmetric import padding as crypto_padding
                            from signxml import XMLSigner, methods
                            from requests import Session
                            from zeep import Client
                            from zeep.transports import Transport

                            # Carrega certificado dos Secrets
                            pfx_data = base64.b64decode(st.secrets["nfse_cert_b64"])
                            senha_pfx = st.secrets["nfse_cert_senha"]
                            private_key, certificate, _ = pkcs12.load_key_and_certificates(
                                pfx_data, senha_pfx.encode(), default_backend())
                            cert_pem = certificate.public_bytes(Encoding.PEM)
                            key_pem  = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

                            # Arquivos temporários
                            tmp_cert = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
                            tmp_key  = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
                            tmp_cert.write(cert_pem); tmp_cert.close()
                            tmp_key.write(key_pem);   tmp_key.close()

                            # Conecta ao WS
                            session = Session()
                            session.cert = (tmp_cert.name, tmp_key.name)
                            transport = Transport(session=session)
                            WSDL_URL = "https://nfe.prefeitura.sp.gov.br/ws/lotenfe.asmx?WSDL"
                            client = Client(WSDL_URL, transport=transport)

                            # Busca próximo RPS
                            ctrl = sb.table("nfse_controle").select("*").limit(1).execute().data
                            numero_rps = int(ctrl[0]["ultimo_rps"]) + 1 if ctrl else 879

                            prog = st.progress(0)
                            total_pend = len(df_pend)
                            sucesso = erro = 0

                            for idx, row in df_pend.iterrows():
                                prog.progress((idx) / total_pend)
                                razao   = str(row["razao_social"]).strip()
                                doc     = str(row["cpfcnpj"]).replace(".","").replace("-","").replace("/","").strip()
                                valor   = float(row["valor"])
                                nfse_id = int(row["id"])

                                try:
                                    # Monta XML (mesmo motor)
                                    doc = doc.zfill(14) if len(doc) > 11 else doc.zfill(11)
                                    tipo_doc = "2" if len(doc) == 14 else "1"
                                    valor_float = round(valor, 2)
                                    valor_fmt = f"{valor_float:.2f}"
                                    data_hoje = pd.Timestamp.now().strftime("%Y-%m-%d")

                                    # Assinatura
                                    str_assinar = (
                                        INSCRICAO_MUNICIPAL.zfill(8) + "RPS  " +
                                        str(numero_rps).zfill(12) +
                                        data_hoje.replace("-","") + "TNN" +
                                        str(int(valor_float * 100)).zfill(15) +
                                        "0".zfill(15) + CODIGO_SERVICO + tipo_doc +
                                        doc.zfill(14)
                                    )
                                    sig = base64.b64encode(
                                        private_key.sign(str_assinar.encode("utf-8"), crypto_padding.PKCS1v15(), hashes.SHA1())
                                    ).decode()

                                    tomador_tag = f"<CNPJ>{doc}</CNPJ>" if tipo_doc == "2" else f"<CPF>{doc}</CPF>"
                                    razao_safe = razao.replace("&","e").replace("<","").replace(">","").replace('"',"").replace("'","").replace("%","")
                                    desc_safe = descricao_nf.replace("&","e").replace("<","").replace(">","")

                                    xml_str = (
                                        '<?xml version="1.0" encoding="UTF-8"?>' +
                                        '<PedidoEnvioLoteRPS xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns="http://www.prefeitura.sp.gov.br/nfe">' +
                                        '<Cabecalho Versao="1" xmlns="">' +
                                        f'<CPFCNPJRemetente><CNPJ>{CNPJ_PRESTADOR}</CNPJ></CPFCNPJRemetente>' +
                                        '<transacao>true</transacao>' +
                                        f'<dtInicio>{data_hoje}</dtInicio><dtFim>{data_hoje}</dtFim>' +
                                        '<QtdRPS>1</QtdRPS>' +
                                        f'<ValorTotalServicos>{valor_fmt}</ValorTotalServicos>' +
                                        '<ValorTotalDeducoes>0.00</ValorTotalDeducoes></Cabecalho>' +
                                        '<RPS xmlns="">' +
                                        f'<Assinatura>{sig}</Assinatura>' +
                                        f'<ChaveRPS><InscricaoPrestador>{INSCRICAO_MUNICIPAL}</InscricaoPrestador><SerieRPS>RPS</SerieRPS><NumeroRPS>{numero_rps}</NumeroRPS></ChaveRPS>' +
                                        '<TipoRPS>RPS</TipoRPS>' +
                                        f'<DataEmissao>{data_hoje}</DataEmissao>' +
                                        '<StatusRPS>N</StatusRPS><TributacaoRPS>T</TributacaoRPS>' +
                                        f'<ValorServicos>{valor_fmt}</ValorServicos>' +
                                        '<ValorDeducoes>0.00</ValorDeducoes><ValorPIS>0.00</ValorPIS><ValorCOFINS>0.00</ValorCOFINS><ValorINSS>0.00</ValorINSS><ValorIR>0.00</ValorIR><ValorCSLL>0.00</ValorCSLL>' +
                                        f'<CodigoServico>{CODIGO_SERVICO}</CodigoServico>' +
                                        f'<AliquotaServicos>{ALIQUOTA_ISS}</AliquotaServicos>' +
                                        '<ISSRetido>false</ISSRetido>' +
                                        f'<CPFCNPJTomador>{tomador_tag}</CPFCNPJTomador>' +
                                        f'<RazaoSocialTomador>{razao_safe}</RazaoSocialTomador>' +
                                        f'<EnderecoTomador><TipoLogradouro>R</TipoLogradouro><Logradouro>NAO INFORMADO</Logradouro><NumeroEndereco>0</NumeroEndereco><Bairro>NAO INFORMADO</Bairro><Cidade>{CODIGO_MUNICIPIO}</Cidade><UF>SP</UF><CEP>01310100</CEP></EnderecoTomador>' +
                                        f'<EmailTomador></EmailTomador>' +
                                        f'<Discriminacao>{desc_safe}</Discriminacao>' +
                                        '</RPS></PedidoEnvioLoteRPS>'
                                    )

                                    root = etree.fromstring(xml_str.encode("utf-8"))
                                    signer = XMLSigner(method=methods.enveloped, signature_algorithm="rsa-sha1",
                                        digest_algorithm="sha1", c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
                                    signed_root = signer.sign(root, key=private_key, cert=cert_pem)
                                    xml_assinado = etree.tostring(signed_root, encoding="unicode")

                                    resultado = client.service.EnvioLoteRPS(VersaoSchema=1, MensagemXML=xml_assinado)
                                    ret = str(resultado) if resultado else ""

                                    # Parseia retorno
                                    num_nfse = cod_ver = chave_nac = ""
                                    ok = False

                                    if "NumeroNFe>" in ret:
                                        idx2 = ret.find("NumeroNFe>") + 10
                                        num_nfse = ret[idx2:ret.find("<", idx2)]
                                        ok = True
                                    if "CodigoVerificacao>" in ret:
                                        idx2 = ret.find("CodigoVerificacao>") + 18
                                        cod_ver = ret[idx2:ret.find("<", idx2)]
                                    if "ChaveNotaNacional>" in ret:
                                        idx2 = ret.find("ChaveNotaNacional>") + 18
                                        chave_nac = ret[idx2:ret.find("<", idx2)]
                                    if "Sucesso>true" in ret and not num_nfse:
                                        ok = True; num_nfse = "lote"
                                    if "224" in ret and "NFS-e" in ret:
                                        match = re.search(r"NFS-e (\d+)", ret)
                                        num_nfse = match.group(1) if match else "?"
                                        ok = True

                                    status_nf = f"Emitida - NFS-e {num_nfse}" if ok else f"Erro: {ret[:100]}"

                                    sb.table("nfse_emissoes").update({
                                        "numero_rps":         numero_rps,
                                        "numero_nfse":        num_nfse,
                                        "codigo_verificacao": cod_ver,
                                        "chave_nacional":     chave_nac,
                                        "xml_envio":          xml_assinado[:5000],
                                        "xml_retorno":        ret[:5000],
                                        "status":             "EMITIDA" if ok else "ERRO",
                                        "erro":               "" if ok else ret[:500]
                                    }).eq("id", nfse_id).execute()

                                    # Atualiza controle RPS
                                    if ctrl:
                                        sb.table("nfse_controle").update({"ultimo_rps": numero_rps}).eq("id", ctrl[0]["id"]).execute()

                                    if ok: sucesso += 1
                                    else:  erro += 1
                                    numero_rps += 1
                                    time.sleep(random.uniform(2.0, 4.0))

                                except Exception as e:
                                    sb.table("nfse_emissoes").update({"status":"ERRO","erro":str(e)[:500]}).eq("id",nfse_id).execute()
                                    erro += 1; numero_rps += 1

                            prog.progress(1.0)
                            os.unlink(tmp_cert.name); os.unlink(tmp_key.name)
                            st.success(f"✅ Concluído! {sucesso} emitidas | {erro} erros")
                            st.rerun()

                        except Exception as e:
                            st.error(f"❌ Erro geral: {e}")

        # ── ABA 3: HISTÓRICO ───────────────────────────────────
        with aba_nf[2]:
            st.subheader("📊 Histórico de Emissões")

            col_h1, col_h2, col_h3 = st.columns(3)
            with col_h1: mes_hist = st.text_input("Mês ref. (ex: Jul/2026)", key="nf_hist_mes")
            with col_h2: status_hist = st.selectbox("Status", ["Todos","EMITIDA","PENDENTE","ERRO"], key="nf_hist_status")
            with col_h3: busca_hist = st.text_input("Buscar cliente", key="nf_hist_busca")

            try:
                hist = buscar_todos(sb, "nfse_emissoes", 10000)
                df_hist = pd.DataFrame(hist) if hist else pd.DataFrame()
                if not df_hist.empty:
                    if mes_hist: df_hist = df_hist[df_hist["mes_referencia"].str.contains(mes_hist, case=False, na=False)]
                    if status_hist != "Todos": df_hist = df_hist[df_hist["status"] == status_hist]
                    if busca_hist: df_hist = df_hist[df_hist["razao_social"].str.contains(busca_hist, case=False, na=False)]

                    col_m1, col_m2, col_m3 = st.columns(3)
                    with col_m1: st.metric("Total", f"{len(df_hist):,}")
                    with col_m2: st.metric("✅ Emitidas", f"{(df_hist['status']=='EMITIDA').sum():,}")
                    with col_m3: st.metric("❌ Erros", f"{(df_hist['status']=='ERRO').sum():,}")

                    cols_show = ["mes_referencia","razao_social","cpfcnpj","valor","numero_rps","numero_nfse","codigo_verificacao","status","created_at"]
                    cols_show = [c for c in cols_show if c in df_hist.columns]
                    st.dataframe(df_hist[cols_show], use_container_width=True, hide_index=True)

                    # Links da prefeitura para NFs emitidas
                    df_emitidas = df_hist[df_hist["status"]=="EMITIDA"].copy()
                    if not df_emitidas.empty:
                        st.markdown("---")
                        st.subheader("🔗 Links para Consulta na Prefeitura de SP")
                        for _, row in df_emitidas.iterrows():
                            num_nfse = str(row.get("numero_nfse","") or "")
                            cod_ver  = str(row.get("codigo_verificacao","") or "")
                            razao    = str(row.get("razao_social","") or "")
                            if num_nfse and cod_ver:
                                link = f"https://nfe.prefeitura.sp.gov.br/contribuinte/notaprint.aspx?inscricao={INSCRICAO_MUNICIPAL}&nf={num_nfse}&verificacao={cod_ver}"
                                st.markdown(f"**{razao}** — NFS-e {num_nfse} → [🔗 Ver na Prefeitura]({link})")

                    buf_h = io.BytesIO()
                    with pd.ExcelWriter(buf_h, engine="xlsxwriter") as w:
                        df_hist[cols_show].to_excel(w, index=False, sheet_name="Historico NFS-e")
                    st.download_button("📥 Excel", buf_h.getvalue(),
                        file_name="historico_nfse.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                else:
                    st.info("Nenhuma nota emitida ainda.")
            except Exception as e:
                st.error(f"Erro: {e}")

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

                # CSS para compactar linhas
                st.markdown("""
                <style>
                div[data-testid="stHorizontalBlock"] { gap: 4px !important; align-items: center !important; }
                div[data-testid="stSelectbox"] { margin-bottom: 0px !important; }
                div[data-testid="stSelectbox"] > label { display: none !important; }
                </style>
                """, unsafe_allow_html=True)

                STATUS_CORES = {
                    "REALIZADO": ("🟢", "#d4edda", "#1a7a3a"),
                    "PENDENTE":  ("🟡", "#fff3cd", "#856404"),
                    "ENVIADO":   ("🔵", "#bae6fd", "#0c4a6e"),
                    "":          ("",   "#1e1e2e", "#888"),
                }

                # Linhas por mês
                for _, row in df_fech.iterrows():
                    mes_idx = int(row["mes"]) - 1
                    mes_nome = f"{MESES_NOMES[mes_idx]}/{str(ano_sel)[2:]}"
                    cols = st.columns([0.6] + [1.2]*8 + [2])
                    cols[0].markdown(f"**{mes_nome}**")

                    novos_valores = {}
                    for i, col_key in enumerate(COLUNAS_FECHAMENTO):
                        val_atual = str(row.get(col_key, "") or "").strip()
                        if val_atual not in STATUS_OPTS:
                            val_atual = ""
                        emoji, fundo, cor = STATUS_CORES.get(val_atual, STATUS_CORES[""])

                        # Badge clicável como selectbox estilizado
                        cols[i+1].markdown(
                            f'<div style="background:{fundo};color:{cor};border-radius:6px;padding:3px 6px;font-size:0.7rem;font-weight:700;text-align:center;margin-bottom:2px;">{emoji} {val_atual if val_atual else "—"}</div>',
                            unsafe_allow_html=True
                        )
                        novo = cols[i+1].selectbox(
                            "", STATUS_OPTS,
                            index=STATUS_OPTS.index(val_atual),
                            key=f"fech_{ano_sel}_{row['mes']}_{col_key}",
                            label_visibility="collapsed"
                        )
                        novos_valores[col_key] = novo

                    nova_nota = cols[-1].text_input(
                        "", value=str(row.get("notas","") or ""),
                        key=f"nota_{ano_sel}_{row['mes']}",
                        label_visibility="collapsed",
                        placeholder="Notas..."
                    )
                    novos_valores["notas"] = nova_nota

                    # Salva automaticamente se mudou
                    mudou = any(novos_valores[k] != str(row.get(k,"") or "").strip() for k in novos_valores)
                    if mudou:
                        try:
                            sb.table("contabil_fechamento").update(novos_valores).eq("id", int(row["id"])).execute()
                        except Exception:
                            pass
                    st.markdown("<hr style='margin:2px 0;border-color:#2a2a3a;'>", unsafe_allow_html=True)

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
