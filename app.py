# ============================================================
# app.py - Sistema de Gestão de Planos de Ação
# Com upload de evidências (PDF, JPEG, PNG) e visualização
# CORREÇÃO: URL via url_for, caminhos relativos e MIME types
# ============================================================

import os
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, flash, session, jsonify, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import logging
import mimetypes

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'chave-secreta-para-desenvolvimento'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB (suporta 10MB para imagens)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ==================== BANCO DE DADOS ====================
DB_DIR = os.path.join(os.path.dirname(__file__), 'instance')
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, 'planos_acao.db')
logger.info(f"Banco de dados em: {DB_PATH}")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # --- Tabela usuarios ---
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        senha TEXT NOT NULL,
        perfil TEXT DEFAULT 'usuario',
        ativo INTEGER DEFAULT 0,
        primeiro_acesso INTEGER DEFAULT 1,
        departamento TEXT,
        cargo TEXT,
        data_vigencia DATE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    try:
        c.execute('SELECT primeiro_acesso FROM usuarios LIMIT 1')
    except sqlite3.OperationalError:
        c.execute('ALTER TABLE usuarios ADD COLUMN primeiro_acesso INTEGER DEFAULT 1')
    
    # --- Tabela empresas ---
    c.execute('''CREATE TABLE IF NOT EXISTS empresas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE NOT NULL,
        ativo INTEGER DEFAULT 1
    )''')
    
    # --- Tabela planos_categorias ---
    c.execute('''CREATE TABLE IF NOT EXISTS planos_categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE NOT NULL,
        descricao TEXT,
        ativo INTEGER DEFAULT 1
    )''')
    
    # --- Tabela wbs ---
    c.execute('''CREATE TABLE IF NOT EXISTS wbs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        descricao TEXT,
        empresa_id INTEGER NOT NULL,
        plano_id INTEGER NOT NULL,
        ativo INTEGER DEFAULT 1,
        critica INTEGER DEFAULT 0,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id),
        FOREIGN KEY (plano_id) REFERENCES planos_categorias(id)
    )''')
    
    try:
        c.execute('SELECT critica FROM wbs LIMIT 1')
    except sqlite3.OperationalError:
        c.execute('ALTER TABLE wbs ADD COLUMN critica INTEGER DEFAULT 0')
    
    # --- Tabela atividades ---
    c.execute('''CREATE TABLE IF NOT EXISTS atividades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        acao TEXT NOT NULL,
        lb_inicio DATE NOT NULL,
        lb_fim DATE NOT NULL,
        inicio_real DATE,
        fim_real DATE,
        status TEXT DEFAULT 'pendente',
        responsavel TEXT NOT NULL,
        autor TEXT NOT NULL,
        wbs_id INTEGER NOT NULL,
        macro INTEGER DEFAULT 0,
        criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
        atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (wbs_id) REFERENCES wbs(id)
    )''')
    
    # --- Tabela notificacoes ---
    c.execute('''CREATE TABLE IF NOT EXISTS notificacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER,
        mensagem TEXT,
        lida INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # --- Tabela evidencias (para upload de arquivos) ---
    c.execute('''CREATE TABLE IF NOT EXISTS evidencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        atividade_id INTEGER NOT NULL,
        nome_original TEXT NOT NULL,
        nome_arquivo TEXT NOT NULL,
        caminho TEXT NOT NULL,
        tipo_arquivo TEXT NOT NULL,
        tamanho INTEGER NOT NULL,
        upload_por TEXT NOT NULL,
        upload_em DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (atividade_id) REFERENCES atividades(id) ON DELETE CASCADE
    )''')
    
    # --- Dados iniciais ---
    empresas_padrao = ['EPB', 'EMT', 'EMR', 'ERO', 'ETO', 'EMS', 'ESE', 'ESS']
    for emp in empresas_padrao:
        c.execute('INSERT OR IGNORE INTO empresas (nome) VALUES (?)', (emp,))
    
    planos_padrao = [
        ('Eficiência Operacional', 'Planos relacionados à eficiência'),
        ('Gestão', 'Planos de gestão administrativa'),
        ('Inteligência Operacional', 'Planos de inteligência e análise'),
        ('Padronização Operacional', 'Planos de padronização de processos'),
        ('Representação do Negócio', 'Planos de representação comercial'),
        ('Resiliência Operacional', 'Planos de resiliência e continuidade')
    ]
    for nome, desc in planos_padrao:
        c.execute('INSERT OR IGNORE INTO planos_categorias (nome, descricao) VALUES (?, ?)', (nome, desc))
    
    # Admin padrão
    c.execute('SELECT id FROM usuarios WHERE email = "admin@empresa.com"')
    if not c.fetchone():
        senha_hash = generate_password_hash('admin123')
        c.execute('''INSERT INTO usuarios (nome, email, senha, perfil, ativo, primeiro_acesso)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  ('Administrador', 'admin@empresa.com', senha_hash, 'admin', 1, 0))
        logger.info("✅ Admin criado: admin@empresa.com / admin123")
    
    conn.commit()
    conn.close()
    logger.info("✅ Banco de dados inicializado com sucesso")

init_db()

# ==================== FUNÇÕES AUXILIARES ====================
def formatar_data(data_str):
    if not data_str:
        return '-'
    try:
        data = datetime.strptime(data_str, '%Y-%m-%d')
        return data.strftime('%d/%m/%Y')
    except:
        return data_str

def formatar_data_hora(data_str):
    if not data_str:
        return '-'
    try:
        data = datetime.strptime(data_str, '%Y-%m-%d %H:%M:%S')
        return data.strftime('%d/%m/%Y %H:%M')
    except:
        return data_str

def tamanho_legivel(bytes):
    for unidade in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unidade}"
        bytes /= 1024.0
    return f"{bytes:.1f} TB"

# ==================== FUNÇÕES DE USUÁRIO ====================
def get_usuario(email):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM usuarios WHERE email = ?', (email,))
    user = c.fetchone()
    conn.close()
    return dict(user) if user else None

