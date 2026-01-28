import streamlit as st
import pandas as pd
from datetime import datetime, date
import json
import io
import math
import re
import libsql_client as libsql
import os
import pytz
import face_recognition
import numpy as np
from PIL import Image

# Configuração da página
st.set_page_config(
    layout="wide",
    page_icon="🥗",
    initial_sidebar_state="expanded",
    page_title="Controle de Refeições - Facial"
)

# --- FUNÇÕES DE BANCO DE DADOS ---

def get_db_client():
    """Conecta ao Turso usando as Secrets do Streamlit Cloud."""
    try:
        url = st.secrets["database"]["url"]
        auth_token = st.secrets["database"]["auth_token"]
    except Exception:
        url = os.getenv("TURSO_DATABASE_URL")
        auth_token = os.getenv("TURSO_AUTH_TOKEN")
        
    if not url or not auth_token:
        st.error("Credenciais do banco de dados não encontradas. Verifique as 'Secrets' no painel do Streamlit.")
        st.stop()
    
    return libsql.create_client_sync(url=url, auth_token=auth_token)

def run_db_query(query: str, params=None, fetch=None):
    """Executa comandos SQL e retorna os dados formatados."""
    client = None
    try:
        client = get_db_client()
        result_set = client.execute(query, params or [])
        
        if fetch == 'one':
            return tuple(result_set.rows[0]) if result_set.rows else None
        elif fetch == 'all':
            return [tuple(row) for row in result_set.rows]
        elif fetch == 'dataframe':
            return pd.DataFrame(result_set.rows, columns=result_set.columns)
        else:
            return None
    except Exception as e:
        st.error(f"Erro na base de dados: {e}")
        return pd.DataFrame() if fetch == 'dataframe' else None
    finally:
        if client:
            client.close()

def init_db():
    """Garante que todas as tabelas existam no Turso."""
    run_db_query("CREATE TABLE IF NOT EXISTS usuarios_adm (username TEXT PRIMARY KEY, nome TEXT NOT NULL, email TEXT, senha TEXT NOT NULL, is_superadmin INTEGER NOT NULL DEFAULT 0)")
    
    run_db_query("""
        CREATE TABLE IF NOT EXISTS colaboradores (
            id TEXT PRIMARY KEY, 
            nome TEXT NOT NULL UNIQUE, 
            cpf TEXT NOT NULL UNIQUE, 
            centro_custo TEXT, 
            os TEXT, 
            pode_duas_vezes INTEGER NOT NULL DEFAULT 0, 
            criado_por_admin TEXT, 
            restaurantes_permitidos TEXT,
            face_embedding TEXT
        )
    """)
    
    run_db_query("CREATE TABLE IF NOT EXISTS registros (id INTEGER PRIMARY KEY AUTOINCREMENT, restaurante TEXT NOT NULL, colaborador_nome TEXT NOT NULL, colaborador_id TEXT NOT NULL, centro_custo TEXT, os TEXT, data_hora TEXT NOT NULL)")
    
    run_db_query("""
        CREATE TABLE IF NOT EXISTS restaurantes (
            nome TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            criado_por_admin TEXT,
            data_inicio TEXT,
            data_fim TEXT
        )
    """)
       
    # Cria admin inicial se não houver nenhum
    count = run_db_query("SELECT COUNT(*) FROM usuarios_adm", fetch='one')
    if count and count[0] == 0:
        try:
            adm = st.secrets["initial_admin"]
            run_db_query("INSERT INTO usuarios_adm (username, nome, email, senha, is_superadmin) VALUES (?, ?, ?, ?, ?)", 
                        (adm["username"], adm["nome"], adm["email"], adm["senha"], 1))
        except:
            pass

# --- RECONHECIMENTO FACIAL ---

def processar_imagem_facial(image_file):
    """Converte foto em vetor numérico (embedding)."""
    try:
        image = face_recognition.load_image_file(image_file)
        encodings = face_recognition.face_encodings(image)
        if encodings:
            return json.dumps(encodings[0].tolist())
        return None
    except Exception as e:
        st.error(f"Erro ao processar rosto: {e}")
        return None

def reconhecer_colaborador_por_foto(foto_capturada, restaurante_nome):
    """Compara foto da câmera com o banco de dados."""
    img = face_recognition.load_image_file(foto_capturada)
    encoding_atual = face_recognition.face_encodings(img)
    if not encoding_atual:
        return None

    colaboradores = run_db_query("SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos, face_embedding FROM colaboradores WHERE face_embedding IS NOT NULL", fetch='all')

    for colab in colaboradores:
        # colab[5] = permitidos, colab[6] = embedding
        permitidos = json.loads(colab[5] or '[]')
        if restaurante_nome not in permitidos:
            continue
            
        embedding_banco = np.array(json.loads(colab[6]))
        if face_recognition.compare_faces([embedding_banco], encoding_atual[0], tolerance=0.5)[0]:
            return colab[0:6]
    return None

