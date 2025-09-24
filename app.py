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

st.set_page_config(
    layout="wide",
    page_icon="🥗",
    initial_sidebar_state="expanded",
    page_title="Controle de Refeições"
)

# --- BLOCO DE BANCO DE DADOS ---
def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados."""
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
    """Executa uma query no banco de dados de forma síncrona."""
    conn = None
    try:
        conn = get_db_connection()
        if fetch:
            conn.sync()
        cursor = conn.execute(query, params or [])
        if fetch == 'one':
            return cursor.fetchone()
        elif fetch == 'all':
            return cursor.fetchall()
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
        if fetch == 'dataframe':
            return pd.DataFrame()
        return None
    finally:
        if conn:
            conn.close()

# --- Funções Auxiliares ---
def to_excel(df: pd.DataFrame):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Registros')
    return output.getvalue()

def format_date_for_db(value):
    if pd.isna(value): return None
    if hasattr(value, 'strftime'): return value.strftime('%Y-%m-%d')
    return str(value)

def init_db():
    """Inicializa o banco de dados com a estrutura de todas as tabelas."""
    run_db_query("CREATE TABLE IF NOT EXISTS usuarios_adm (username TEXT PRIMARY KEY, nome TEXT NOT NULL, email TEXT, senha TEXT NOT NULL, is_superadmin INTEGER NOT NULL DEFAULT 0)")
    run_db_query("CREATE TABLE IF NOT EXISTS colaboradores (id TEXT PRIMARY KEY, nome TEXT NOT NULL UNIQUE, cpf TEXT NOT NULL UNIQUE, centro_custo TEXT, os TEXT, pode_duas_vezes INTEGER NOT NULL DEFAULT 0, criado_por_admin TEXT, restaurantes_permitidos TEXT)")
    run_db_query("CREATE TABLE IF NOT EXISTS registros (id INTEGER PRIMARY KEY AUTOINCREMENT, restaurante TEXT NOT NULL, colaborador_nome TEXT NOT NULL, colaborador_id TEXT NOT NULL, centro_custo TEXT, os TEXT, data_hora TEXT NOT NULL)")
    run_db_query("CREATE TABLE IF NOT EXISTS restaurantes (nome TEXT PRIMARY KEY, criado_por_admin TEXT, data_inicio TEXT, data_fim TEXT)")
    
    # <<< NOVO: Cria a tabela para os donos de restaurante >>>
    run_db_query("""
        CREATE TABLE IF NOT EXISTS donos_restaurante (
            username TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            senha TEXT NOT NULL,
            restaurante TEXT NOT NULL,
            criado_por_admin TEXT,
            FOREIGN KEY (restaurante) REFERENCES restaurantes(nome)
        )
    """)
    
    count_result = run_db_query("SELECT COUNT(*) FROM usuarios_adm", fetch='one')
    if count_result and count_result[0] == 0:
        try:
            admin_user, admin_name, admin_email, admin_pass = st.secrets["initial_admin"].values()
            run_db_query("INSERT INTO usuarios_adm (username, nome, email, senha, is_superadmin) VALUES (?, ?, ?, ?, ?)", (admin_user, admin_name, admin_email, admin_pass, 1))
        except Exception as e:
            st.warning(f"Não foi possível criar o usuário admin inicial: {e}")

# --- INICIALIZAÇÃO DO ESTADO DA SESSÃO ---
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'current_user' not in st.session_state: st.session_state.current_user = None
if 'current_username' not in st.session_state: st.session_state.current_username = None
if 'is_superadmin' not in st.session_state: st.session_state.is_superadmin = False
# <<< NOVO: Estados para o tipo de usuário e seu restaurante >>>
if 'user_role' not in st.session_state: st.session_state.user_role = None
if 'user_restaurant' not in st.session_state: st.session_state.user_restaurant = None

def tela_login():
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        st.image("imagens/logo.png", width='stretch')
        st.markdown("### Controle de Refeições")
        with st.form("login_form"):
            usuario = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("ENTRAR", type='primary', use_container_width=True):
                # <<< ALTERAÇÃO: Lógica de login que verifica as duas tabelas >>>
                
                # 1. Tenta logar como Admin
                admin_data = run_db_query("SELECT nome, username, is_superadmin FROM usuarios_adm WHERE username = ? AND senha = ?", params=(usuario, senha), fetch='one')
                if admin_data:
                    st.session_state.logged_in = True
                    st.session_state.user_role = "admin"
                    st.session_state.current_user, st.session_state.current_username, is_super_int = admin_data
                    st.session_state.is_superadmin = (is_super_int == 1)
                    st.rerun()
                else:
                    # 2. Se não for Admin, tenta logar como Dono de Restaurante
                    owner_data = run_db_query("SELECT nome, username, restaurante FROM donos_restaurante WHERE username = ? AND senha = ?", params=(usuario, senha), fetch='one')
                    if owner_data:
                        st.session_state.logged_in = True
                        st.session_state.user_role = "owner"
                        st.session_state.current_user, st.session_state.current_username, st.session_state.user_restaurant = owner_data
                        st.session_state.is_superadmin = False # Dono nunca é superadmin
                        st.rerun()
                    else:
                        st.error("Usuário ou senha incorretos.")

# ... (paginated_dataframe, display_colaboradores_editor e outras funções de Admin permanecem as mesmas)
# (Vou colar todas para o código ficar completo e funcional)
def paginated_dataframe(df, page_size=20, key_prefix=""):
    total_items = len(df)
    total_pages = math.ceil(total_items / page_size) if total_items > 0 else 1
    page_key = f"{key_prefix}_page"
    if page_key not in st.session_state: st.session_state[page_key] = 1
    current_page = st.session_state[page_key]
    if current_page > total_pages: current_page = total_pages
    start_idx = (current_page - 1) * page_size
    end_idx = start_idx + page_size
    st.dataframe(df.iloc[start_idx:end_idx], use_container_width=True, hide_index=True)
    st.write("")
    cols = st.columns([2, 2, 1, 4])
    if cols[0].button("⬅️ Anterior", key=f"{key_prefix}_prev", disabled=(current_page <= 1), use_container_width=True): st.session_state[page_key] -= 1; st.rerun()
    if cols[1].button("Próxima ➡️", key=f"{key_prefix}_next", disabled=(current_page >= total_pages), use_container_width=True): st.session_state[page_key] += 1; st.rerun()
    jump_page = cols[2].number_input("Pular para:", min_value=1, max_value=total_pages, value=current_page, key=f"{key_prefix}_jump")
    if jump_page != current_page: st.session_state[page_key] = jump_page; st.rerun()
    cols[3].markdown(f"<p style='text-align: right; margin-top: 2rem;'>Página {current_page} de {total_pages}</p>", unsafe_allow_html=True)

def display_colaboradores_editor(current_username, is_superadmin):
    st.subheader("Gerenciar Colaboradores")
    rest_query = "SELECT nome FROM restaurantes" + ("" if is_superadmin else " WHERE criado_por_admin = ?")
    params = None if is_superadmin else (current_username,)
    df_restaurantes = run_db_query(rest_query, params, fetch='dataframe')
    restaurants_options = df_restaurantes['nome'].tolist() if not df_restaurantes.empty else []

    with st.expander("➕ Adicionar Novo Colaborador"):
        with st.form("novo_colaborador_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                novo_id, novo_nome, novo_cpf = st.text_input("ID *"), st.text_input("Nome *"), st.text_input("CPF *")
            with col2:
                novo_cc, novo_os = st.text_input("Centro de Custo"), st.text_input("OS")
                st.markdown(":blue[2 REFEIÇÕES DIÁRIAS?]"); novo_duas_vezes = st.checkbox("Duas refeições")
            st.subheader("Restaurantes Permitidos")
            cols_rest = st.columns(3)
            restaurantes_selecionados = [restaurante for i, restaurante in enumerate(restaurants_options) if cols_rest[i % 3].checkbox(restaurante, key=f"novo_{restaurante}")]
            if st.form_submit_button("Adicionar Colaborador", type="primary"):
                if not all([novo_id, novo_nome, novo_cpf]):
                    st.error("ID, Nome e CPF são obrigatórios!")
                else:
                    cpf_limpo = re.sub(r'\D', '', novo_cpf)
                    cpf_existente = run_db_query("SELECT id FROM colaboradores WHERE cpf = ?", (cpf_limpo,), fetch='one')
                    if cpf_existente:
                        st.warning("CPF já cadastrado.")
                    else:
                        run_db_query("INSERT INTO colaboradores (id, nome, cpf, centro_custo, os, pode_duas_vezes, criado_por_admin, restaurantes_permitidos) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                     (novo_id, novo_nome, cpf_limpo, novo_cc, novo_os, 1 if novo_duas_vezes else 0, current_username, json.dumps(restaurantes_selecionados)))
                        st.success("Colaborador adicionado com sucesso!"); st.rerun()

    st.markdown("---"); st.subheader("Colaboradores Cadastrados")
    st.info("Crie colaboradores no formulário acima. Na tabela abaixo, edite os campos permitidos ou marque a caixa 'Deletar' e clique em 'Salvar Alterações' para remover um colaborador.")
    colab_query = "SELECT id, nome, cpf, centro_custo, os, pode_duas_vezes, restaurantes_permitidos, criado_por_admin FROM colaboradores" + ("" if is_superadmin else " WHERE criado_por_admin = ?")
    df_colab_original = run_db_query(colab_query, params, fetch='dataframe')
    if df_colab_original is None or df_colab_original.empty: return st.warning("Nenhum colaborador cadastrado.")
    
    df_para_editar = df_colab_original.copy()
    df_para_editar['Deletar'] = False
    df_para_editar['pode_duas_vezes'] = df_para_editar['pode_duas_vezes'].astype(bool)
    df_para_editar['Restaurantes'] = df_para_editar['restaurantes_permitidos'].apply(lambda x: ", ".join(json.loads(x or '[]')))
    colunas_visiveis = ['Deletar', 'id', 'nome', 'cpf', 'centro_custo', 'os', 'pode_duas_vezes', 'Restaurantes'] + (['criado_por_admin'] if is_superadmin else [])
    config_colunas = {
        "Deletar": st.column_config.CheckboxColumn("Deletar?", help="Marque para remover o colaborador ao salvar."),
        "id": st.column_config.TextColumn("ID", disabled=True), "nome": st.column_config.TextColumn("Nome", disabled=True),
        "cpf": st.column_config.TextColumn("CPF", disabled=True), "centro_custo": st.column_config.TextColumn("Centro de Custo"),
        "os": st.column_config.TextColumn("OS"), "pode_duas_vezes": st.column_config.CheckboxColumn("Pode 2 Refeições?"),
        "Restaurantes": st.column_config.TextColumn("Restaurantes Permitidos", disabled=True),
        "criado_por_admin": st.column_config.TextColumn("Criado Por", disabled=True),
    }
    edited_df = st.data_editor(df_para_editar[colunas_visiveis], num_rows="fixed", use_container_width=True, hide_index=True, column_config=config_colunas, key="colab_editor")
    if st.button("Salvar Alterações nos Colaboradores", type="primary"):
        deletados, atualizados = 0, 0
        ids_para_deletar = edited_df[edited_df['Deletar'] == True]['id'].tolist()
        if ids_para_deletar:
            for colab_id in ids_para_deletar:
                run_db_query("DELETE FROM colaboradores WHERE id = ?", (colab_id,))
                deletados += 1
        df_para_atualizar = edited_df[~edited_df['id'].isin(ids_para_deletar)]
        for _, row in df_para_atualizar.iterrows():
            pode_duas = 1 if row['pode_duas_vezes'] else 0
            run_db_query("UPDATE colaboradores SET centro_custo=?, os=?, pode_duas_vezes=? WHERE id=?",
                         (row.get('centro_custo'), row.get('os'), pode_duas, row['id']))
            atualizados += 1
        mensagem = f"{deletados} colaborador(es) removido(s)." if deletados > 0 else ""
        if mensagem: st.toast(mensagem, icon="✅")
        st.rerun()

def verificar_e_registrar_refeicao(restaurante, colaborador_info):
    colab_id, colab_nome, colab_cc, colab_os, pode_duas_vezes, restaurantes_permitidos_json = colaborador_info
    lista_permitida = json.loads(restaurantes_permitidos_json or '[]')
    if restaurante not in lista_permitida:
        return st.error(f"Acesso negado. **{colab_nome}** não tem permissão para **{restaurante}**.")
    datas_restaurante = run_db_query("SELECT data_inicio, data_fim FROM restaurantes WHERE nome = ?", (restaurante,), fetch='one')
    if not datas_restaurante or not all(datas_restaurante):
        return st.error(f"Acesso negado. Restaurante '{restaurante}' sem período de validade configurado.")
    hoje = date.today()
    if not (datetime.strptime(datas_restaurante[0], '%Y-%m-%d').date() <= hoje <= datetime.strptime(datas_restaurante[1], '%Y-%m-%d').date()):
        return st.error(f"Acesso negado. Período de validade do restaurante '{restaurante}' expirou ou não começou.")
    refeicoes_hoje_result = run_db_query("SELECT COUNT(*) FROM registros WHERE colaborador_id = ? AND DATE(data_hora) = ?", (colab_id, hoje.strftime("%Y-%m-%d")), fetch='one')
    refeicoes_hoje = refeicoes_hoje_result[0] if refeicoes_hoje_result else 0
    limite = 2 if pode_duas_vezes == 1 else 1
    if refeicoes_hoje < limite:
        br_timezone = pytz.timezone("America/Sao_Paulo")
        utc_now = datetime.now(pytz.utc)
        br_now = utc_now.astimezone(br_timezone)
        timestamp = br_now.strftime("%Y-%m-%d %H:%M:%S")
        run_db_query("INSERT INTO registros (restaurante, colaborador_nome, colaborador_id, centro_custo, os, data_hora) VALUES (?, ?, ?, ?, ?, ?)",
                     (restaurante, colab_nome, colab_id, colab_cc, colab_os, timestamp))
        st.success(f"✅ Acesso registrado para: **{colab_nome}**"); st.balloons()
    else:
        st.error(f"🚫 Limite de {limite} refeição(ões) diária(s) já atingido para **{colab_nome}**.")

def tela_1():
    st.sidebar.image("imagens/logo.png", width='stretch')
    st.sidebar.success(f"Logado como: {st.session_state.current_user}")
    if st.sidebar.button("Sair", use_container_width=True, type='primary'):
        st.session_state.clear(); st.rerun()

    # <<< ROTEAMENTO PRINCIPAL DA UI BASEADO NO CARGO DO USUÁRIO >>>
    
    # --- VISÃO DO ADMINISTRADOR ---
    if st.session_state.user_role == "admin":
        is_super, username = st.session_state.is_superadmin, st.session_state.current_username
        
        # <<< NOVO: Aba para gerenciar donos de restaurante >>>
        tab_titles = ["Registro", "Colaboradores", "Restaurantes", "Donos"] + (["Administradores"] if is_super else [])
        registro, colaboradores, restaurantes, donos, *admin_tab = st.tabs(tab_titles)
        
        with registro:
            st.markdown("### Registro de Refeição")
            rest_query = "SELECT nome FROM restaurantes" + ("" if is_super else " WHERE criado_por_admin = ?")
            params = None if is_super else (username,)
            df_restaurants = run_db_query(rest_query, params, fetch='dataframe')
            user_restaurants = df_restaurants['nome'].tolist() if df_restaurants is not None and not df_restaurants.empty else []
            if not user_restaurants:
                st.warning("Nenhum restaurante disponível.")
            else:
                selected_restaurant = st.selectbox("Selecione o Restaurante", user_restaurants)
                cpf_input = st.text_input("Digite o CPF do colaborador", key="cpf_input")
                if st.button("Registrar Refeição", key="btn_cpf", use_container_width=True, type="primary"):
                    if cpf_limpo := re.sub(r'\D', '', cpf_input):
                        found_collaborator = run_db_query("SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos FROM colaboradores WHERE cpf = ?", (cpf_limpo,), fetch='one')
                        if found_collaborator:
                            verificar_e_registrar_refeicao(selected_restaurant, found_collaborator)
                        else: st.error("CPF não encontrado.")
                    else: st.warning("Por favor, digite um CPF válido.")

        with colaboradores: display_colaboradores_editor(username, is_super)
        
        with restaurantes:
            st.subheader("Gerenciar Restaurantes")
            query = "SELECT nome, criado_por_admin, data_inicio, data_fim FROM restaurantes" + ("" if is_super else " WHERE criado_por_admin = ?")
            params = None if is_super else (username,)
            df_rest_original = run_db_query(query, params, fetch='dataframe')
            if df_rest_original is not None:
                df_rest = df_rest_original.copy()
                df_rest['data_inicio'] = pd.to_datetime(df_rest['data_inicio'], errors='coerce')
                df_rest['data_fim'] = pd.to_datetime(df_rest['data_fim'], errors='coerce')
                config = {"nome": st.column_config.TextColumn("Nome", required=True), "data_inicio": st.column_config.DateColumn("Início", format="DD/MM/YYYY", required=True),
                          "data_fim": st.column_config.DateColumn("Fim", format="DD/MM/YYYY", required=True), "criado_por_admin": st.column_config.TextColumn("Criado Por", disabled=True)}
                edited_df = st.data_editor(df_rest, num_rows="dynamic", key="rest_editor", use_container_width=True, hide_index=True, column_config=config)
                if st.button("Salvar Alterações nos Restaurantes", type="primary"):
                    original_names, edited_names = set(df_rest_original['nome']), set(edited_df['nome'].dropna())
                    for name in (original_names - edited_names): run_db_query("DELETE FROM restaurantes WHERE nome = ?", (name,))
                    for _, row in edited_df.iterrows():
                        if pd.isna(row['nome']): continue
                        data_i, data_f = format_date_for_db(row['data_inicio']), format_date_for_db(row['data_fim'])
                        if row['nome'] in original_names: run_db_query("UPDATE restaurantes SET data_inicio = ?, data_fim = ? WHERE nome = ?", (data_i, data_f, row['nome']))
                        else: run_db_query("INSERT INTO restaurantes (nome, criado_por_admin, data_inicio, data_fim) VALUES (?, ?, ?, ?)", (row['nome'], username, data_i, data_f))
                    st.success("Restaurantes atualizados!"); st.rerun()
        
        with donos:
            st.subheader("Gerenciar Donos de Restaurantes")
            df_owners_original = run_db_query("SELECT username, nome, senha, restaurante, criado_por_admin FROM donos_restaurante", fetch='dataframe')
            
            # Pega a lista de restaurantes para o dropdown
            all_restaurants = run_db_query("SELECT nome FROM restaurantes", fetch='all')
            restaurant_choices = [r[0] for r in all_restaurants] if all_restaurants else []

            if df_owners_original is not None:
                config = {
                    "username": st.column_config.TextColumn("Username", required=True),
                    "nome": st.column_config.TextColumn("Nome", required=True),
                    "senha": st.column_config.TextColumn("Senha", required=True, type="password"),
                    "restaurante": st.column_config.SelectboxColumn("Restaurante", options=restaurant_choices, required=True),
                    "criado_por_admin": st.column_config.TextColumn("Criado Por", disabled=True)
                }
                edited_df = st.data_editor(df_owners_original, num_rows="dynamic", key="owner_editor", use_container_width=True, hide_index=True, column_config=config)
                if st.button("Salvar Donos de Restaurantes", type="primary"):
                    original_users, edited_users = set(df_owners_original['username']), set(edited_df['username'].dropna())
                    for user in (original_users - edited_users): run_db_query("DELETE FROM donos_restaurante WHERE username = ?", (user,))
                    for _, row in edited_df.iterrows():
                        if pd.isna(row['username']): continue
                        if row['username'] in original_users:
                            run_db_query("UPDATE donos_restaurante SET nome = ?, senha = ?, restaurante = ? WHERE username = ?", (row['nome'], row['senha'], row['restaurante'], row['username']))
                        else:
                            run_db_query("INSERT INTO donos_restaurante (username, nome, senha, restaurante, criado_por_admin) VALUES (?, ?, ?, ?, ?)", (row['username'], row['nome'], row['senha'], row['restaurante'], username))
                    st.success("Donos de restaurante atualizados!"); st.rerun()

        if is_super:
            with admin_tab[0]:
                st.subheader("Gerenciar Administradores")
                # ... (código de gerenciar admins)
        
        st.markdown("---"); st.markdown("### Relatório de Refeições")
        # ... (código dos relatórios)

    # --- VISÃO DO DONO DE RESTAURANTE ---
    elif st.session_state.user_role == "owner":
        st.markdown(f"### Registro de Refeição - {st.session_state.user_restaurant}")
        
        selected_restaurant = st.session_state.user_restaurant
        cpf_input = st.text_input("Digite o CPF do colaborador", key="cpf_input")
        
        if st.button("Registrar Refeição", key="btn_cpf_owner", use_container_width=True, type="primary"):
            if cpf_limpo := re.sub(r'\D', '', cpf_input):
                found_collaborator = run_db_query("SELECT id, nome, centro_custo, os, pode_duas_vezes, restaurantes_permitidos FROM colaboradores WHERE cpf = ?", (cpf_limpo,), fetch='one')
                if found_collaborator:
                    verificar_e_registrar_refeicao(selected_restaurant, found_collaborator)
                else: st.error("CPF não encontrado.")
            else: st.warning("Por favor, digite um CPF válido.")

# --- ROTEADOR PRINCIPAL ---
if 'db_initialized' not in st.session_state:
    init_db()
    st.session_state.db_initialized = True
if not st.session_state.logged_in:
    tela_login()
else:
    tela_1()