def get_usuario_by_id(id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM usuarios WHERE id = ?', (id,))
    user = c.fetchone()
    conn.close()
    return dict(user) if user else None

def listar_usuarios():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT id, nome, email, perfil, ativo, primeiro_acesso, departamento, cargo, data_vigencia FROM usuarios ORDER BY nome')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def criar_usuario(nome, email, senha, perfil='usuario', departamento='', cargo='', data_vigencia=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    senha_hash = generate_password_hash(senha)
    try:
        c.execute('''INSERT INTO usuarios (nome, email, senha, perfil, departamento, cargo, data_vigencia, ativo, primeiro_acesso)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (nome, email, senha_hash, perfil, departamento, cargo, data_vigencia, 1, 1))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def atualizar_senha(email, nova_senha):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    senha_hash = generate_password_hash(nova_senha)
    c.execute('UPDATE usuarios SET senha = ?, primeiro_acesso = 0 WHERE email = ?', (senha_hash, email))
    conn.commit()
    conn.close()

def resetar_senha(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    senha_hash = generate_password_hash('1234')
    c.execute('UPDATE usuarios SET senha = ?, primeiro_acesso = 1 WHERE email = ?', (senha_hash, email))
    conn.commit()
    conn.close()

def ativar_usuario(id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE usuarios SET ativo = 1 WHERE id = ?', (id,))
    conn.commit()
    conn.close()

def desativar_usuario(id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE usuarios SET ativo = 0 WHERE id = ?', (id,))
    conn.commit()
    conn.close()

def deletar_usuario(id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM usuarios WHERE id = ?', (id,))
    conn.commit()
    conn.close()

# ==================== FUNÇÕES PARA EMPRESAS, PLANOS, WBS E ATIVIDADES ====================
def get_empresas():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM empresas WHERE ativo = 1 ORDER BY nome')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_planos_categorias():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM planos_categorias WHERE ativo = 1 ORDER BY nome')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_wbs(empresa_id=None, plano_id=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = 'SELECT w.*, e.nome as empresa_nome, p.nome as plano_nome FROM wbs w JOIN empresas e ON w.empresa_id = e.id JOIN planos_categorias p ON w.plano_id = p.id WHERE w.ativo = 1'
    params = []
    if empresa_id:
        query += ' AND w.empresa_id = ?'
        params.append(empresa_id)
    if plano_id:
        query += ' AND w.plano_id = ?'
        params.append(plano_id)
    query += ' ORDER BY e.nome, p.nome, w.nome'
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_atividades(wbs_id=None, macro=None, status=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = '''SELECT a.*, w.nome as wbs_nome, e.nome as empresa_nome, p.nome as plano_nome 
               FROM atividades a 
               JOIN wbs w ON a.wbs_id = w.id 
               JOIN empresas e ON w.empresa_id = e.id 
               JOIN planos_categorias p ON w.plano_id = p.id 
               WHERE 1=1'''
    params = []
    if wbs_id:
        query += ' AND a.wbs_id = ?'
        params.append(wbs_id)
    if macro is not None:
        query += ' AND a.macro = ?'
        params.append(macro)
    if status:
        query += ' AND a.status = ?'
        params.append(status)
    query += ' ORDER BY a.lb_fim ASC'
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def criar_empresa(nome):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO empresas (nome) VALUES (?)', (nome,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def criar_plano_categoria(nome, descricao=''):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO planos_categorias (nome, descricao) VALUES (?, ?)', (nome, descricao))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def criar_wbs(nome, descricao, empresa_id, plano_id, critica=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO wbs (nome, descricao, empresa_id, plano_id, critica) VALUES (?, ?, ?, ?, ?)',
              (nome, descricao, empresa_id, plano_id, critica))
    conn.commit()
    wbs_id = c.lastrowid
    conn.close()
    return wbs_id

def calcular_status_atividade(lb_inicio, lb_fim, inicio_real, fim_real):
    hoje = datetime.now().date()
    if lb_fim:
        try:
            lb_fim_dt = datetime.strptime(lb_fim, '%Y-%m-%d').date()
        except:
            lb_fim_dt = None
    else:
        lb_fim_dt = None
    if fim_real:
        try:
            fim_real_dt = datetime.strptime(fim_real, '%Y-%m-%d').date()
        except:
            fim_real_dt = None
    else:
        fim_real_dt = None
    if inicio_real:
        try:
            inicio_real_dt = datetime.strptime(inicio_real, '%Y-%m-%d').date()
        except:
            inicio_real_dt = None
    else:
        inicio_real_dt = None
    
    if fim_real_dt:
        if lb_fim_dt and fim_real_dt <= lb_fim_dt:
            return 'Concluído Dentro do Prazo'
        else:
            return 'Concluído Fora do Prazo'
    else:
        if lb_fim_dt:
            if hoje <= lb_fim_dt:
                return 'Dentro do Prazo'
            else:
                return 'Fora do Prazo'
        else:
            return 'Pendente'

def criar_atividade(acao, lb_inicio, lb_fim, inicio_real, fim_real, responsavel, autor, wbs_id, macro=0):
    status = calcular_status_atividade(lb_inicio, lb_fim, inicio_real, fim_real)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO atividades (acao, lb_inicio, lb_fim, inicio_real, fim_real, status, responsavel, autor, wbs_id, macro)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (acao, lb_inicio, lb_fim, inicio_real, fim_real, status, responsavel, autor, wbs_id, macro))
    conn.commit()
    atividade_id = c.lastrowid
    conn.close()
    return atividade_id

def atualizar_atividade(id, acao, lb_inicio, lb_fim, inicio_real, fim_real, responsavel, autor, macro):
    status = calcular_status_atividade(lb_inicio, lb_fim, inicio_real, fim_real)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''UPDATE atividades SET 
                 acao=?, lb_inicio=?, lb_fim=?, inicio_real=?, fim_real=?, 
                 status=?, responsavel=?, autor=?, macro=?, atualizado_em=CURRENT_TIMESTAMP
                 WHERE id=?''',
              (acao, lb_inicio, lb_fim, inicio_real, fim_real, status, responsavel, autor, macro, id))
    conn.commit()
    conn.close()

def deletar_atividade(id):
    # Remove evidências associadas
    evidencias = get_evidencias_by_atividade(id)
    for ev in evidencias:
        try:
            os.remove(ev['caminho'])
        except:
            pass
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM evidencias WHERE atividade_id = ?', (id,))
    c.execute('DELETE FROM atividades WHERE id = ?', (id,))
    conn.commit()
    conn.close()

def get_indicadores(empresa_id=None, plano_id=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = '''SELECT e.nome as empresa, p.nome as plano, 
                      COUNT(a.id) as total,
                      SUM(CASE WHEN a.status LIKE 'Concluído%' THEN 1 ELSE 0 END) as concluidos,
                      SUM(CASE WHEN a.status = 'Fora do Prazo' THEN 1 ELSE 0 END) as atrasados,
                      SUM(CASE WHEN a.status = 'Dentro do Prazo' THEN 1 ELSE 0 END) as dentro_prazo,
                      SUM(CASE WHEN a.status NOT LIKE 'Concluído%' AND a.status != 'Fora do Prazo' AND a.status != 'Dentro do Prazo' THEN 1 ELSE 0 END) as pendentes
               FROM atividades a
               JOIN wbs w ON a.wbs_id = w.id
               JOIN empresas e ON w.empresa_id = e.id
               JOIN planos_categorias p ON w.plano_id = p.id
               WHERE 1=1
    '''
    params = []
    if empresa_id:
        query += ' AND e.id = ?'
        params.append(empresa_id)
    if plano_id:
        query += ' AND p.id = ?'
        params.append(plano_id)
    query += ' GROUP BY e.nome, p.nome'
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ==================== FUNÇÕES PARA EVIDÊNCIAS ====================
def get_evidencias_by_atividade(atividade_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM evidencias WHERE atividade_id = ? ORDER BY upload_em DESC', (atividade_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_evidencia_by_id(evidencia_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM evidencias WHERE id = ?', (evidencia_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def salvar_evidencia(atividade_id, arquivo, usuario_nome):
    # Valida extensão
    nome_original = arquivo.filename
    ext = nome_original.rsplit('.', 1)[-1].lower() if '.' in nome_original else ''
    if ext not in ['pdf', 'jpeg', 'png']:
        raise ValueError("Formato não permitido. Use .pdf, .jpeg ou .png.")
    
    # Valida tamanho (10MB para imagens, PDF pode ser maior)
    arquivo.seek(0, os.SEEK_END)
    tamanho = arquivo.tell()
    arquivo.seek(0)
    if ext in ['jpeg', 'png'] and tamanho > 10 * 1024 * 1024:
        raise ValueError("Imagens devem ter no máximo 10MB.")
    if tamanho > 16 * 1024 * 1024:
        raise ValueError("Arquivo muito grande (máximo 16MB).")
    
    # Cria pasta para a atividade
    pasta_atividade = os.path.join(app.config['UPLOAD_FOLDER'], str(atividade_id))
    os.makedirs(pasta_atividade, exist_ok=True)
    
    # Gera nome único para o arquivo
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    nome_arquivo = f"{timestamp}_{secure_filename(nome_original)}"
    # Caminho relativo à pasta uploads (para facilitar a URL)
    caminho_relativo = os.path.join(str(atividade_id), nome_arquivo)
    caminho_absoluto = os.path.join(app.config['UPLOAD_FOLDER'], caminho_relativo)
    
    # Salva arquivo
    arquivo.save(caminho_absoluto)
    
    # Registra no banco (armazena caminho relativo)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO evidencias (atividade_id, nome_original, nome_arquivo, caminho, tipo_arquivo, tamanho, upload_por)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (atividade_id, nome_original, nome_arquivo, caminho_relativo, ext, tamanho, usuario_nome))
    conn.commit()
    evidencia_id = c.lastrowid
    conn.close()
    return evidencia_id

def deletar_evidencia(evidencia_id):
    ev = get_evidencia_by_id(evidencia_id)
    if not ev:
        return False
    caminho_absoluto = os.path.join(app.config['UPLOAD_FOLDER'], ev['caminho'])
    try:
        os.remove(caminho_absoluto)
    except:
        pass
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM evidencias WHERE id = ?', (evidencia_id,))
    conn.commit()
    conn.close()
    return True

# ==================== FUNÇÃO DE IMPORTAÇÃO ====================
def importar_wbs_planilha(filepath):
    if filepath.endswith('.xlsx') or filepath.endswith('.xls'):
        df = pd.read_excel(filepath)
    else:
        df = pd.read_csv(filepath)
    
    df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
    
    required = ['empresa', 'plano', 'wbs', 'acao', 'lb_inicio', 'lb_fim', 'responsavel']
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Colunas obrigatórias faltando: {', '.join(missing)}")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    empresas_cache = {}
    planos_cache = {}
    wbs_cache = {}
    
    linhas_processadas = 0
    for idx, row in df.iterrows():
        try:
            empresa_nome = str(row['empresa']).strip()
            if not empresa_nome:
                continue
            if empresa_nome not in empresas_cache:
                c.execute('SELECT id FROM empresas WHERE nome = ?', (empresa_nome,))
                result = c.fetchone()
                if not result:
                    c.execute('INSERT INTO empresas (nome) VALUES (?)', (empresa_nome,))
                    conn.commit()
                    empresa_id = c.lastrowid
                    empresas_cache[empresa_nome] = empresa_id
                else:
                    empresas_cache[empresa_nome] = result[0]
            empresa_id = empresas_cache[empresa_nome]
            
            plano_nome = str(row['plano']).strip()
            if not plano_nome:
                continue
            if plano_nome not in planos_cache:
                c.execute('SELECT id FROM planos_categorias WHERE nome = ?', (plano_nome,))
                result = c.fetchone()
                if not result:
                    c.execute('INSERT INTO planos_categorias (nome) VALUES (?)', (plano_nome,))
                    conn.commit()
                    plano_id = c.lastrowid
                    planos_cache[plano_nome] = plano_id
                else:
                    planos_cache[plano_nome] = result[0]
            plano_id = planos_cache[plano_nome]
            
            wbs_nome = str(row['wbs']).strip()
            if not wbs_nome:
                continue
            chave_wbs = (empresa_id, plano_id, wbs_nome)
            if chave_wbs not in wbs_cache:
                c.execute('SELECT id FROM wbs WHERE nome = ? AND empresa_id = ? AND plano_id = ?', 
                          (wbs_nome, empresa_id, plano_id))
                result = c.fetchone()
                if not result:
                    descricao_wbs = str(row.get('descricao_wbs', '')) if 'descricao_wbs' in row else ''
                    critica = 0
                    if 'critica' in row:
                        val = str(row['critica']).strip().lower()
                        if val in ['sim', 's', '1', 'verdadeiro', 'true']:
                            critica = 1
                    c.execute('INSERT INTO wbs (nome, descricao, empresa_id, plano_id, critica) VALUES (?, ?, ?, ?, ?)',
                              (wbs_nome, descricao_wbs, empresa_id, plano_id, critica))
                    conn.commit()
                    wbs_id = c.lastrowid
                    wbs_cache[chave_wbs] = wbs_id
                else:
                    wbs_cache[chave_wbs] = result[0]
            wbs_id = wbs_cache[chave_wbs]
            
            acao = str(row['acao']).strip()
            if not acao:
                continue
            
            def parse_date(val):
                if pd.isna(val):
                    return None
                if isinstance(val, datetime):
                    return val.strftime('%Y-%m-%d')
                if isinstance(val, str):
                    try:
                        return datetime.strptime(val.strip(), '%Y-%m-%d').strftime('%Y-%m-%d')
                    except:
                        return None
                return None
            
            lb_inicio = parse_date(row['lb_inicio'])
            lb_fim = parse_date(row['lb_fim'])
            if not lb_inicio or not lb_fim:
                raise ValueError(f"Data inválida na linha {idx+2}")
            
            inicio_real = parse_date(row.get('inicio_real')) if 'inicio_real' in row else None
            fim_real = parse_date(row.get('fim_real')) if 'fim_real' in row else None
            
            responsavel = str(row['responsavel']).strip()
            autor = str(row.get('autor', 'importação')).strip() if 'autor' in row else 'importação'
            macro = 0
            if 'macro' in row:
                val = str(row['macro']).strip().lower()
                if val in ['sim', 's', '1', 'verdadeiro', 'true']:
                    macro = 1
            
            status = calcular_status_atividade(lb_inicio, lb_fim, inicio_real, fim_real)
            c.execute('''INSERT INTO atividades (acao, lb_inicio, lb_fim, inicio_real, fim_real, status, responsavel, autor, wbs_id, macro)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (acao, lb_inicio, lb_fim, inicio_real, fim_real, status, responsavel, autor, wbs_id, macro))
            conn.commit()
            linhas_processadas += 1
        except Exception as e:
            logger.error(f"Erro na linha {idx+2}: {e}")
            raise ValueError(f"Erro na linha {idx+2}: {str(e)}")
    
    conn.close()
    return linhas_processadas

# ==================== FUNÇÃO AUXILIAR PARA VALORES ÚNICOS ====================
def get_unique_values(column, table='atividades', where=None, where_params=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = f"SELECT DISTINCT {column} FROM {table}"
    if where:
        query += f" WHERE {where}"
    if where_params:
        c.execute(query, where_params)
    else:
        c.execute(query)
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows if row[0] is not None and row[0] != '']

# ==================== DECORATOR DE PERMISSÃO ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Sua sessão expirou. Faça login novamente.', 'warning')
            return redirect(url_for('login'))
        user = get_usuario_by_id(session.get('usuario_id'))
        if not user or not user.get('ativo', 0):
            session.clear()
            flash('Usuário não encontrado ou inativo. Faça login novamente.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Sua sessão expirou. Faça login novamente.', 'warning')
            return redirect(url_for('login'))
        if session.get('perfil') != 'admin':
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def master_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Sua sessão expirou. Faça login novamente.', 'warning')
            return redirect(url_for('login'))
        perfil = session.get('perfil')
        if perfil not in ['admin', 'master']:
            flash('Acesso restrito a administradores e master.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== ROTAS DE AUTENTICAÇÃO ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')
        user = get_usuario(email)
        if not user:
            flash('E-mail não encontrado.', 'danger')
            return render_template_string(LOGIN_PAGE)
        if not user.get('ativo', 0):
            flash('Conta desativada. Contate o administrador.', 'danger')
            return render_template_string(LOGIN_PAGE)
        if check_password_hash(user['senha'], senha):
            session.permanent = False
            session['usuario_id'] = user['id']
            session['email'] = user['email']
            session['nome'] = user['nome']
            session['perfil'] = user['perfil']
            if user.get('primeiro_acesso', 1):
                flash('Este é seu primeiro acesso. Por favor, altere sua senha.', 'warning')
                return redirect(url_for('alterar_senha'))
            flash(f'Bem-vindo, {user["nome"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Senha incorreta.', 'danger')
    return render_template_string(LOGIN_PAGE)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logout realizado. Até logo!', 'info')
    return redirect(url_for('login'))

@app.route('/alterar-senha', methods=['GET', 'POST'])
@login_required
def alterar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        confirmar = request.form.get('confirmar_senha')
        user = get_usuario_by_id(session['usuario_id'])
        if not check_password_hash(user['senha'], senha_atual):
            flash('Senha atual incorreta.', 'danger')
            return render_template_string(ALTERAR_SENHA_PAGE)
        if nova_senha != confirmar:
            flash('As senhas não coincidem.', 'danger')
            return render_template_string(ALTERAR_SENHA_PAGE)
        if len(nova_senha) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'danger')
            return render_template_string(ALTERAR_SENHA_PAGE)
        atualizar_senha(user['email'], nova_senha)
        flash('Senha alterada com sucesso!', 'success')
        return redirect(url_for('dashboard'))
    return render_template_string(ALTERAR_SENHA_PAGE)

# ==================== ROTAS DE ADMINISTRAÇÃO ====================
@app.route('/admin/usuarios')
@admin_required
def admin_usuarios():
    usuarios = listar_usuarios()
    conteudo = f'''
    <div class="page-title"><i class="fas fa-users"></i> Gerenciar Usuários</div>
    <div class="card">
        <div style="margin-bottom:1rem;">
            <a href="/admin/usuario/novo" class="btn btn-success"><i class="fas fa-plus"></i> Novo Usuário</a>
        </div>
        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Nome</th>
                        <th>E-mail</th>
                        <th>Perfil</th>
                        <th>Status</th>
                        <th>Primeiro Acesso</th>
                        <th>Ações</th>
                    </tr>
                </thead>
                <tbody>
    '''
    for u in usuarios:
        status = 'Ativo' if u['ativo'] else 'Inativo'
        primeiro = 'Sim' if u['primeiro_acesso'] else 'Não'
        conteudo += f'''
        <tr>
            <td>{u['id']}</td>
            <td>{u['nome']}</td>
            <td>{u['email']}</td>
            <td><span class="badge badge-{u['perfil']}">{u['perfil']}</span></td>
            <td>{status}</td>
            <td>{primeiro}</td>
            <td>
                <a href="/admin/usuario/editar/{u['id']}" class="btn btn-warning btn-sm"><i class="fas fa-edit"></i></a>
                <a href="/admin/usuario/resetar/{u['id']}" class="btn btn-primary btn-sm" onclick="return confirm('Resetar senha para 1234?')"><i class="fas fa-key"></i></a>
                <a href="/admin/usuario/toggle/{u['id']}" class="btn btn-sm {'btn-success' if u['ativo'] else 'btn-danger'}" onclick="return confirm('Confirmar?')">
                    <i class="fas {'fa-check' if u['ativo'] else 'fa-times'}"></i>
                </a>
                <a href="/admin/usuario/deletar/{u['id']}" class="btn btn-danger btn-sm" onclick="return confirm('Tem certeza?')"><i class="fas fa-trash"></i></a>
            </td>
        </tr>
        '''
    conteudo += '''
                </tbody>
            </table>
        </div>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

@app.route('/admin/usuario/novo', methods=['GET', 'POST'])
@admin_required
def admin_novo_usuario():
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']
        perfil = request.form['perfil']
        departamento = request.form.get('departamento', '')
        cargo = request.form.get('cargo', '')
        if criar_usuario(nome, email, senha, perfil, departamento, cargo):
            flash('Usuário criado com sucesso!', 'success')
        else:
            flash('E-mail já cadastrado.', 'danger')
        return redirect(url_for('admin_usuarios'))
    conteudo = '''
    <div class="page-title"><i class="fas fa-user-plus"></i> Novo Usuário</div>
    <div class="card">
        <form method="post">
            <div class="form-group">
                <label>Nome *</label>
                <input type="text" name="nome" class="form-control" required>
            </div>
            <div class="form-group">
                <label>E-mail *</label>
                <input type="email" name="email" class="form-control" required>
            </div>
            <div class="form-group">
                <label>Senha *</label>
                <input type="password" name="senha" class="form-control" required>
            </div>
            <div class="form-group">
                <label>Perfil *</label>
                <select name="perfil" class="form-control" required>
                    <option value="admin">Administrador</option>
                    <option value="master">Master</option>
                    <option value="usuario">Usuário</option>
                </select>
            </div>
            <div class="form-group">
                <label>Departamento</label>
                <input type="text" name="departamento" class="form-control">
            </div>
            <div class="form-group">
                <label>Cargo</label>
                <input type="text" name="cargo" class="form-control">
            </div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success"><i class="fas fa-save"></i> Salvar</button>
                <a href="/admin/usuarios" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

@app.route('/admin/usuario/editar/<int:id>', methods=['GET', 'POST'])
@admin_required
def admin_editar_usuario(id):
    user = get_usuario_by_id(id)
    if not user:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('admin_usuarios'))
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        perfil = request.form['perfil']
        departamento = request.form.get('departamento', '')
        cargo = request.form.get('cargo', '')
        senha = request.form.get('senha')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        if senha:
            senha_hash = generate_password_hash(senha)
            c.execute('''UPDATE usuarios SET nome=?, email=?, perfil=?, departamento=?, cargo=?, senha=?
                         WHERE id=?''',
                      (nome, email, perfil, departamento, cargo, senha_hash, id))
        else:
            c.execute('''UPDATE usuarios SET nome=?, email=?, perfil=?, departamento=?, cargo=?
                         WHERE id=?''',
                      (nome, email, perfil, departamento, cargo, id))
        conn.commit()
        conn.close()
        flash('Usuário atualizado.', 'success')
        return redirect(url_for('admin_usuarios'))
    conteudo = f'''
    <div class="page-title"><i class="fas fa-user-edit"></i> Editar Usuário</div>
    <div class="card">
        <form method="post">
            <div class="form-group">
                <label>Nome *</label>
                <input type="text" name="nome" class="form-control" value="{user['nome']}" required>
            </div>
            <div class="form-group">
                <label>E-mail *</label>
                <input type="email" name="email" class="form-control" value="{user['email']}" required>
            </div>
            <div class="form-group">
                <label>Perfil *</label>
                <select name="perfil" class="form-control" required>
                    <option value="admin" {'selected' if user['perfil']=='admin' else ''}>Administrador</option>
                    <option value="master" {'selected' if user['perfil']=='master' else ''}>Master</option>
                    <option value="usuario" {'selected' if user['perfil']=='usuario' else ''}>Usuário</option>
                </select>
            </div>
            <div class="form-group">
                <label>Departamento</label>
                <input type="text" name="departamento" class="form-control" value="{user.get('departamento','')}">
            </div>
            <div class="form-group">
                <label>Cargo</label>
                <input type="text" name="cargo" class="form-control" value="{user.get('cargo','')}">
            </div>
            <div class="form-group">
                <label>Nova Senha (opcional)</label>
                <input type="password" name="senha" class="form-control" placeholder="Deixe em branco para manter">
            </div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success"><i class="fas fa-save"></i> Salvar</button>
                <a href="/admin/usuarios" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

@app.route('/admin/usuario/resetar/<int:id>')
@admin_required
def admin_resetar_senha(id):
    user = get_usuario_by_id(id)
    if user:
        resetar_senha(user['email'])
        flash(f'Senha de {user["nome"]} resetada para 1234.', 'success')
    return redirect(url_for('admin_usuarios'))

@app.route('/admin/usuario/toggle/<int:id>')
@admin_required
def admin_toggle_usuario(id):
    user = get_usuario_by_id(id)
    if user:
        if user['ativo']:
            desativar_usuario(id)
            flash(f'Usuário {user["nome"]} desativado.', 'warning')
        else:
            ativar_usuario(id)
            flash(f'Usuário {user["nome"]} ativado.', 'success')
    return redirect(url_for('admin_usuarios'))

@app.route('/admin/usuario/deletar/<int:id>')
@admin_required
def admin_deletar_usuario(id):
    if id == session.get('usuario_id'):
        flash('Você não pode deletar a própria conta.', 'danger')
    else:
        deletar_usuario(id)
        flash('Usuário deletado.', 'success')
    return redirect(url_for('admin_usuarios'))

# ==================== ADMIN: GERENCIAR EMPRESAS, PLANOS, WBS ====================
@app.route('/admin/configuracoes')
@admin_required
def admin_configuracoes():
    empresas = get_empresas()
    planos = get_planos_categorias()
    wbs_list = get_wbs()
    conteudo = f'''
    <div class="page-title"><i class="fas fa-cog"></i> Configurações</div>
    <div class="card">
        <h3>Empresas</h3>
        <a href="/admin/empresa/novo" class="btn btn-primary btn-sm">Nova Empresa</a>
        <ul>
        {''.join(f'<li>{e["nome"]}</li>' for e in empresas)}
        </ul>
    </div>
    <div class="card">
        <h3>Planos</h3>
        <a href="/admin/plano/novo" class="btn btn-primary btn-sm">Novo Plano</a>
        <ul>
        {''.join(f'<li>{p["nome"]}</li>' for p in planos)}
        </ul>
    </div>
    <div class="card">
        <h3>WBS</h3>
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:0.5rem;">
            <a href="/admin/wbs/novo" class="btn btn-primary btn-sm">Nova WBS</a>
        </div>
        <table class="table-responsive" style="width:100%;">
            <thead>
                <tr><th>Nome</th><th>Empresa</th><th>Plano</th><th>Crítica</th></tr>
            </thead>
            <tbody>
    '''
    if not wbs_list:
        conteudo += '<tr><td colspan="4" class="empty-state">Nenhuma WBS cadastrada.</td></tr>'
    else:
        for w in wbs_list:
            critica_text = 'Sim' if w.get('critica', 0) else 'Não'
            conteudo += f'<tr><td>{w["nome"]}</td><td>{w["empresa_nome"]}</td><td>{w["plano_nome"]}</td><td>{critica_text}</td></tr>'
    conteudo += '''
            </tbody>
        </table>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

@app.route('/admin/empresa/novo', methods=['GET', 'POST'])
@admin_required
def admin_nova_empresa():
    if request.method == 'POST':
        nome = request.form['nome'].strip().upper()
        if nome:
            if criar_empresa(nome):
                flash('Empresa criada.', 'success')
            else:
                flash('Empresa já existe.', 'danger')
        return redirect(url_for('admin_configuracoes'))
    conteudo = '''
    <div class="page-title"><i class="fas fa-building"></i> Nova Empresa</div>
    <div class="card">
        <form method="post">
            <div class="form-group">
                <label>Nome da Empresa *</label>
                <input type="text" name="nome" class="form-control" required>
            </div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success">Salvar</button>
                <a href="/admin/configuracoes" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

@app.route('/admin/plano/novo', methods=['GET', 'POST'])
@admin_required
def admin_novo_plano():
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        descricao = request.form.get('descricao', '')
        if nome:
            if criar_plano_categoria(nome, descricao):
                flash('Plano criado.', 'success')
            else:
                flash('Plano já existe.', 'danger')
        return redirect(url_for('admin_configuracoes'))
    conteudo = '''
    <div class="page-title"><i class="fas fa-folder"></i> Novo Plano</div>
    <div class="card">
        <form method="post">
            <div class="form-group">
                <label>Nome do Plano *</label>
                <input type="text" name="nome" class="form-control" required>
            </div>
            <div class="form-group">
                <label>Descrição</label>
                <textarea name="descricao" class="form-control" rows="2"></textarea>
            </div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success">Salvar</button>
                <a href="/admin/configuracoes" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

@app.route('/admin/wbs/novo', methods=['GET', 'POST'])
@admin_required
def admin_nova_wbs():
    empresas = get_empresas()
    planos = get_planos_categorias()
    if request.method == 'POST':
        nome = request.form['nome'].strip()
        descricao = request.form.get('descricao', '')
        empresa_id = request.form.get('empresa_id')
        plano_id = request.form.get('plano_id')
        critica = 1 if request.form.get('critica') == 'on' else 0
        if nome and empresa_id and plano_id:
            criar_wbs(nome, descricao, empresa_id, plano_id, critica)
            flash('WBS criada.', 'success')
        else:
            flash('Preencha todos os campos obrigatórios.', 'danger')
        return redirect(url_for('admin_configuracoes'))
    select_empresas = ''.join(f'<option value="{e["id"]}">{e["nome"]}</option>' for e in empresas)
    select_planos = ''.join(f'<option value="{p["id"]}">{p["nome"]}</option>' for p in planos)
    conteudo = f'''
    <div class="page-title"><i class="fas fa-sitemap"></i> Nova WBS</div>
    <div class="card">
        <form method="post">
            <div class="form-group">
                <label>Nome da WBS *</label>
                <input type="text" name="nome" class="form-control" required>
            </div>
            <div class="form-group">
                <label>Descrição</label>
                <textarea name="descricao" class="form-control" rows="2"></textarea>
            </div>
            <div class="form-group">
                <label>Empresa *</label>
                <select name="empresa_id" class="form-control" required>
                    <option value="">Selecione</option>
                    {select_empresas}
                </select>
            </div>
            <div class="form-group">
                <label>Plano *</label>
                <select name="plano_id" class="form-control" required>
                    <option value="">Selecione</option>
                    {select_planos}
                </select>
            </div>
            <div class="form-group">
                <label><input type="checkbox" name="critica"> Atividade Crítica</label>
            </div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success">Salvar</button>
                <a href="/admin/configuracoes" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

# ==================== ROTA DE IMPORTAÇÃO ====================
@app.route('/admin/wbs/importar', methods=['GET', 'POST'])
@admin_required
def admin_importar_wbs():
    if request.method == 'POST':
        if 'planilha' not in request.files:
            flash('Nenhum arquivo enviado.', 'danger')
            return redirect(request.url)
        arquivo = request.files['planilha']
        if arquivo.filename == '':
            flash('Nenhum arquivo selecionado.', 'danger')
            return redirect(request.url)
        if not (arquivo.filename.lower().endswith('.xlsx') or 
                arquivo.filename.lower().endswith('.xls') or 
                arquivo.filename.lower().endswith('.csv')):
            flash('Formato não suportado. Use .xlsx, .xls ou .csv.', 'danger')
            return redirect(request.url)
        try:
            filename = secure_filename(arquivo.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            arquivo.save(filepath)
            linhas = importar_wbs_planilha(filepath)
            os.remove(filepath)
            flash(f'Planilha importada com sucesso! {linhas} linhas processadas.', 'success')
        except Exception as e:
            flash(f'Erro ao importar: {str(e)}', 'danger')
        return redirect(url_for('listar_atividades'))
    
    conteudo = '''
    <div class="page-title"><i class="fas fa-file-upload"></i> Importar WBS</div>
    <div class="card">
        <p>Envie uma planilha Excel (.xlsx, .xls) ou CSV com as seguintes colunas:</p>
        <ul>
            <li><strong>empresa</strong> (obrigatório) - Nome da empresa (será criada se não existir)</li>
            <li><strong>plano</strong> (obrigatório) - Nome do plano (será criado se não existir)</li>
            <li><strong>wbs</strong> (obrigatório) - Nome da WBS (será criada se não existir para aquela empresa/plano)</li>
            <li><strong>descricao_wbs</strong> (opcional) - Descrição da WBS</li>
            <li><strong>acao</strong> (obrigatório) - Descrição da atividade</li>
            <li><strong>lb_inicio</strong> (obrigatório) - Data de início da LB (formato YYYY-MM-DD)</li>
            <li><strong>lb_fim</strong> (obrigatório) - Data de fim da LB (formato YYYY-MM-DD)</li>
            <li><strong>inicio_real</strong> (opcional) - Data de início real</li>
            <li><strong>fim_real</strong> (opcional) - Data de fim real</li>
            <li><strong>responsavel</strong> (obrigatório) - Nome do responsável</li>
            <li><strong>autor</strong> (opcional) - Nome do autor (padrão "importação")</li>
            <li><strong>macro</strong> (opcional) - "sim" ou "não" (padrão "não")</li>
            <li><strong>critica</strong> (opcional) - "sim" ou "não" (padrão "não")</li>
        </ul>
        <form method="post" enctype="multipart/form-data">
            <div class="form-group">
                <label>Arquivo *</label>
                <input type="file" name="planilha" accept=".xlsx,.xls,.csv" class="form-control" required>
            </div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success"><i class="fas fa-upload"></i> Importar</button>
                <a href="/atividades" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT_ADMIN, conteudo=conteudo)

# ==================== ROTAS DE API PARA CASCATA ====================
@app.route('/api/planos/<int:empresa_id>')
@login_required
def api_planos(empresa_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT DISTINCT p.id, p.nome 
        FROM planos_categorias p
        JOIN wbs w ON w.plano_id = p.id
        WHERE w.empresa_id = ? AND w.ativo = 1
        ORDER BY p.nome
    ''', (empresa_id,))
    planos = c.fetchall()
    conn.close()
    return jsonify([dict(row) for row in planos])

@app.route('/api/wbs/<int:empresa_id>/<int:plano_id>')
@login_required
def api_wbs(empresa_id, plano_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT id, nome, critica
        FROM wbs 
        WHERE empresa_id = ? AND plano_id = ? AND ativo = 1
        ORDER BY nome
    ''', (empresa_id, plano_id))
    wbs = c.fetchall()
    conn.close()
    return jsonify([dict(row) for row in wbs])

# ==================== ROTA WBS/ATIVIDADES (expansão) ====================
@app.route('/api/wbs/<int:wbs_id>/atividades')
@login_required
def get_wbs_atividades_completo(wbs_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT a.id, a.acao, a.lb_inicio, a.lb_fim, a.inicio_real, a.fim_real, 
               a.responsavel, a.status, a.macro,
               w.nome as wbs_nome, e.nome as empresa_nome, p.nome as plano_nome
        FROM atividades a
        JOIN wbs w ON a.wbs_id = w.id
        JOIN empresas e ON w.empresa_id = e.id
        JOIN planos_categorias p ON w.plano_id = p.id
        WHERE a.wbs_id = ?
        ORDER BY a.lb_fim ASC
    ''', (wbs_id,))
    atividades = c.fetchall()
    conn.close()
    return jsonify([dict(row) for row in atividades])

# ==================== ROTAS DE EVIDÊNCIAS ====================
@app.route('/atividade/evidencias/<int:atividade_id>')
@login_required
def gerenciar_evidencias(atividade_id):
    # Busca a atividade
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT a.*, w.nome as wbs_nome, e.nome as empresa_nome, p.nome as plano_nome
        FROM atividades a
        JOIN wbs w ON a.wbs_id = w.id
        JOIN empresas e ON w.empresa_id = e.id
        JOIN planos_categorias p ON w.plano_id = p.id
        WHERE a.id = ?
    ''', (atividade_id,))
    atividade = c.fetchone()
    conn.close()
    
    if not atividade:
        flash('Atividade não encontrada.', 'danger')
        return redirect(url_for('listar_atividades'))
    
    atividade = dict(atividade)
    evidencias = get_evidencias_by_atividade(atividade_id)
    
    conteudo = f'''
    <div class="page-title"><i class="fas fa-paperclip"></i> Evidências - #{atividade['id']}</div>
    
    <div class="card">
        <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.5rem;">
            <div>
                <strong>Ação:</strong> {atividade['acao']}<br>
                <strong>WBS:</strong> {atividade['wbs_nome']} | <strong>Empresa:</strong> {atividade['empresa_nome']} | <strong>Plano:</strong> {atividade['plano_nome']}
            </div>
            <div>
                <span class="badge badge-{atividade['status'].replace(' ', '_')}">{atividade['status']}</span>
            </div>
        </div>
        <div style="margin-top:0.5rem;">
            <a href="/atividades" class="btn btn-outline btn-sm"><i class="fas fa-arrow-left"></i> Voltar</a>
        </div>
    </div>
    
    <div class="card">
        <h3>Upload de Nova Evidência</h3>
        <form method="post" enctype="multipart/form-data" action="/atividade/evidencia/upload/{atividade_id}">
            <div class="form-row">
                <div class="form-group" style="grid-column: span 2;">
                    <label>Selecione o arquivo (PDF, JPEG, PNG - imagens até 10MB)</label>
                    <input type="file" name="arquivo" class="form-control" accept=".pdf,.jpeg,.jpg,.png" required>
                </div>
            </div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success"><i class="fas fa-upload"></i> Enviar</button>
            </div>
        </form>
    </div>
    
    <div class="card">
        <h3>Evidências Enviadas ({len(evidencias)})</h3>
    '''
    
    if not evidencias:
        conteudo += '''
        <div class="empty-state"><i class="fas fa-file"></i><p>Nenhuma evidência enviada para esta atividade.</p></div>
        '''
    else:
        conteudo += '''
        <div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap:1rem;">
        '''
        for ev in evidencias:
            ext = ev['tipo_arquivo'].lower()
            is_image = ext in ['jpeg', 'jpg', 'png']
            tamanho = tamanho_legivel(ev['tamanho'])
            
            # CORREÇÃO: gera URL usando url_for
            url_arquivo = url_for('uploaded_file', filename=ev['caminho'])
            
            conteudo += f'''
            <div style="border:1px solid #e2e8f0; border-radius:0.5rem; padding:0.5rem; text-align:center; background:#f7fafc;">
            '''
            if is_image:
                conteudo += f'''
                <a href="{url_arquivo}" target="_blank">
                    <img src="{url_arquivo}" alt="{ev['nome_original']}" style="max-width:100%; max-height:150px; object-fit:contain; border-radius:0.25rem;">
                </a>
                '''
            else:
                conteudo += f'''
                <div style="font-size:3rem; color:#4299e1; padding:0.5rem;">
                    <i class="fas fa-file-pdf"></i>
                </div>
                '''
            conteudo += f'''
                <div style="font-size:0.75rem; margin-top:0.25rem; word-break:break-all;">
                    <strong>{ev['nome_original']}</strong><br>
                    <span style="color:#718096;">{tamanho}</span><br>
                    <span style="color:#a0aec0; font-size:0.65rem;">{formatar_data_hora(ev['upload_em'])}</span><br>
                    <span style="color:#a0aec0; font-size:0.65rem;">por {ev['upload_por']}</span>
                </div>
                <div style="margin-top:0.5rem; display:flex; gap:0.3rem; justify-content:center; flex-wrap:wrap;">
                    <a href="{url_arquivo}" target="_blank" class="btn btn-primary btn-sm"><i class="fas fa-eye"></i></a>
                    <a href="{url_arquivo}" download class="btn btn-success btn-sm"><i class="fas fa-download"></i></a>
                    <a href="/atividade/evidencia/deletar/{ev['id']}" class="btn btn-danger btn-sm" onclick="return confirm('Tem certeza que deseja excluir esta evidência?')"><i class="fas fa-trash"></i></a>
                </div>
            </div>
            '''
        conteudo += '''
        </div>
        '''
    
    conteudo += '''
    </div>
    '''
    
    return render_template_string(LAYOUT, conteudo=conteudo)

@app.route('/atividade/evidencia/upload/<int:atividade_id>', methods=['POST'])
@login_required
def upload_evidencia(atividade_id):
    if 'arquivo' not in request.files:
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('gerenciar_evidencias', atividade_id=atividade_id))
    
    arquivo = request.files['arquivo']
    if arquivo.filename == '':
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('gerenciar_evidencias', atividade_id=atividade_id))
    
    try:
        usuario_nome = session.get('nome', 'Usuário')
        salvar_evidencia(atividade_id, arquivo, usuario_nome)
        flash('Evidência enviada com sucesso!', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    except Exception as e:
        logger.error(f"Erro no upload: {e}")
        flash('Erro ao fazer upload do arquivo.', 'danger')
    
    return redirect(url_for('gerenciar_evidencias', atividade_id=atividade_id))

@app.route('/atividade/evidencia/deletar/<int:evidencia_id>')
@login_required
def deletar_evidencia_route(evidencia_id):
    ev = get_evidencia_by_id(evidencia_id)
    if not ev:
        flash('Evidência não encontrada.', 'danger')
        return redirect(url_for('listar_atividades'))
    
    atividade_id = ev['atividade_id']
    if deletar_evidencia(evidencia_id):
        flash('Evidência excluída com sucesso.', 'success')
    else:
        flash('Erro ao excluir evidência.', 'danger')
    
    return redirect(url_for('gerenciar_evidencias', atividade_id=atividade_id))

@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    """Serve arquivos da pasta uploads com o MIME type correto"""
    # Garante que o arquivo está dentro da pasta uploads
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(safe_path):
        abort(404)
    # Determina o MIME type
    mimetype, _ = mimetypes.guess_type(safe_path)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, mimetype=mimetype)

# ==================== ROTAS DE ATIVIDADES COM FILTROS ====================
@app.route('/atividades')
@login_required
def listar_atividades():
    empresas = get_empresas()
    acoes = get_unique_values('acao')
    responsaveis = get_unique_values('responsavel')
    status_options = ['Dentro do Prazo', 'Fora do Prazo', 'Concluído Dentro do Prazo', 'Concluído Fora do Prazo']
    
    empresa_id = request.args.get('empresa_id', type=int)
    plano_id = request.args.get('plano_id', type=int)
    wbs_id = request.args.get('wbs_id', type=int)
    macro = request.args.get('macro', type=int)
    status = request.args.get('status')
    acao = request.args.get('acao', '')
    responsavel = request.args.get('responsavel', '')
    
    tem_filtro = any([
        empresa_id is not None,
        plano_id is not None,
        wbs_id is not None,
        macro is not None,
        status,
        acao,
        responsavel
    ])
    
    atividades = []
    if tem_filtro:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        query = '''SELECT a.*, w.nome as wbs_nome, e.nome as empresa_nome, p.nome as plano_nome 
                   FROM atividades a 
                   JOIN wbs w ON a.wbs_id = w.id 
                   JOIN empresas e ON w.empresa_id = e.id 
                   JOIN planos_categorias p ON w.plano_id = p.id 
                   WHERE 1=1'''
        params = []
        if empresa_id:
            query += ' AND e.id = ?'
            params.append(empresa_id)
        if plano_id:
            query += ' AND p.id = ?'
            params.append(plano_id)
        if wbs_id:
            query += ' AND a.wbs_id = ?'
            params.append(wbs_id)
        if macro is not None:
            query += ' AND a.macro = ?'
            params.append(macro)
        if status:
            query += ' AND a.status = ?'
            params.append(status)
        if acao:
            query += ' AND a.acao = ?'
            params.append(acao)
        if responsavel:
            query += ' AND a.responsavel = ?'
            params.append(responsavel)
        query += ' ORDER BY a.lb_fim ASC'
        c.execute(query, params)
        atividades = c.fetchall()
        conn.close()
    
    planos_disponiveis = []
    if empresa_id:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT DISTINCT p.id, p.nome 
            FROM planos_categorias p
            JOIN wbs w ON w.plano_id = p.id
            WHERE w.empresa_id = ? AND w.ativo = 1
            ORDER BY p.nome
        ''', (empresa_id,))
        planos_disponiveis = c.fetchall()
        conn.close()
    
    wbs_disponiveis = []
    if empresa_id and plano_id:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT id, nome 
            FROM wbs 
            WHERE empresa_id = ? AND plano_id = ? AND ativo = 1
            ORDER BY nome
        ''', (empresa_id, plano_id))
        wbs_disponiveis = c.fetchall()
        conn.close()
    
    is_admin = session.get('perfil') == 'admin'
    
    conteudo = f'''
    <div class="page-title"><i class="fas fa-tasks"></i> Atividades</div>
    <div class="card">
        <form method="get" id="filtro-form" style="margin-bottom:1rem;">
            <div class="form-row">
                <div class="form-group">
                    <label>Empresa</label>
                    <select name="empresa_id" id="empresa-select" class="form-control">
                        <option value="">Todas</option>
                        {''.join(f'<option value="{e["id"]}" {"selected" if empresa_id==e["id"] else ""}>{e["nome"]}</option>' for e in empresas)}
                    </select>
                </div>
                <div class="form-group">
                    <label>Plano</label>
                    <select name="plano_id" id="plano-select" class="form-control">
                        <option value="">Todos</option>
                        {''.join(f'<option value="{p["id"]}" {"selected" if plano_id==p["id"] else ""}>{p["nome"]}</option>' for p in planos_disponiveis)}
                    </select>
                </div>
                <div class="form-group">
                    <label>WBS</label>
                    <select name="wbs_id" id="wbs-select" class="form-control">
                        <option value="">Todas</option>
                        {''.join(f'<option value="{w["id"]}" {"selected" if wbs_id==w["id"] else ""}>{w["nome"]}</option>' for w in wbs_disponiveis)}
                    </select>
                </div>
                <div class="form-group">
                    <label>Macro</label>
                    <select name="macro" class="form-control">
                        <option value="">Todos</option>
                        <option value="1" {"selected" if macro==1 else ""}>Sim</option>
                        <option value="0" {"selected" if macro==0 else ""}>Não</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Status</label>
                    <select name="status" class="form-control">
                        <option value="">Todos</option>
                        {''.join(f'<option value="{s}" {"selected" if status==s else ""}>{s}</option>' for s in status_options)}
                    </select>
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Ação</label>
                    <select name="acao" class="form-control">
                        <option value="">Todas</option>
                        {''.join(f'<option value="{a}" {"selected" if acao==a else ""}>{a}</option>' for a in acoes)}
                    </select>
                </div>
                <div class="form-group">
                    <label>Responsável</label>
                    <select name="responsavel" class="form-control">
                        <option value="">Todos</option>
                        {''.join(f'<option value="{r}" {"selected" if responsavel==r else ""}>{r}</option>' for r in responsaveis)}
                    </select>
                </div>
                <div class="form-group" style="align-self:flex-end;">
                    <button type="submit" class="btn btn-primary" style="width:100%;">Filtrar</button>
                </div>
                <div class="form-group" style="align-self:flex-end;">
                    <a href="/atividades" class="btn btn-outline" style="width:100%;">Limpar</a>
                </div>
            </div>
        </form>
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:1rem;">
            <a href="/atividades/novo" class="btn btn-success"><i class="fas fa-plus"></i> Nova Atividade</a>
            {f'<a href="/admin/wbs/importar" class="btn btn-primary"><i class="fas fa-file-upload"></i> Importar WBS</a>' if is_admin else ''}
        </div>
        <div class="table-responsive">
    '''
    
    if not tem_filtro:
        conteudo += '''
            <div class="empty-state" style="padding:2rem; text-align:center; color:#718096;">
                <i class="fas fa-filter" style="font-size:2rem; color:#cbd5e0; margin-bottom:0.5rem;"></i>
                <p>Selecione um filtro para visualizar as atividades.</p>
            </div>
        '''
    elif not atividades:
        conteudo += '''
            <div class="empty-state" style="padding:2rem; text-align:center; color:#718096;">
                <i class="fas fa-inbox" style="font-size:2rem; color:#cbd5e0; margin-bottom:0.5rem;"></i>
                <p>Nenhuma atividade encontrada com os filtros selecionados.</p>
            </div>
        '''
    else:
        conteudo += '''
            <table>
                <thead>
                    <tr><th>ID</th><th>Ação</th><th>WBS</th><th>Empresa</th><th>Plano</th><th>LB Início</th><th>LB Fim</th><th>Início Real</th><th>Fim Real</th><th>Responsável</th><th>Status</th><th>Macro</th><th>Evidências</th><th>Ações</th></tr>
                </thead>
                <tbody>
        '''
        for a in atividades:
            macro_text = 'Sim' if a['macro'] else 'Não'
            evs = get_evidencias_by_atividade(a['id'])
            qtd_ev = len(evs)
            icone_ev = f'<i class="fas fa-paperclip" style="color:#4299e1;"></i>' if qtd_ev > 0 else '<i class="fas fa-paperclip" style="color:#a0aec0;"></i>'
            conteudo += f'''
            <tr>
                <td>{a['id']}</td>
                <td>{a['acao']}</td>
                <td>{a['wbs_nome']}</td>
                <td>{a['empresa_nome']}</td>
                <td>{a['plano_nome']}</td>
                <td>{formatar_data(a['lb_inicio'])}</td>
                <td>{formatar_data(a['lb_fim'])}</td>
                <td>{formatar_data(a['inicio_real']) if a['inicio_real'] else '-'}</td>
                <td>{formatar_data(a['fim_real']) if a['fim_real'] else '-'}</td>
                <td>{a['responsavel']}</td>
                <td><span class="badge badge-{a['status'].replace(' ', '_')}">{a['status']}</span></td>
                <td>{macro_text}</td>
                <td>
                    <a href="/atividade/evidencias/{a['id']}" class="btn btn-outline btn-sm" title="Gerenciar evidências ({qtd_ev})">
                        {icone_ev} {qtd_ev}
                    </a>
                </td>
                <td>
                    <a href="/atividades/editar/{a['id']}" class="btn btn-warning btn-sm"><i class="fas fa-edit"></i></a>
                    <a href="/atividades/deletar/{a['id']}" class="btn btn-danger btn-sm" onclick="return confirm('Tem certeza?')"><i class="fas fa-trash"></i></a>
                </td>
            </tr>
            '''
        conteudo += '''
                </tbody>
            </table>
        '''
    
    conteudo += '''
        </div>
    </div>
    '''
    
    scripts = '''
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        const empresaSelect = document.getElementById('empresa-select');
        const planoSelect = document.getElementById('plano-select');
        const wbsSelect = document.getElementById('wbs-select');
        const form = document.getElementById('filtro-form');
        function carregarPlanos(empresaId, planoSelecionado) {
            if (!empresaId) { window.location.href = '/atividades'; return; }
            fetch('/api/planos/' + empresaId)
                .then(response => response.json())
                .then(data => {
                    planoSelect.innerHTML = '<option value="">Todos</option>';
                    data.forEach(p => {
                        const opt = document.createElement('option');
                        opt.value = p.id; opt.textContent = p.nome;
                        if (p.id == planoSelecionado) opt.selected = true;
                        planoSelect.appendChild(opt);
                    });
                    const planoId = planoSelect.value;
                    if (planoId && empresaId) {
                        carregarWBS(empresaId, planoId);
                    } else {
                        wbsSelect.innerHTML = '<option value="">Todas</option>';
                    }
                });
        }
        function carregarWBS(empresaId, planoId, wbsSelecionado) {
            if (!empresaId || !planoId) { wbsSelect.innerHTML = '<option value="">Todas</option>'; return; }
            fetch('/api/wbs/' + empresaId + '/' + planoId)
                .then(response => response.json())
                .then(data => {
                    wbsSelect.innerHTML = '<option value="">Todas</option>';
                    data.forEach(w => {
                        const opt = document.createElement('option');
                        opt.value = w.id; opt.textContent = w.nome;
                        if (w.id == wbsSelecionado) opt.selected = true;
                        wbsSelect.appendChild(opt);
                    });
                });
        }
        empresaSelect.addEventListener('change', function() {
            const empresaId = this.value;
            const planoAtual = planoSelect.value;
            if (empresaId) carregarPlanos(empresaId, planoAtual);
            else form.submit();
        });
        planoSelect.addEventListener('change', function() {
            const empresaId = empresaSelect.value;
            const planoId = this.value;
            const wbsAtual = wbsSelect.value;
            if (empresaId && planoId) carregarWBS(empresaId, planoId, wbsAtual);
            else { wbsSelect.innerHTML = '<option value="">Todas</option>'; if (planoId === '') form.submit(); }
        });
        wbsSelect.addEventListener('change', function() { form.submit(); });
        const empresaInicial = empresaSelect.value;
        const planoInicial = planoSelect.value;
        const wbsInicial = wbsSelect.value;
        if (empresaInicial) {
            carregarPlanos(empresaInicial, planoInicial);
            if (planoInicial) carregarWBS(empresaInicial, planoInicial, wbsInicial);
        }
    });
    </script>
    '''
    
    conteudo += scripts
    return render_template_string(LAYOUT, conteudo=conteudo)

# ==================== DEMAIS ROTAS ====================
@app.route('/atividades/novo', methods=['GET', 'POST'])
@master_required
def nova_atividade():
    wbs_list = get_wbs()
    usuario_nome = session.get('nome', '')
    if request.method == 'POST':
        acao = request.form['acao'].strip()
        lb_inicio = request.form['lb_inicio']
        lb_fim = request.form['lb_fim']
        inicio_real = request.form.get('inicio_real') or None
        fim_real = request.form.get('fim_real') or None
        responsavel = request.form['responsavel'].strip()
        autor = usuario_nome
        wbs_id = request.form.get('wbs_id', type=int)
        macro = 1 if request.form.get('macro') == 'on' else 0
        
        if not all([acao, lb_inicio, lb_fim, responsavel, wbs_id]):
            flash('Preencha todos os campos obrigatórios.', 'danger')
        else:
            criar_atividade(acao, lb_inicio, lb_fim, inicio_real, fim_real, responsavel, autor, wbs_id, macro)
            flash('Atividade criada com sucesso!', 'success')
            return redirect(url_for('listar_atividades'))
    
    select_wbs = ''.join(f'<option value="{w["id"]}">{w["nome"]} ({w["empresa_nome"]} / {w["plano_nome"]})</option>' for w in wbs_list)
    conteudo = f'''
    <div class="page-title"><i class="fas fa-plus-circle"></i> Nova Atividade</div>
    <div class="card">
        <form method="post">
            <div class="form-group"><label>Ação *</label><textarea name="acao" class="form-control" rows="2" required></textarea></div>
            <div class="form-row">
                <div class="form-group"><label>LB Início *</label><input type="date" name="lb_inicio" class="form-control" required></div>
                <div class="form-group"><label>LB Fim *</label><input type="date" name="lb_fim" class="form-control" required></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Início Real</label><input type="date" name="inicio_real" class="form-control"></div>
                <div class="form-group"><label>Fim Real</label><input type="date" name="fim_real" class="form-control"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Responsável *</label><input type="text" name="responsavel" class="form-control" required></div>
                <div class="form-group"><label>Autor</label><input type="text" name="autor" class="form-control" value="{usuario_nome}" readonly style="background:#f0f0f0;"></div>
            </div>
            <div class="form-group"><label>WBS *</label><select name="wbs_id" class="form-control" required><option value="">Selecione</option>{select_wbs}</select></div>
            <div class="form-group"><label><input type="checkbox" name="macro"> Atividade Macro (destaque)</label></div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success"><i class="fas fa-save"></i> Salvar</button>
                <a href="/atividades" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT, conteudo=conteudo)

@app.route('/atividades/editar/<int:id>', methods=['GET', 'POST'])
@master_required
def editar_atividade(id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM atividades WHERE id = ?', (id,))
    atividade = c.fetchone()
    conn.close()
    if not atividade:
        flash('Atividade não encontrada.', 'danger')
        return redirect(url_for('listar_atividades'))
    
    wbs_list = get_wbs()
    if request.method == 'POST':
        acao = request.form['acao'].strip()
        lb_inicio = request.form['lb_inicio']
        lb_fim = request.form['lb_fim']
        inicio_real = request.form.get('inicio_real') or None
        fim_real = request.form.get('fim_real') or None
        responsavel = request.form['responsavel'].strip()
        autor = atividade['autor']
        wbs_id = request.form.get('wbs_id', type=int)
        macro = 1 if request.form.get('macro') == 'on' else 0
        
        if not all([acao, lb_inicio, lb_fim, responsavel, wbs_id]):
            flash('Preencha todos os campos obrigatórios.', 'danger')
        else:
            atualizar_atividade(id, acao, lb_inicio, lb_fim, inicio_real, fim_real, responsavel, autor, macro)
            flash('Atividade atualizada.', 'success')
            return redirect(url_for('listar_atividades'))
    
    select_wbs = ''.join(f'<option value="{w["id"]}" {"selected" if w["id"]==atividade["wbs_id"] else ""}>{w["nome"]} ({w["empresa_nome"]} / {w["plano_nome"]})</option>' for w in wbs_list)
    checked = 'checked' if atividade['macro'] else ''
    conteudo = f'''
    <div class="page-title"><i class="fas fa-edit"></i> Editar Atividade</div>
    <div class="card">
        <form method="post">
            <div class="form-group"><label>Ação *</label><textarea name="acao" class="form-control" rows="2" required>{atividade['acao']}</textarea></div>
            <div class="form-row">
                <div class="form-group"><label>LB Início *</label><input type="date" name="lb_inicio" class="form-control" value="{atividade['lb_inicio']}" required></div>
                <div class="form-group"><label>LB Fim *</label><input type="date" name="lb_fim" class="form-control" value="{atividade['lb_fim']}" required></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Início Real</label><input type="date" name="inicio_real" class="form-control" value="{atividade['inicio_real'] or ''}"></div>
                <div class="form-group"><label>Fim Real</label><input type="date" name="fim_real" class="form-control" value="{atividade['fim_real'] or ''}"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Responsável *</label><input type="text" name="responsavel" class="form-control" value="{atividade['responsavel']}" required></div>
                <div class="form-group"><label>Autor</label><input type="text" name="autor" class="form-control" value="{atividade['autor']}" readonly></div>
            </div>
            <div class="form-group"><label>WBS *</label><select name="wbs_id" class="form-control" required><option value="">Selecione</option>{select_wbs}</select></div>
            <div class="form-group"><label><input type="checkbox" name="macro" {checked}> Atividade Macro</label></div>
            <div class="form-actions">
                <button type="submit" class="btn btn-success"><i class="fas fa-save"></i> Salvar</button>
                <a href="/atividades" class="btn btn-outline">Cancelar</a>
            </div>
        </form>
    </div>
    '''
    return render_template_string(LAYOUT, conteudo=conteudo)

@app.route('/atividades/deletar/<int:id>')
@master_required
def deletar_atividade_route(id):
    deletar_atividade(id)
    flash('Atividade deletada.', 'success')
    return redirect(url_for('listar_atividades'))

# ==================== DASHBOARD ====================
@app.route('/')
@login_required
def dashboard():
    empresa_id = request.args.get('empresa_id', type=int)
    plano_id = request.args.get('plano_id', type=int)
    filtro_vencimento = request.args.get('filtro_vencimento')
    
    indicadores = get_indicadores(empresa_id, plano_id)
    
    hoje = datetime.now().date()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = '''SELECT a.*, w.nome as wbs_nome, e.nome as empresa_nome, p.nome as plano_nome 
               FROM atividades a 
               JOIN wbs w ON a.wbs_id = w.id 
               JOIN empresas e ON w.empresa_id = e.id 
               JOIN planos_categorias p ON w.plano_id = p.id 
               WHERE a.status NOT LIKE 'Concluído%' AND a.fim_real IS NULL'''
    params = []
    if empresa_id:
        query += ' AND e.id = ?'
        params.append(empresa_id)
    if plano_id:
        query += ' AND p.id = ?'
        params.append(plano_id)
    if filtro_vencimento == 'hoje':
        query += ' AND date(a.lb_fim) = date(?)'
        params.append(hoje.strftime('%Y-%m-%d'))
    elif filtro_vencimento == '15d':
        query += ' AND date(a.lb_fim) BETWEEN date(?) AND date(?, "+15 days")'
        params.append(hoje.strftime('%Y-%m-%d'))
        params.append(hoje.strftime('%Y-%m-%d'))
    elif filtro_vencimento == 'mes':
        inicio_mes = hoje.replace(day=1)
        fim_mes = (inicio_mes + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        query += ' AND date(a.lb_fim) BETWEEN date(?) AND date(?)'
        params.append(inicio_mes.strftime('%Y-%m-%d'))
        params.append(fim_mes.strftime('%Y-%m-%d'))
    query += ' ORDER BY a.lb_fim ASC'
    c.execute(query, params)
    atividades = c.fetchall()
    conn.close()
    
    empresas = get_empresas()
    planos = get_planos_categorias()
    
    total_geral = sum(i['total'] for i in indicadores)
    concluidos_geral = sum(i['concluidos'] for i in indicadores)
    atrasados_geral = sum(i['atrasados'] for i in indicadores)
    dentro_prazo_geral = sum(i['dentro_prazo'] for i in indicadores)
    pendentes_geral = sum(i['pendentes'] for i in indicadores)
    
    conteudo = f'''
    <div class="page-title"><i class="fas fa-chart-pie" style="color:#4299e1;"></i> Dashboard</div>
    <div style="margin-bottom:1rem;">
        <form method="get" class="form-row">
            <div class="form-group"><label>Empresa</label><select name="empresa_id" class="form-control" onchange="this.form.submit()"><option value="">Todas</option>{''.join(f'<option value="{e["id"]}" {"selected" if empresa_id==e["id"] else ""}>{e["nome"]}</option>' for e in empresas)}</select></div>
            <div class="form-group"><label>Plano</label><select name="plano_id" class="form-control" onchange="this.form.submit()"><option value="">Todos</option>{''.join(f'<option value="{p["id"]}" {"selected" if plano_id==p["id"] else ""}>{p["nome"]}</option>' for p in planos)}</select></div>
            <div class="form-group"><label>Vencimento</label><select name="filtro_vencimento" class="form-control" onchange="this.form.submit()"><option value="">Todos</option><option value="hoje" {"selected" if filtro_vencimento=="hoje" else ""}>Vencendo hoje</option><option value="15d" {"selected" if filtro_vencimento=="15d" else ""}>Vencendo em 15 dias</option><option value="mes" {"selected" if filtro_vencimento=="mes" else ""}>Vencendo no mês</option></select></div>
            <div class="form-group" style="align-self:flex-end;"><a href="/" class="btn btn-outline">Limpar</a></div>
        </form>
    </div>
    <div class="grid-5">
        <div class="stat-card"><div class="stat-icon blue"><i class="fas fa-tasks"></i></div><div class="stat-info"><h3>{total_geral}</h3><p>Total</p></div></div>
        <div class="stat-card"><div class="stat-icon green"><i class="fas fa-check-circle"></i></div><div class="stat-info"><h3>{concluidos_geral}</h3><p>Concluídos</p></div></div>
        <div class="stat-card"><div class="stat-icon red"><i class="fas fa-exclamation-triangle"></i></div><div class="stat-info"><h3>{atrasados_geral}</h3><p>Fora do Prazo</p></div></div>
        <div class="stat-card"><div class="stat-icon yellow"><i class="fas fa-clock"></i></div><div class="stat-info"><h3>{dentro_prazo_geral}</h3><p>Dentro do Prazo</p></div></div>
        <div class="stat-card"><div class="stat-icon gray"><i class="fas fa-hourglass-half"></i></div><div class="stat-info"><h3>{pendentes_geral}</h3><p>Pendentes</p></div></div>
    </div>
    <div class="card">
        <h3><i class="fas fa-chart-bar"></i> Indicadores por Empresa / Plano</h3>
        <div class="table-responsive"><table><thead><tr><th>Empresa</th><th>Plano</th><th>Total</th><th>Concluídos</th><th>Dentro do Prazo</th><th>Fora do Prazo</th><th>Pendentes</th></tr></thead><tbody>
    '''
    if not indicadores:
        conteudo += '<tr><td colspan="7" class="empty-state">Nenhum dado disponível.</td></tr>'
    else:
        for i in indicadores:
            conteudo += f'<tr><td>{i["empresa"]}</td><td>{i["plano"]}</td><td>{i["total"]}</td><td>{i["concluidos"]}</td><td>{i["dentro_prazo"]}</td><td>{i["atrasados"]}</td><td>{i["pendentes"]}</td></tr>'
    conteudo += '''
        </tbody></table></div>
    </div>
    <div class="card">
        <h3><i class="fas fa-list"></i> Atividades em Destaque (Macro)</h3>
        <div class="table-responsive"><table id="macro-table"><thead><tr><th>WBS</th><th>Ação</th><th>Empresa</th><th>Plano</th><th>LB Fim</th><th>Status</th><th>Evid.</th><th>Ações</th></tr></thead><tbody id="macro-tbody">
    '''
    macro_atividades = [a for a in atividades if a['macro'] == 1]
    if not macro_atividades:
        conteudo += '<tr><td colspan="8" class="empty-state">Nenhuma atividade macro.</td></tr>'
    else:
        for a in macro_atividades:
            evs = get_evidencias_by_atividade(a['id'])
            qtd_ev = len(evs)
            icone_ev = f'<i class="fas fa-paperclip" style="color:#4299e1;"></i>' if qtd_ev > 0 else '<i class="fas fa-paperclip" style="color:#a0aec0;"></i>'
            conteudo += f'''
            <tr id="macro-row-{a['id']}">
                <td>{a['wbs_nome']}</td>
                <td>{a['acao']}</td>
                <td>{a['empresa_nome']}</td>
                <td>{a['plano_nome']}</td>
                <td>{formatar_data(a['lb_fim'])}</td>
                <td><span class="badge badge-{a['status'].replace(' ', '_')}">{a['status']}</span></td>
                <td><a href="/atividade/evidencias/{a['id']}" class="btn btn-outline btn-sm" title="{qtd_ev} evidência(s)">{icone_ev} {qtd_ev}</a></td>
                <td><button class="btn btn-outline btn-sm toggle-wbs" data-wbs-id="{a['wbs_id']}" data-macro-id="{a['id']}"><i class="fas fa-chevron-down"></i> Ver WBS</button></td>
            </tr>
            '''
    conteudo += '''
        </tbody></table></div>
    </div>
    '''
    
    scripts = '''
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        function toggleWBS(wbsId, macroId, button) {
            const macroRow = document.getElementById('macro-row-' + macroId);
            if (!macroRow) return;
            
            const detailRowIdPrefix = 'wbs-detail-' + wbsId + '-';
            const existingRows = document.querySelectorAll('tr[id^="' + detailRowIdPrefix + '"]');
            if (existingRows.length > 0) {
                existingRows.forEach(row => row.remove());
                button.innerHTML = '<i class="fas fa-chevron-down"></i> Ver WBS';
                return;
            }
            
            fetch('/api/wbs/' + wbsId + '/atividades')
                .then(response => response.json())
                .then(data => {
                    if (data.length === 0) {
                        alert('Nenhuma atividade encontrada para esta WBS.');
                        return;
                    }
                    
                    let rowsHtml = '';
                    data.forEach((a, index) => {
                        const statusBadge = a.status ? a.status.replace(/ /g, '_') : 'pendente';
                        rowsHtml += `
                            <tr id="wbs-detail-${wbsId}-${index}" style="background-color:#f7fafc;">
                                <td>${a.wbs_nome}</td>
                                <td>${a.acao}</td>
                                <td>${a.empresa_nome}</td>
                                <td>${a.plano_nome}</td>
                                <td>${formatarData(a.lb_fim)}</td>
                                <td><span class="badge badge-${statusBadge}">${a.status}</span></td>
                                <td></td>
                                <td>
                                    <a href="/atividades/editar/${a.id}" class="btn btn-warning btn-sm"><i class="fas fa-edit"></i></a>
                                    <a href="/atividades/deletar/${a.id}" class="btn btn-danger btn-sm" onclick="return confirm('Tem certeza?')"><i class="fas fa-trash"></i></a>
                                </td>
                            </tr>
                        `;
                    });
                    
                    macroRow.insertAdjacentHTML('afterend', rowsHtml);
                    button.innerHTML = '<i class="fas fa-chevron-up"></i> Ocultar WBS';
                })
                .catch(error => {
                    console.error('Erro ao carregar atividades:', error);
                    alert('Erro ao carregar atividades da WBS.');
                });
        }
        
        function formatarData(dataStr) {
            if (!dataStr || dataStr === '-') return '-';
            try {
                const partes = dataStr.split('-');
                if (partes.length === 3) {
                    return partes[2] + '/' + partes[1] + '/' + partes[0];
                }
                return dataStr;
            } catch(e) {
                return dataStr;
            }
        }
        
        document.querySelectorAll('.toggle-wbs').forEach(button => {
            button.addEventListener('click', function(e) {
                e.stopPropagation();
                const wbsId = this.dataset.wbsId;
                const macroId = this.dataset.macroId;
                toggleWBS(wbsId, macroId, this);
            });
        });
    });
    </script>
    '''
    
    return render_template_string(LAYOUT + scripts, conteudo=conteudo)

# ==================== NOTIFICAÇÕES ====================
@app.route('/notificacoes')
@login_required
def notificacoes():
    conteudo = '''
    <div class="page-title"><i class="fas fa-bell"></i> Notificações</div>
    <div class="card"><div class="empty-state"><i class="fas fa-inbox"></i><p>Nenhuma notificação.</p></div></div>
    '''
    return render_template_string(LAYOUT, conteudo=conteudo)

# ==================== TEMPLATES ====================
LOGIN_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Planos de Ação</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #2d3748, #1a202c); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-card { background: white; border-radius: 1rem; padding: 2.5rem; width: 100%; max-width: 400px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
        .login-card h1 { font-size: 1.8rem; color: #2d3748; margin-bottom: 0.5rem; text-align: center; }
        .login-card .subtitle { text-align: center; color: #718096; margin-bottom: 2rem; font-size: 0.9rem; }
        .login-card .logo { text-align: center; font-size: 3rem; color: #4299e1; margin-bottom: 1rem; }
        .form-group { margin-bottom: 1.2rem; }
        .form-group label { display: block; font-weight: 600; color: #4a5568; margin-bottom: 0.3rem; font-size: 0.9rem; }
        .form-control { width: 100%; padding: 0.7rem 1rem; border: 1px solid #e2e8f0; border-radius: 0.5rem; font-size: 1rem; transition: border-color 0.2s; }
        .form-control:focus { outline: none; border-color: #4299e1; box-shadow: 0 0 0 3px rgba(66,153,225,0.15); }
        .btn { width: 100%; padding: 0.7rem; background: #4299e1; color: white; border: none; border-radius: 0.5rem; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        .btn:hover { background: #3182ce; }
        .alert { padding: 0.75rem; border-radius: 0.5rem; margin-bottom: 1rem; font-size: 0.9rem; }
        .alert-danger { background: #fed7d7; color: #9b2c2c; border: 1px solid #fc8181; }
        .alert-success { background: #c6f6d5; color: #22543d; border: 1px solid #48bb78; }
        .alert-warning { background: #fefcbf; color: #744210; border: 1px solid #ecc94b; }
        .mt-2 { margin-top: 1rem; }
        .text-center { text-align: center; }
        .text-muted { color: #718096; font-size: 0.85rem; }
        @media (max-width: 480px) { .login-card { padding: 1.5rem; margin: 1rem; } }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo"><i class="fas fa-tasks"></i></div>
        <h1>Gestão de Planos</h1>
        <p class="subtitle">Acesse sua conta</p>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }}">{{ message }}</div>{% endfor %}{% endif %}
        {% endwith %}
        <form method="post">
            <div class="form-group"><label><i class="fas fa-envelope"></i> E-mail</label><input type="email" name="email" class="form-control" placeholder="seu@email.com" required></div>
            <div class="form-group"><label><i class="fas fa-lock"></i> Senha</label><input type="password" name="senha" class="form-control" placeholder="••••••••" required></div>
            <button type="submit" class="btn"><i class="fas fa-sign-in-alt"></i> Entrar</button>
        </form>
    </div>
</body>
</html>
'''

ALTERAR_SENHA_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alterar Senha - Planos de Ação</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #2d3748, #1a202c); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .card { background: white; border-radius: 1rem; padding: 2.5rem; width: 100%; max-width: 400px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
        .card h1 { font-size: 1.8rem; color: #2d3748; margin-bottom: 0.5rem; text-align: center; }
        .card .logo { text-align: center; font-size: 3rem; color: #4299e1; margin-bottom: 1rem; }
        .form-group { margin-bottom: 1.2rem; }
        .form-group label { display: block; font-weight: 600; color: #4a5568; margin-bottom: 0.3rem; }
        .form-control { width: 100%; padding: 0.7rem 1rem; border: 1px solid #e2e8f0; border-radius: 0.5rem; font-size: 1rem; }
        .form-control:focus { outline: none; border-color: #4299e1; box-shadow: 0 0 0 3px rgba(66,153,225,0.15); }
        .btn { width: 100%; padding: 0.7rem; background: #4299e1; color: white; border: none; border-radius: 0.5rem; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        .btn:hover { background: #3182ce; }
        .btn-secondary { background: #718096; margin-top: 0.5rem; }
        .btn-secondary:hover { background: #4a5568; }
        .alert { padding: 0.75rem; border-radius: 0.5rem; margin-bottom: 1rem; font-size: 0.9rem; }
        .alert-danger { background: #fed7d7; color: #9b2c2c; border: 1px solid #fc8181; }
        .alert-success { background: #c6f6d5; color: #22543d; border: 1px solid #48bb78; }
        .alert-warning { background: #fefcbf; color: #744210; border: 1px solid #ecc94b; }
        .text-center { text-align: center; }
        .text-muted { color: #718096; font-size: 0.85rem; margin-top: 1rem; }
        @media (max-width: 480px) { .card { padding: 1.5rem; margin: 1rem; } }
    </style>
</head>
<body>
    <div class="card">
        <div class="logo"><i class="fas fa-key"></i></div>
        <h1>Alterar Senha</h1>
        <p class="text-muted">Este é seu primeiro acesso. Defina uma nova senha.</p>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }}">{{ message }}</div>{% endfor %}{% endif %}
        {% endwith %}
        <form method="post">
            <div class="form-group"><label>Senha Atual</label><input type="password" name="senha_atual" class="form-control" required></div>
            <div class="form-group"><label>Nova Senha</label><input type="password" name="nova_senha" class="form-control" required></div>
            <div class="form-group"><label>Confirmar Nova Senha</label><input type="password" name="confirmar_senha" class="form-control" required></div>
            <button type="submit" class="btn"><i class="fas fa-save"></i> Alterar Senha</button>
        </form>
    </div>
</body>
</html>
'''

LAYOUT = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gestão de Planos de Ação</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #f0f4f8; color: #1a202c; line-height: 1.6; }
        .navbar { background: #2d3748; padding: 0 1rem; height: 64px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; }
        .navbar-brand { color: white; font-weight: 700; font-size: 1.1rem; }
        .navbar-brand i { color: #63b3ed; margin-right: 0.5rem; }
        .navbar-links { display: flex; gap: 1rem; list-style: none; flex-wrap: wrap; }
        .navbar-links a { color: #cbd5e0; text-decoration: none; display: flex; align-items: center; gap: 0.2rem; font-size: 0.9rem; padding: 0.3rem 0.5rem; border-radius: 0.375rem; transition: background 0.2s; }
        .navbar-links a:hover { background: rgba(255,255,255,0.1); color: white; }
        .navbar-links a.active { background: rgba(99,179,237,0.2); color: white; }
        .user-info { display: flex; align-items: center; gap: 0.8rem; color: #a0aec0; font-size:0.9rem; flex-wrap: wrap; }
        .user-info .perfil-badge { padding: 0.15rem 0.6rem; border-radius: 999px; font-size: 0.65rem; font-weight: 600; }
        .perfil-badge.admin { background: #805ad5; color: white; }
        .perfil-badge.master { background: #48bb78; color: white; }
        .perfil-badge.usuario { background: #a0aec0; color: white; }
        .user-info .logout-btn { color: #fc8181; text-decoration: none; font-size:0.85rem; display: flex; align-items: center; gap:0.3rem; }
        .user-info .logout-btn:hover { color: #e53e3e; }
        .container { max-width: 1280px; margin: 0 auto; padding: 1rem; }
        .page-title { font-size: 1.5rem; font-weight: 700; margin-bottom: 1rem; color: #2d3748; }
        .card { background: white; border-radius: 0.75rem; padding: 1.25rem; margin-bottom: 1.25rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border: 1px solid #e2e8f0; }
        .grid-4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 1rem; }
        .grid-5 { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px,1fr)); gap: 1rem; }
        .stat-card { background: white; border-radius: 0.75rem; padding: 1rem; border: 1px solid #e2e8f0; display: flex; align-items: center; gap: 0.8rem; }
        .stat-icon { width: 42px; height: 42px; border-radius: 0.75rem; display: flex; align-items: center; justify-content: center; font-size: 1.2rem; color: white; flex-shrink: 0; }
        .stat-icon.blue { background: #4299e1; }
        .stat-icon.green { background: #48bb78; }
        .stat-icon.red { background: #fc8181; }
        .stat-icon.yellow { background: #ecc94b; }
        .stat-icon.gray { background: #a0aec0; }
        .stat-info h3 { font-size: 1.25rem; }
        .stat-info p { color: #718096; font-size: 0.75rem; }
        .badge { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; font-size: 0.7rem; font-weight: 600; }
        .badge-admin { background: #805ad5; color: white; }
        .badge-master { background: #48bb78; color: white; }
        .badge-usuario { background: #a0aec0; color: white; }
        .badge-Dentro_do_Prazo { background: #c6f6d5; color: #22543d; }
        .badge-Fora_do_Prazo { background: #fed7d7; color: #9b2c2c; }
        .badge-Concluído_Dentro_do_Prazo { background: #9ae6b4; color: #22543d; }
        .badge-Concluído_Fora_do_Prazo { background: #feb2b2; color: #9b2c2c; }
        .btn { display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.4rem 0.8rem; border-radius: 0.375rem; font-weight: 600; border: none; cursor: pointer; text-decoration: none; font-size: 0.85rem; }
        .btn-primary { background: #4299e1; color: white; }
        .btn-success { background: #48bb78; color: white; }
        .btn-warning { background: #ecc94b; color: #1a202c; }
        .btn-danger { background: #fc8181; color: white; }
        .btn-secondary { background: #718096; color: white; }
        .btn-outline { background: transparent; color: #4a5568; border: 1px solid #e2e8f0; }
        .btn-sm { padding: 0.2rem 0.5rem; font-size: 0.75rem; }
        .form-group { margin-bottom: 0.8rem; }
        .form-group label { display: block; font-weight: 600; color: #4a5568; margin-bottom: 0.2rem; font-size: 0.9rem; }
        .form-control { width: 100%; padding: 0.5rem 0.7rem; border: 1px solid #e2e8f0; border-radius: 0.375rem; font-size: 0.9rem; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
        .form-actions { display: flex; gap: 0.5rem; margin-top: 1rem; flex-wrap: wrap; }
        .flex { display: flex; }
        .flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; }
        .table-responsive { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th { background: #f7fafc; padding: 0.5rem 0.75rem; text-align: left; border-bottom: 2px solid #e2e8f0; }
        td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #edf2f7; }
        .empty-state { text-align: center; padding: 2rem; color: #718096; }
        .empty-state i { font-size: 2rem; color: #cbd5e0; margin-bottom: 0.5rem; }
        .alert { padding: 0.5rem 0.75rem; border-radius: 0.375rem; margin-bottom: 0.8rem; font-size: 0.9rem; }
        .alert-success { background: #c6f6d5; color: #22543d; border: 1px solid #48bb78; }
        .alert-danger { background: #fed7d7; color: #9b2c2c; border: 1px solid #fc8181; }
        .alert-warning { background: #fefcbf; color: #744210; border: 1px solid #ecc94b; }
        .alert-info { background: #bee3f8; color: #2a69ac; border: 1px solid #63b3ed; }
        @media (max-width: 768px) {
            .navbar { padding: 0.5rem 1rem; height: auto; }
            .navbar-brand { font-size: 1rem; }
            .navbar-links { gap: 0.3rem; }
            .navbar-links a { font-size: 0.8rem; padding: 0.2rem 0.4rem; }
            .container { padding: 0.75rem; }
            .page-title { font-size: 1.25rem; }
            .card { padding: 1rem; }
            .form-row { grid-template-columns: 1fr; }
            .btn { font-size: 0.8rem; padding: 0.3rem 0.6rem; }
            table { font-size: 0.75rem; }
            th, td { padding: 0.3rem 0.5rem; }
            .user-info { font-size: 0.8rem; }
            .grid-5 { grid-template-columns: 1fr 1fr; }
        }
    </style>
</head>
<body>
    <nav class="navbar">
        <a class="navbar-brand" href="/"><i class="fas fa-tasks"></i> Planos</a>
        <ul class="navbar-links">
            <li><a href="/" class="{{ 'active' if request.path == '/' else '' }}"><i class="fas fa-chart-pie"></i> Dashboard</a></li>
            <li><a href="/atividades" class="{{ 'active' if request.path == '/atividades' else '' }}"><i class="fas fa-tasks"></i> Atividades</a></li>
            <li><a href="/notificacoes" class="{{ 'active' if request.path == '/notificacoes' else '' }}"><i class="fas fa-bell"></i> Notificações</a></li>
            {% if session.perfil == 'admin' %}
            <li><a href="/admin/usuarios" class="{{ 'active' if request.path == '/admin/usuarios' else '' }}"><i class="fas fa-users"></i> Usuários</a></li>
            <li><a href="/admin/configuracoes" class="{{ 'active' if request.path == '/admin/configuracoes' else '' }}"><i class="fas fa-cog"></i> Config</a></li>
            {% endif %}
        </ul>
        <div class="user-info">
            <i class="fas fa-user"></i> {{ session.nome }}
            <span class="perfil-badge {{ session.perfil }}">{{ session.perfil }}</span>
            <a href="/logout" class="logout-btn"><i class="fas fa-sign-out-alt"></i> Sair</a>
        </div>
    </nav>
    <main class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }}">{{ message }}</div>{% endfor %}{% endif %}
        {% endwith %}
        {{ conteudo|safe }}
    </main>
    {% block scripts %}{% endblock %}
</body>
</html>
'''

LAYOUT_ADMIN = LAYOUT

# ==================== INICIALIZAÇÃO ====================
if __name__ == '__main__':
    print("🚀 Servidor iniciado em http://localhost:5000")
    print("📧 Admin: admin@empresa.com / admin123")
    print("📋 Datas formatadas em DD/MM/AAAA")
    print("📋 Expansão de WBS com colunas alinhadas")
    print("📋 Upload de evidências: PDF, JPEG, PNG (imagens até 10MB)")
    print("📋 Responsivo para mobile")
    app.run(debug=True, host='0.0.0.0', port=8080)