# --- LÓGICA DE NEGÓCIO ---

def verificar_e_registrar_refeicao(restaurante, colaborador_info):
    """Valida permissões e grava o registro da refeição."""
    colab_id, colab_nome, colab_cc, colab_os, pode_duas_vezes, rest_perm_json = colaborador_info
    
    # 1. Permissão por Restaurante
    if restaurante not in json.loads(rest_perm_json or '[]'):
        return st.error(f"❌ {colab_nome} não tem acesso a este restaurante.")
    
    # 2. Vigência do Restaurante
    res_data = run_db_query("SELECT data_inicio, data_fim FROM restaurantes WHERE nome = ?", (restaurante,), fetch='one')
    hoje = date.today()
    if res_data:
        d_inicio = datetime.strptime(res_data[0], '%Y-%m-%d').date()
        d_fim = datetime.strptime(res_data[1], '%Y-%m-%d').date()
        if not (d_inicio <= hoje <= d_fim):
            return st.error("❌ Restaurante fora do período de vigência.")
    
    # 3. Limite de Refeições
    refeicoes = run_db_query("SELECT COUNT(*) FROM registros WHERE colaborador_id = ? AND DATE(data_hora) = ?", (colab_id, hoje.strftime("%Y-%m-%d")), fetch='one')[0]
    limite = 2 if pode_duas_vezes == 1 else 1
    
    if refeicoes < limite:
        agora = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")
        run_db_query("INSERT INTO registros (restaurante, colaborador_nome, colaborador_id, centro_custo, os, data_hora) VALUES (?, ?, ?, ?, ?, ?)",
                     (restaurante, colab_nome, colab_id, colab_cc, colab_os, agora))
        st.success(f"✅ Refeição Autorizada: {colab_nome}")
        st.balloons()
    else:
        st.error(f"🚫 Limite atingido ({limite}x ao dia) para {colab_nome}.")

def to_excel(df: pd.DataFrame):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Registros')
    return output.getvalue()

# --- INTERFACE ---

def tela_login():
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        st.markdown("### 🥗 Login do Sistema")
        with st.form("login_form"):
            u = st.text_input("Usuário")
            s = st.text_input("Senha", type="password")
            if st.form_submit_button("ENTRAR", type='primary', use_container_width=True):
                adm = run_db_query("SELECT nome, username, is_superadmin FROM usuarios_adm WHERE username = ? AND senha = ?", (u, s), fetch='one')
                if adm:
                    st.session_state.update({"logged_in": True, "role": "admin", "current_user": adm[0], "current_username": adm[1], "is_superadmin": adm[2] == 1})
                    st.rerun()
                else:
                    rest = run_db_query("SELECT nome, username FROM restaurantes WHERE username = ? AND senha = ?", (u, s), fetch='one')
                    if rest:
                        st.session_state.update({"logged_in": True, "role": "restaurante", "restaurante_associado": rest[0], "current_user": rest[0], "current_username": rest[1]})
                        st.rerun()
                    else:
                        st.error("Usuário ou senha inválidos.")

