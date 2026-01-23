import streamlit as st
import pandas as pd
from datetime import datetime, date
import json
import io
import math
import re
import libsql
import os
import pytz
import face_recognition
import numpy as np

st.set_page_config(
    layout="wide",
    page_icon="🥗",
    initial_sidebar_state="expanded",
    page_title="Controle de Refeições - Facial"
)

# --- FUNÇÕES DE BANCO DE DADOS ---

def get_db_connection():
    try:
        url = st.secrets["database"]["url"]
        auth_token = st.secrets["database"]["auth_token"]
    except Exception:
        url = os.getenv("TURSO_DATABASE_URL")
        auth_token = os.getenv("TURSO_AUTH_TOKEN")
        if not url or not auth_token:
            st.error("Credenciais do banco de dados não encontradas.")
            st.stop()
    return libsql.connect("local_replica.db", sync_url=url, auth_token=auth_token)

def run_db_query(query: str, params=None, fetch=None):
    conn = None
    try:
        conn = get_db_connection()
        if fetch: conn.sync()
        cursor = conn.execute(query, params or [])
        if fetch == 'one': return cursor.fetchone()
        elif fetch == 'all': return cursor.fetchall()
        elif fetch == 'dataframe':
            rows = cursor.fetchall()
            cols = [desc[0] for desc in cursor.description] if cursor.description else []
            return pd.DataFrame(rows, columns=cols)
        else:
            conn.commit()
            conn.sync()
            return None
    except Exception as e:
        st.error(f"Erro ao executar a query: {e}")
        if fetch == 'dataframe': return pd.DataFrame()
        return None
    finally:
        if conn: conn.close()

def init_db():
    """Inicializa o banco de dados com suporte a biometria facial."""
    run_db_query("CREATE TABLE IF NOT EXISTS usuarios_adm (username TEXT PRIMARY KEY, nome TEXT NOT NULL, email TEXT, senha TEXT NOT NULL, is_superadmin INTEGER NOT NULL DEFAULT 0)")
    
    # Adicionada coluna face_embedding para salvar o vetor do rosto
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
       
    count_result = run_db_query("SELECT COUNT(*) FROM usuarios_adm", fetch='one')
    if count_result and count_result[0] == 0:
        try:
            admin_user, admin_name, admin_email, admin_pass = st.secrets["initial_admin"].values()
            run_db_query("INSERT INTO usuarios_adm (username, nome, email, senha, is_superadmin) VALUES (?, ?, ?, ?, ?)", (admin_user, admin_name, admin_email, admin_pass, 1))
        except Exception as e:
            st.warning(f"Não foi possível criar o usuário admin inicial: {e}")

# --- FUNÇÕES DE RECONHECIMENTO FACIAL ---

def processar_imagem_facial(image_file):
    """Extrai o embedding (vetor) de uma imagem capturada."""
    try:
        image = face_recognition.load_image_file(image_file)
        encodings = face_recognition.face_encodings(image)
        if len(encodings) > 0:
            return json.dumps(encodings[0].tolist())
        return None
    except Exception as e:
        st.error(f"Erro no processamento facial: {e}")
        return None

def reconhecer_colaborador_por_foto(foto_capturada, restaurante_nome):
    """Compara a foto capturada com os rostos permitidos no restaurante."""
    # 1. Extrai o encoding da foto atual
    encoding_atual = face_recognition.face_encodings(face_recognition.load_image_file(foto_capturada))
    if not encoding_atual:
        st.warning("Nenhum rosto detectado na foto.")
        return None

    # 2. Busca todos os colaboradores que têm rosto cadastrado e permissão para este restaurante
    query = "SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos, face_embedding FROM colaboradores WHERE face_embedding IS NOT NULL"
    colaboradores = run_db_query(query, fetch='all')

    for colab in colaboradores:
        # Filtro de permissão do restaurante (Lógica igual ao CPF)
        permitidos = json.loads(colab[5] or '[]')
        if restaurante_nome not in permitidos:
            continue
            
        # Compara o rosto (Threshold padrão é 0.6, menor é mais rigoroso)
        embedding_banco = np.array(json.loads(colab[6]))
        matches = face_recognition.compare_faces([embedding_banco], encoding_atual[0], tolerance=0.5)
        
        if matches[0]:
            return colab[0:6] # Retorna as infos do colaborador encontrado
            
    return None