def display_colaboradores_editor():
    st.subheader("Gerenciar Colaboradores")
    
    # Busca restaurantes para o checklist de permissão
    df_rest = run_db_query("SELECT nome FROM restaurantes", fetch='dataframe')
    lista_rest = df_rest['nome'].tolist() if not df_rest.empty else []

    tab_cad, tab_facial = st.tabs(["Cadastro Básico", "Vincular Biometria"])

    with tab_cad:
        with st.form("novo_colab", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nid = c1.text_input("ID / Matrícula")
            nome = c1.text_input("Nome Completo")
            cpf = c1.text_input("CPF (apenas números)")
            cc = c2.text_input("Centro de Custo")
            os_val = c2.text_input("OS")
            duas = c2.checkbox("Pode 2 refeições/dia")
            
            st.write("Permitir acesso em:")
            selecionados = [r for r in lista_rest if st.checkbox(r, key=f"perm_{r}")]
            
            if st.form_submit_button("Cadastrar Colaborador"):
                if nid and nome and cpf:
                    run_db_query("INSERT INTO colaboradores (id, nome, cpf, centro_custo, os, pode_duas_vezes, criado_por_admin, restaurantes_permitidos) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (nid, nome, re.sub(r'\D','',cpf), cc, os_val, 1 if duas else 0, st.session_state.current_username, json.dumps(selecionados)))
                    st.success("Cadastrado com sucesso!")
                else:
                    st.error("Preencha os campos obrigatórios.")

    with tab_facial:
        colabs = run_db_query("SELECT id, nome FROM colaboradores", fetch='dataframe')
        if not colabs.empty:
            escolhido = st.selectbox("Selecione o Colaborador", colabs['id'].tolist(), format_func=lambda x: colabs[colabs['id']==x]['nome'].values[0])
            foto = st.camera_input("Capturar Rosto")
            if foto and st.button("Confirmar Biometria"):
                emb = processar_imagem_facial(foto)
                if emb:
                    run_db_query("UPDATE colaboradores SET face_embedding = ? WHERE id = ?", (emb, escolhido))
                    st.success("Biometria vinculada!")
                else:
                    st.error("Rosto não detectado. Tente novamente.")

    st.markdown("---")
    df_lista = run_db_query("SELECT id, nome, cpf, centro_custo, face_embedding FROM colaboradores", fetch='dataframe')
    if not df_lista.empty:
        df_lista['Biometria'] = df_lista['face_embedding'].apply(lambda x: "✅" if x else "❌")
        st.dataframe(df_lista[['id', 'nome', 'cpf', 'centro_custo', 'Biometria']], use_container_width=True)

def display_reports():
    st.subheader("📊 Relatório de Registros")
    c1, c2 = st.columns(2)
    ini, fim = c1.date_input("Início", date.today()), c2.date_input("Fim", date.today())
    
    q = "SELECT restaurante, colaborador_nome, centro_custo, os, data_hora FROM registros WHERE DATE(data_hora) BETWEEN ? AND ?"
    p = [ini.strftime('%Y-%m-%d'), fim.strftime('%Y-%m-%d')]
    
    if st.session_state.role == "restaurante":
        q += " AND restaurante = ?"
        p.append(st.session_state.restaurante_associado)
        
    df = run_db_query(q, p, fetch='dataframe')
    st.dataframe(df, use_container_width=True)
    if not df.empty:
        st.download_button("Baixar Excel", to_excel(df), "relatorio.xlsx")

# --- EXECUÇÃO PRINCIPAL ---

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    init_db()

if not st.session_state.logged_in:
    tela_login()
else:
    st.sidebar.title("🥗 Menu")
    st.sidebar.write(f"Usuário: **{st.session_state.current_user}**")
    if st.sidebar.button("Sair"):
        st.session_state.clear()
        st.rerun()

    if st.session_state.role == "admin":
        t1, t2, t3, t4 = st.tabs(["Registro Manual/Facial", "Colaboradores", "Restaurantes", "Relatórios"])
        
        with t1:
            df_r = run_db_query("SELECT nome FROM restaurantes", fetch='dataframe')
            if not df_r.empty:
                r_sel = st.selectbox("Selecione o Restaurante de Operação", df_r['nome'].tolist())
                c_cpf, c_face = st.columns(2)
                with c_cpf:
                    cpf_dig = st.text_input("Validar por CPF")
                    if st.button("Confirmar CPF"):
                        res = run_db_query("SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos FROM colaboradores WHERE cpf = ?", (re.sub(r'\D','',cpf_dig),), fetch='one')
                        if res: verificar_e_registrar_refeicao(r_sel, res)
                        else: st.error("CPF não encontrado.")
                with c_face:
                    f_dig = st.camera_input("Validar por Rosto")
                    if f_dig:
                        res = reconhecer_colaborador_por_foto(f_dig, r_sel)
                        if res: verificar_e_registrar_refeicao(r_sel, res)
                        else: st.error("Não identificado.")
            else:
                st.warning("Cadastre um restaurante na aba 'Restaurantes'.")

        with t2:
            display_colaboradores_editor()
            
        with t3:
            st.subheader("Configuração de Restaurantes")
            df_at = run_db_query("SELECT nome, username, senha, data_inicio, data_fim FROM restaurantes", fetch='dataframe')
            editado = st.data_editor(df_at, num_rows="dynamic", use_container_width=True, key="editor_rest")
            if st.button("Salvar Alterações"):
                # Lógica simples de sincronização (apaga e reinsere para simplificar o código de exemplo)
                run_db_query("DELETE FROM restaurantes")
                for _, row in editado.iterrows():
                    run_db_query("INSERT INTO restaurantes VALUES (?, ?, ?, ?, ?, ?)", 
                                (row['nome'], row['username'], row['senha'], st.session_state.current_username, str(row['data_inicio']), str(row['data_fim'])))
                st.success("Restaurantes atualizados!")

        with t4:
            display_reports()

    else: # Modo Restaurante
        st.title(f"Ponto: {st.session_state.restaurante_associado}")
        col_a, col_b = st.columns(2)
        with col_a:
            cpf_r = st.text_input("CPF")
            if st.button("Registrar"):
                res = run_db_query("SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos FROM colaboradores WHERE cpf = ?", (re.sub(r'\D','',cpf_r),), fetch='one')
                if res: verificar_e_registrar_refeicao(st.session_state.restaurante_associado, res)
        with col_b:
            foto_r = st.camera_input("Reconhecimento")
            if foto_r:
                res = reconhecer_colaborador_por_foto(foto_r, st.session_state.restaurante_associado)
                if res: verificar_e_registrar_refeicao(st.session_state.restaurante_associado, res)
        display_reports()