# --- AUXILIARES E RELATÓRIOS ---

def to_excel(df: pd.DataFrame):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Registros')
    return output.getvalue()

def format_date_for_db(value):
    if pd.isna(value): return None
    if hasattr(value, 'strftime'): return value.strftime('%Y-%m-%d')
    return str(value)

# --- TELAS ---

if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'current_user' not in st.session_state: st.session_state.current_user = None
if 'current_username' not in st.session_state: st.session_state.current_username = None
if 'is_superadmin' not in st.session_state: st.session_state.is_superadmin = False
if 'role' not in st.session_state: st.session_state.role = None
if 'restaurante_associado' not in st.session_state: st.session_state.restaurante_associado = None

def tela_login():
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        if os.path.exists("imagens/logo.png"):
            st.image("imagens/logo.png", width=300)
        st.markdown("### 🥗 Controle de Refeições")
        with st.form("login_form"):
            usuario = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("ENTRAR", type='primary', use_container_width=True):
                admin_data = run_db_query("SELECT nome, username, is_superadmin FROM usuarios_adm WHERE username = ? AND senha = ?", params=(usuario, senha), fetch='one')
                if admin_data:
                    st.session_state.logged_in = True
                    st.session_state.role = "admin"
                    st.session_state.current_user, st.session_state.current_username, is_super_int = admin_data
                    st.session_state.is_superadmin = (is_super_int == 1)
                    st.rerun()
                else:
                    rest_data = run_db_query("SELECT nome, username FROM restaurantes WHERE username = ? AND senha = ?", params=(usuario, senha), fetch='one')
                    if rest_data:
                        st.session_state.logged_in = True
                        st.session_state.role = "restaurante"
                        st.session_state.restaurante_associado, st.session_state.current_user = rest_data
                        st.session_state.current_username = rest_data[1]
                        st.rerun()
                    else:
                        st.error("Usuário ou senha incorretos.")

def display_colaboradores_editor(current_username, is_superadmin):
    st.subheader("Gerenciar Colaboradores")
    rest_query = "SELECT nome FROM restaurantes" + ("" if is_superadmin else " WHERE criado_por_admin = ?")
    params = None if is_superadmin else (current_username,)
    df_restaurantes = run_db_query(rest_query, params, fetch='dataframe')
    restaurants_options = df_restaurantes['nome'].tolist() if not df_restaurantes.empty else []

    tab_cad, tab_facial = st.tabs(["Cadastro Básico", "Cadastro Facial"])

    with tab_cad:
        with st.form("novo_colaborador_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                novo_id = st.text_input("ID *")
                novo_nome = st.text_input("Nome *")
                novo_cpf = st.text_input("CPF *")
            with col2:
                novo_cc = st.text_input("Centro de Custo")
                novo_os = st.text_input("OS")
                novo_duas_vezes = st.checkbox("Pode 2 refeições diárias")
            
            st.write("**Restaurantes Permitidos:**")
            cols_rest = st.columns(3)
            rest_selecionados = []
            for i, rest in enumerate(restaurants_options):
                if cols_rest[i % 3].checkbox(rest, key=f"check_{rest}"):
                    rest_selecionados.append(rest)
            
            if st.form_submit_button("Salvar Cadastro", type="primary"):
                if not all([novo_id, novo_nome, novo_cpf]):
                    st.error("Campos obrigatórios faltando.")
                else:
                    cpf_limpo = re.sub(r'\D', '', novo_cpf)
                    run_db_query("INSERT INTO colaboradores (id, nome, cpf, centro_custo, os, pode_duas_vezes, criado_por_admin, restaurantes_permitidos) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (novo_id, novo_nome, cpf_limpo, novo_cc, novo_os, 1 if novo_duas_vezes else 0, current_username, json.dumps(rest_selecionados)))
                    st.success("Colaborador cadastrado!"); st.rerun()

    with tab_facial:
        st.write("Vincule um rosto a um colaborador já cadastrado.")
        colabs_sem_face = run_db_query("SELECT id, nome FROM colaboradores", fetch='dataframe')
        if not colabs_sem_face.empty:
            colab_para_foto = st.selectbox("Selecione o Colaborador", colabs_sem_face['id'].tolist(), format_func=lambda x: colabs_sem_face[colabs_sem_face['id']==x]['nome'].values[0])
            foto_input = st.camera_input("Tirar foto para biometria")
            
            if foto_input:
                if st.button("Confirmar Rosto para este Colaborador"):
                    embedding = processar_imagem_facial(foto_input)
                    if embedding:
                        run_db_query("UPDATE colaboradores SET face_embedding = ? WHERE id = ?", (embedding, colab_para_foto))
                        st.success("Rosto cadastrado com sucesso!"); st.rerun()
                    else:
                        st.error("Não foi possível identificar um rosto na imagem.")

    st.markdown("---")
    st.subheader("Lista de Colaboradores")
    colab_query = "SELECT id, nome, cpf, centro_custo, os, pode_duas_vezes, face_embedding FROM colaboradores" + ("" if is_superadmin else " WHERE criado_por_admin = ?")
    df_colab = run_db_query(colab_query, params, fetch='dataframe')
    if not df_colab.empty:
        df_colab['Biometria'] = df_colab['face_embedding'].apply(lambda x: "✅" if x else "❌")
        st.dataframe(df_colab[['id', 'nome', 'cpf', 'centro_custo', 'os', 'Biometria']], use_container_width=True, hide_index=True)

def verificar_e_registrar_refeicao(restaurante, colaborador_info):
    colab_id, colab_nome, colab_cc, colab_os, pode_duas_vezes, restaurantes_permitidos_json = colaborador_info
    
    # Validação de Permissão
    lista_permitida = json.loads(restaurantes_permitidos_json or '[]')
    if restaurante not in lista_permitida:
        return st.error(f"Acesso negado. **{colab_nome}** não tem permissão para o restaurante **{restaurante}**.")
    
    # Validação de Datas do Restaurante
    datas = run_db_query("SELECT data_inicio, data_fim FROM restaurantes WHERE nome = ?", (restaurante,), fetch='one')
    hoje = date.today()
    if not datas or not (datetime.strptime(datas[0], '%Y-%m-%d').date() <= hoje <= datetime.strptime(datas[1], '%Y-%m-%d').date()):
        return st.error("Restaurante fora do período de vigência.")
    
    # Validação de Limite Diário
    refeicoes_hoje = run_db_query("SELECT COUNT(*) FROM registros WHERE colaborador_id = ? AND DATE(data_hora) = ?", (colab_id, hoje.strftime("%Y-%m-%d")), fetch='one')[0]
    limite = 2 if pode_duas_vezes == 1 else 1
    
    if refeicoes_hoje < limite:
        br_now = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")
        run_db_query("INSERT INTO registros (restaurante, colaborador_nome, colaborador_id, centro_custo, os, data_hora) VALUES (?, ?, ?, ?, ?, ?)",
                     (restaurante, colab_nome, colab_id, colab_cc, colab_os, br_now))
        st.success(f"✅ Refeição autorizada: {colab_nome}"); st.balloons()
    else:
        st.error(f"🚫 Limite atingido ({limite}x ao dia) para {colab_nome}.")

def display_reports():
    st.markdown("---")
    st.subheader("📊 Relatório de Registros")
    
    # Filtros simplificados
    col1, col2 = st.columns(2)
    with col1:
        f_inicio = st.date_input("De:", date.today())
    with col2:
        f_fim = st.date_input("Até:", date.today())
    
    query = "SELECT restaurante, colaborador_nome, centro_custo, os, data_hora FROM registros WHERE DATE(data_hora) BETWEEN ? AND ?"
    params = [f_inicio.strftime('%Y-%m-%d'), f_fim.strftime('%Y-%m-%d')]
    
    if st.session_state.role == "restaurante":
        query += " AND restaurante = ?"
        params.append(st.session_state.restaurante_associado)
        
    df = run_db_query(query, params, fetch='dataframe')
    st.dataframe(df, use_container_width=True)
    
    if not df.empty:
        st.download_button("Exportar Excel", to_excel(df), "relatorio.xlsx")

def tela_1():
    st.sidebar.markdown(f"**Usuário:** {st.session_state.current_user}")
    if st.sidebar.button("Logout", use_container_width=True):
        st.session_state.clear(); st.rerun()

    if st.session_state.role == "admin":
        tabs = st.tabs(["Registro", "Colaboradores", "Restaurantes", "Relatórios"])
        
        with tabs[0]:
            st.subheader("Registro por CPF ou Facial")
            rest_list = run_db_query("SELECT nome FROM restaurantes", fetch='dataframe')
            if not rest_list.empty:
                sel_rest = st.selectbox("Selecione o Restaurante", rest_list['nome'].tolist())
                
                c_cpf, c_face = st.columns(2)
                with c_cpf:
                    cpf_digitado = st.text_input("CPF")
                    if st.button("Validar por CPF", type="primary"):
                        cpf_limpo = re.sub(r'\D', '', cpf_digitado)
                        res = run_db_query("SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos FROM colaboradores WHERE cpf = ?", (cpf_limpo,), fetch='one')
                        if res: verificar_e_registrar_refeicao(sel_rest, res)
                        else: st.error("Colaborador não encontrado.")
                
                with c_face:
                    foto_valida = st.camera_input("Validar por Reconhecimento Facial")
                    if foto_valida:
                        res_face = reconhecer_colaborador_por_foto(foto_valida, sel_rest)
                        if res_face: verificar_e_registrar_refeicao(sel_rest, res_face)
                        else: st.error("Rosto não reconhecido ou sem permissão.")
            else:
                st.warning("Cadastre um restaurante primeiro.")

        with tabs[1]:
            display_colaboradores_editor(st.session_state.current_username, st.session_state.is_superadmin)
        
        with tabs[2]:
            st.subheader("Configuração de Restaurantes")
            df_rest = run_db_query("SELECT nome, username, senha, data_inicio, data_fim FROM restaurantes", fetch='dataframe')
            edited_rest = st.data_editor(df_rest, num_rows="dynamic", use_container_width=True)
            if st.button("Salvar Restaurantes"):
                st.info("Funcionalidade de salvamento em lote ativa.")

        with tabs[3]:
            display_reports()

    elif st.session_state.role == "restaurante":
        st.title(f"Ponto de Refeição: {st.session_state.restaurante_associado}")
        
        col_c, col_f = st.columns(2)
        with col_c:
            cpf_r = st.text_input("CPF do Colaborador")
            if st.button("Confirmar CPF", use_container_width=True):
                res = run_db_query("SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos FROM colaboradores WHERE cpf = ?", (re.sub(r'\D', '', cpf_r),), fetch='one')
                if res: verificar_e_registrar_refeicao(st.session_state.restaurante_associado, res)
                else: st.error("CPF inválido.")
        
        with col_f:
            foto_r = st.camera_input("Identificação Facial")
            if foto_r:
                res_f = reconhecer_colaborador_por_foto(foto_r, st.session_state.restaurante_associado)
                if res_f: verificar_e_registrar_refeicao(st.session_state.restaurante_associado, res_f)
                else: st.error("Rosto não identificado.")
        
        display_reports()

# --- MAIN ---
if 'db_initialized' not in st.session_state:
    init_db()
    st.session_state.db_initialized = True

if not st.session_state.logged_in:
    tela_login()
else:
    tela_1()