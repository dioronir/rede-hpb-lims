from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import hashlib
import io
import json
import os
import re
import smtplib

import numpy as np
import pandas as pd
import pypdf
from sqlalchemy import create_engine, text
import streamlit as st

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Rede HPB - Plataforma Operacional, Qualidade e Governança",
    layout="wide",
)


# --- 2. CONEXÃO SEGURA COM O BANCO DE DADOS (SUPABASE POSTGRESQL OU SQLITE AUTOMÁTICO) ---
@st.cache_resource
def get_engine():
    try:
        db_url = str(st.secrets["database"]["url"]).strip()
        db_url = re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+pg8000://", db_url)
        eng = create_engine(
            db_url, pool_pre_ping=True, pool_size=5, max_overflow=10
        )
        # Testa a conexão real aqui dentro: se o Supabase estiver pausado, cai no except abaixo
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception:
        return create_engine(
            "sqlite:///rede_hpb_local.db",
            connect_args={"check_same_thread": False},
        )


engine = get_engine()
IS_SQLITE = "sqlite" in str(engine.url)

def run_sql(query: str, params: dict = None):
    with engine.begin() as conn:
        return conn.execute(text(query), params or {})


def fetch_one(query: str, params: dict = None):
    with engine.connect() as conn:
        return conn.execute(text(query), params or {}).fetchone()


def fetch_all(query: str, params: dict = None):
    with engine.connect() as conn:
        return conn.execute(text(query), params or {}).fetchall()


def fetch_df(query: str, params: dict = None) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params or {})


# --- 3. CONSTANTES INSTITUCIONAIS E PERMISSÕES DE ACESSO DA REDE HPB ---
CENTROS_HOSPITAIS_HPB = {
    "HC": "Hospital das Clínicas de Pernambuco (HC-UFPE / Ebserh)",
    "HUOC": "Hospital Universitário Oswaldo Cruz (HUOC-UPE)",
    "HCP": "Hospital de Câncer de Pernambuco (HCP)",
    "HSE": "Hospital dos Servidores do Estado (HSE)",
}

SETORES_COLETA_HC_HPB = [
    "Bloco Cirúrgico (Agendamento Cirúrgico)",
    "Laboratório e Patologia",
    "Ambulatório de Oncologia",
    "Unidade de Diagnóstico por Imagem",
    "Ultrassom Endoscópica",
    "Enfermarias",
    "Ambulatório da Lista de Espera Cirúrgica (LEC)",
]

MODALIDADES_COLETA_MATERIAL = [
    "Sangue + Tumor (Cirurgia / Biópsia)",
    "Apenas Sangue (Quimioterapia / Diagnóstico por Imagem / Ambulatório / Enfermarias / LEC)",
    "Apenas Tumor / Bloco de Parafina (Laboratório e Patologia - Análise Histológica)",
]

JANELAS_TEMPORAIS_CIRURGIA = [
    "No Ato Cirúrgico (Dia 0 - Ressecção / Biópsia Cirúrgica)",
    "Pré-Operatório / Diagnóstico por Imagem / LEC (Antes da Cirurgia)",
    "Durante Quimioterapia Neoadjuvante (Antes da Cirurgia)",
    "Durante Quimioterapia Adjuvante / Paliativa (Após a Cirurgia)",
    "Seguimento Ambulatorial / Recidiva (Após a Cirurgia)",
    "Resgate de Bloco de Parafina no Setor de Patologia (Retrospectivo / Histologia)",
]

SITIOS_HPB = {
    "P": "Pâncreas (P)",
    "H": "Hepático (H)",
    "B": "Biliar (B)",
    "PAPILA": "Papila Duodenal / Periampular (PAPILA)",
    "Ctl": "Controle (Ctl)",
}

BIOTIPOS_REDE_HPB = {
    1: ("Soro", "SORO (1)"),
    2: ("Plasma", "PLASMA (2)"),
    3: ("PBMC", "PBMC (3)"),
    4: ("PMN", "PMN (4)"),
    5: ("Biópsia", "BIOPSIA / PARAFINA (5)"),
    6: ("Sangue", "SANGUE TOTAL (6)"),
    7: ("Plasma PBMC", "PLASMA PBMC (7)"),
}

MODELOS_TCLE_HOMOLOGADOS = {
    "TCLE_HC_REDEHPB": {
        "titulo": "TCLE HC-UFPE — Rede Internacional de Pesquisa para Câncer HPB (Sangue + Tumor + Biobanco -80°C)",
        "pesquisador": "Prof. Dr. Moacyr Jesus Barreto de Melo Rêgo",
        "permite_sangue": True,
        "permite_tumor": True,
        "exige_guarda_biobanco": True,
        "exige_duas_testemunhas": True,
        "exige_cpf": False,
    },
    "TCLE_HUOC_SO_SANGUE": {
        "titulo": "TCLE HUOC/UPE — Biomarcadores IL-34, IL-36 e IL-37 no Câncer de Pâncreas (EXCLUSIVO SANGUE 26mL)",
        "pesquisador": "Jéssica Winely Batista da Silva / Profa. Michelly Pereira / Prof. Moacyr Rêgo",
        "permite_sangue": True,
        "permite_tumor": False,
        "exige_guarda_biobanco": False,
        "exige_duas_testemunhas": False,
        "exige_cpf": False,
    },
    "TCLE_HUOC_SANGUE_E_TECIDO": {
        "titulo": "TCLE UFPE/HUOC — Biomarcadores microRNA 200, 122, 34a e TP53 (Sangue 5mL + Tecido Tumoral)",
        "pesquisador": "Dr. Adriano Carneiro da Costa / Débora Ferreira Lopes",
        "permite_sangue": True,
        "permite_tumor": True,
        "exige_guarda_biobanco": False,
        "exige_duas_testemunhas": True,
        "exige_cpf": True,
    },
    "TCLE_HCP_REDEHPB": {
        "titulo": "TCLE HCP — Rede HPB Institucional (Sangue + Tecido Tumoral)",
        "pesquisador": "Prof. Dr. Moacyr Jesus Barreto de Melo Rêgo",
        "permite_sangue": True,
        "permite_tumor": True,
        "exige_guarda_biobanco": True,
        "exige_duas_testemunhas": True,
        "exige_cpf": False,
    },
}

# Subatividade exclusiva de Obtenção de Organoides restrita inicialmente a Lauriano e Julia Braga
PROCESSADORES_AUTORIZADOS_ORGANOIDES = ["jose.lauriano", "julia.pereira", "moacyr", "valecia.costa"]

PRAZO_LIMITE_ESCALA_HORAS = 12.0
LIMITE_TROCAS_MENSAL_PADRAO = 3
BONUS_TROCA_MOACYR = 1


def usuario_pode_escalar() -> bool:
    """Apenas Prof. Moacyr e Valécia possuem poder de eleger/escalar Coletores e Processadores."""
    u = st.session_state.get("username", "").lower()
    r = st.session_state.get("role", "")
    return u in ["moacyr", "valecia.costa"] or r in ["Pesquisador_Responsavel", "Coordenacao_Escala"]


def usuario_eh_moacyr() -> bool:
    u = st.session_state.get("username", "").lower()
    r = st.session_state.get("role", "")
    return u == "moacyr" or r == "Pesquisador_Responsavel"


def usuario_pode_ver_pii() -> bool:
    """
    Nomes e Prontuários são expostos APENAS para Controle de Qualidade,
    Bioinformata e Professores/Pesquisadores Responsáveis (Moacyr e Valécia).
    Coletores e Processadores visualizam apenas ID, Hospital, Setor e Status.
    """
    r = st.session_state.get("role", "")
    u = st.session_state.get("username", "").lower()
    return r in [
        "Pesquisador_Responsavel",
        "Coordenacao_Escala",
        "Controle_Qualidade",
        "Bioinformata",
    ] or u in ["moacyr", "valecia.costa"]


def usuario_autorizado_organoides() -> bool:
    """Subatividade de obtenção de organoides restrita a José Lauriano e Julia Braga (e supervisão PI)."""
    u = st.session_state.get("username", "").lower()
    return u in PROCESSADORES_AUTORIZADOS_ORGANOIDES


# --- 4. INICIALIZAÇÃO E AUTO-MIGRAÇÃO DO BANCO DE DADOS (V5) ---
if "db_hpb_v5_initialized" not in st.session_state:
    pk_serial = (
        "INTEGER PRIMARY KEY AUTOINCREMENT" if IS_SQLITE else "SERIAL PRIMARY KEY"
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hpb_usuarios (
                username TEXT PRIMARY KEY,
                nome_completo TEXT,
                password_hash TEXT,
                email TEXT,
                telefone TEXT,
                role TEXT,
                ativo INTEGER DEFAULT 1,
                ultimo_login_em TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_escala_setores_semana (
                id_escala_setor {pk_serial},
                username TEXT,
                nome_operador TEXT,
                data_dia TEXT,
                dia_semana_nome TEXT,
                hospital TEXT,
                setor_hc TEXT,
                atribuido_por TEXT,
                criado_em TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_agenda_cirurgica (
                id_agenda {pk_serial},
                id_hpb_gerado TEXT,
                data_cirurgia TEXT,
                dia_semana_mapa TEXT,
                sala_horario TEXT,
                hospital TEXT,
                setor_coleta TEXT DEFAULT 'Bloco Cirúrgico (Agendamento Cirúrgico)',
                prontuario TEXT,
                nome_paciente TEXT,
                diagnostico_mapa TEXT,
                cirurgia_proposta TEXT,
                cirurgiao TEXT,
                sitio_sugerido TEXT,
                pdf_mapa_nome TEXT,
                drive_mapa_url TEXT,
                coletor_escalado TEXT,
                data_hora_candidatura_coletor TEXT,
                indicado_por_moacyr TEXT,
                processador_escalado TEXT,
                data_hora_candidatura_proc TEXT,
                processador_indicado_por TEXT,
                ultima_atualizacao_coletor_em TEXT,
                status_escala TEXT DEFAULT 'AGUARDANDO_COLETOR',
                alerta_12h_moacyr_enviado INTEGER DEFAULT 0,
                criado_por TEXT,
                criado_em TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hpb_coletas_ifera (
                id_hpb TEXT PRIMARY KEY,
                id_anterior TEXT,
                id_agenda INTEGER,
                eh_paciente_antigo INTEGER DEFAULT 0,
                id_paciente_antigo_ref TEXT,
                hospital TEXT,
                setor_hospital_coleta TEXT,
                modalidade_coleta_material TEXT,
                janela_temporal_cirurgia TEXT,
                tempo_relativo_cirurgia_desc TEXT,
                bloco_parafina_obtido INTEGER DEFAULT 0,
                codigo_bloco_parafina TEXT,
                qtd_blocos_parafina INTEGER DEFAULT 0,
                sitio_hpb TEXT,
                tipo_intervencao_rp10 TEXT,
                ciclo_quimio_dq TEXT,
                codigo_intervencao_final TEXT,
                momento_tcle_longitudinal TEXT,
                nome_paciente TEXT,
                prontuario TEXT,
                cpf_paciente TEXT,
                hipotese_diagnostica TEXT,
                laudo_histopatologico TEXT,
                modelo_tcle_aplicado TEXT,
                tcle_aceite_guarda_biorrepositorio TEXT,
                tcle_assinatura_participante_ok INTEGER DEFAULT 1,
                tcle_duas_testemunhas_ok INTEGER DEFAULT 1,
                tcle_nome_testemunha_1 TEXT,
                tcle_nome_testemunha_2 TEXT,
                sangue_coletado INTEGER DEFAULT 1,
                tubo_heparina_1 INTEGER DEFAULT 0,
                tubo_heparina_2 INTEGER DEFAULT 0,
                tubo_sst_soro_1 INTEGER DEFAULT 0,
                tubo_sst_soro_2 INTEGER DEFAULT 0,
                tubo_edta_1 INTEGER DEFAULT 0,
                vol_sangue_total_coletado_ml REAL DEFAULT 0,
                tumor_coletado INTEGER DEFAULT 1,
                tumor_rnalater_epp1 INTEGER DEFAULT 0,
                tumor_rnalater_epp2 INTEGER DEFAULT 0,
                tumor_sol_transp_epp1 INTEGER DEFAULT 0,
                tumor_sol_transp_epp2 INTEGER DEFAULT 0,
                tumor_falcon_15ml INTEGER DEFAULT 0,
                tumor_falcon_50ml INTEGER DEFAULT 0,
                peso_tumor_coletado_mg REAL DEFAULT 0,
                total_aliquotas_coletadas INTEGER DEFAULT 0,
                local_coleta_detalhado TEXT,
                data_coleta TEXT,
                hora_coleta TEXT,
                data_hora_indexacao_app TEXT,
                motivo_nao_coleta TEXT,
                volume_diferente_protocolo INTEGER DEFAULT 0,
                motivo_volume_diferente TEXT,
                caracteristica_sangue TEXT,
                responsavel_coleta TEXT,
                processador_designado TEXT,
                eleito_processador_por TEXT,
                armazenado_devidamente INTEGER DEFAULT 1,
                temp_armaz_max REAL,
                temp_armaz_min REAL,
                temp_armaz_atual REAL,
                motivo_nao_armazenado TEXT,
                responsavel_armaz_pos_coleta TEXT,
                local_saida TEXT,
                data_saida TEXT,
                hora_saida TEXT,
                temp_saida_max REAL,
                temp_saida_min REAL,
                temp_saida_atual REAL,
                local_chegada TEXT,
                hora_chegada TEXT,
                temp_chegada_max REAL,
                temp_chegada_min REAL,
                temp_chegada_atual REAL,
                responsavel_transporte TEXT,
                local_recebimento TEXT,
                data_recebimento TEXT,
                hora_recebimento TEXT,
                responsavel_recebimento TEXT,
                responsavel_centrifugacao TEXT,
                hora_ini_centrifugacao TEXT,
                hora_fim_centrifugacao TEXT,
                centrifugada_tempo_ok INTEGER DEFAULT 1,
                motivo_centrifugacao TEXT,
                amostra_suficiente_dissoc_pcr TEXT,
                motivo_apenas_um_processamento TEXT,
                data_ini_dissociacao TEXT,
                hora_ini_dissociacao TEXT,
                dissociada_parametros_ok INTEGER DEFAULT 1,
                motivo_dissociacao TEXT,
                data_ini_extracao_rna TEXT,
                hora_ini_extracao_rna TEXT,
                extracao_parametros_ok INTEGER DEFAULT 1,
                motivo_extracao_rna TEXT,
                responsavel_rnalater_dissoc TEXT,
                data_cultivo_garrafas TEXT,
                hora_cultivo_garrafas TEXT,
                num_garrafas_placas TEXT,
                aliquotas_cultivo_ok INTEGER DEFAULT 1,
                motivo_aliquotas_cultivo TEXT,
                data_realizacao_extracao TEXT,
                hora_realizacao_extracao TEXT,
                aliquotas_extracao_ok INTEGER DEFAULT 1,
                motivo_aliquotas_extracao TEXT,
                responsavel_dissoc_extracao_final TEXT,
                data_aliquotagem TEXT,
                hora_aliquotagem TEXT,
                responsavel_processamento_aliquota TEXT,
                aliquotas_planejadas_ok INTEGER DEFAULT 1,
                motivo_aliquotas_faltantes TEXT,
                data_congelamento TEXT,
                hora_congelamento TEXT,
                responsavel_armazenamento_final TEXT,
                observacoes_rp10 TEXT,
                doc_tcle_ok INTEGER DEFAULT 0,
                nome_arq_tcle TEXT,
                drive_url_tcle TEXT,
                doc_fera_ok INTEGER DEFAULT 0,
                nome_arq_fera TEXT,
                drive_url_fera TEXT,
                doc_ficha_clinica_ok INTEGER DEFAULT 0,
                nome_arq_ficha_clinica TEXT,
                drive_url_ficha_clinica TEXT,
                planilha_biobanco_ok INTEGER DEFAULT 0,
                planilha_dados_clinicos_ok INTEGER DEFAULT 0,
                legenda_doc TEXT,
                dados_pdf_confirmados_ok INTEGER DEFAULT 0,
                confirmado_pdf_por TEXT,
                data_hora_confirmacao_pdf TEXT,
                resumo_extracao_pdf_json TEXT,
                liberado_para_processador INTEGER DEFAULT 0,
                data_hora_liberacao TEXT,
                coletor_confirmou_travou INTEGER DEFAULT 0,
                data_hora_confirmacao_coletor TEXT,
                processador_confirmou_travou INTEGER DEFAULT 0,
                data_hora_confirmacao_proc TEXT,
                cq_homologado INTEGER DEFAULT 0,
                cq_homologado_por TEXT,
                data_hora_homologacao_cq TEXT,
                score_agilidade_coletor REAL DEFAULT 100.0,
                score_agilidade_proc REAL DEFAULT 100.0,
                score_viabilidade_amostra REAL DEFAULT 100.0,
                ultima_atualizacao_fluxo_em TEXT,
                acao_organoide_realizada TEXT,
                responsavel_organoide TEXT,
                status_operacional TEXT DEFAULT 'EM_COLETA'
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hpb_ficha_clinica (
                id_hpb TEXT PRIMARY KEY,
                centro_hospital TEXT,
                prontuario TEXT,
                nome_paciente TEXT,
                data_entrada TEXT,
                sexo TEXT,
                naturalidade TEXT,
                data_nascimento TEXT,
                residencia_cidade_uf TEXT,
                data_biopsia TEXT,
                escolaridade TEXT,
                escolaridade_grau TEXT,
                cor_raca TEXT,
                situacao_trabalho TEXT,
                tabagismo TEXT,
                tabagismo_tipo_dose TEXT,
                etilismo TEXT,
                etilismo_tipo_dose TEXT,
                imc REAL,
                outros_tumores TEXT,
                outros_tumores_qual TEXT,
                diabetes TEXT,
                hipertensao TEXT,
                doencas_autoimunes TEXT,
                infeccoes_cronicas TEXT,
                uso_medicacoes TEXT,
                uso_medicacoes_quais TEXT,
                passado_cirurgico TEXT,
                ca19_9 TEXT,
                ca125 TEXT,
                descricao_lesao TEXT,
                ctnm TEXT,
                cestadio TEXT,
                localizacao_anatomica TEXT,
                localizacao_outros TEXT,
                tratamento_inicial TEXT,
                data_tratamento_inicial TEXT,
                tratamento_adjuvante_pos TEXT,
                tipo_cirurgia TEXT,
                tnm_estadiamento_patologico TEXT,
                margens_livres TEXT,
                linfadenectomia_num_linfonodos TEXT,
                tratamento_adjuvante_realizado TEXT,
                tto_adjuvante_esquema TEXT,
                recidiva TEXT,
                resposta_terapeutica TEXT,
                histopatologia TEXT,
                num_cirurgia TEXT,
                outras_informacoes TEXT,
                atualizado_por TEXT,
                atualizado_em TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_biobanco_aliquotas (
                id_aliquota {pk_serial},
                id_anterior TEXT,
                id_hpb TEXT,
                sitio_hpb TEXT,
                intervencao TEXT,
                data_coleta TEXT,
                cod_biotipo INTEGER,
                amostra_nome TEXT,
                codigo_etiqueta TEXT,
                etiqueta_dupla_linha TEXT,
                amostra_no_150 TEXT,
                densidade_celular TEXT,
                n_aliquotas_entrada INTEGER DEFAULT 0,
                vol_aliquota_ml REAL DEFAULT 0,
                n_aliquotas_saida INTEGER DEFAULT 0,
                n_aliquotas_devolucao INTEGER DEFAULT 0,
                vol_saida_ml REAL DEFAULT 0,
                n_aliquotas_biobanco INTEGER DEFAULT 0,
                vol_total_biobanco_ml REAL DEFAULT 0,
                caixa TEXT,
                rack TEXT,
                coluna TEXT,
                linha INTEGER,
                processador_username TEXT,
                data_hora_processamento TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hpb_extracao_rna_pagina13 (
                id_hpb TEXT PRIMARY KEY,
                peso_mg REAL,
                trizol_ul REAL,
                concentracao_ng REAL,
                razao_260_280 REAL,
                volume_final TEXT,
                responsavel TEXT,
                data_registro TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_cultivos_organoides_cafs (
                id_reg {pk_serial},
                id_hpb TEXT,
                tipo_linhagem TEXT,
                protocolo_obtencao_organoide TEXT,
                n_ampolas INTEGER,
                densidade_celular TEXT,
                passagem TEXT,
                data_congelamento TEXT,
                posicao_150c TEXT,
                rack TEXT,
                responsavel TEXT,
                obs TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_saidas_biobanco (
                id_saida {pk_serial},
                id_anterior TEXT,
                id_hpb TEXT,
                amostra TEXT,
                n_aliquotas_retiradas INTEGER,
                volume_utilizado TEXT,
                solicitante TEXT,
                prof_responsavel TEXT DEFAULT 'Moacyr',
                data_retirada TEXT,
                responsavel_retirada TEXT,
                n_aliquotas_devolvidas INTEGER DEFAULT 0,
                data_devolucao TEXT,
                responsavel_devolucao TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_trocas_escala (
                id_troca {pk_serial},
                mes_referencia TEXT,
                tipo_escala TEXT,
                id_referencia TEXT,
                solicitante_username TEXT,
                solicitante_nome TEXT,
                substituto_username TEXT,
                substituto_nome TEXT,
                autorizado_extra_moacyr INTEGER DEFAULT 0,
                autorizado_por TEXT,
                motivo_troca TEXT,
                status_troca TEXT DEFAULT 'EFETIVADA',
                data_hora_troca TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_erros_revisao_cq (
                id_erro {pk_serial},
                id_hpb TEXT,
                etapa_origem TEXT,
                operador_responsavel TEXT,
                tipo_erro TEXT,
                campo_alterado TEXT,
                valor_anterior TEXT,
                valor_corrigido TEXT,
                observacao_cq TEXT,
                avaliador_cq TEXT,
                data_hora TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_chat_operadores (
                id_msg {pk_serial},
                remetente_username TEXT,
                remetente_nome TEXT,
                destinatario_username TEXT,
                mensagem TEXT,
                data_hora TEXT
            )
        """))

        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS hpb_audit_logs (
                id {pk_serial},
                username TEXT,
                role TEXT,
                acao TEXT,
                id_hpb_ref TEXT,
                detalhe TEXT,
                data_hora TEXT
            )
        """))

    # Auto-migração de colunas novas em bancos existentes
    migracoes_gerais = [
        ("hpb_usuarios", "ultimo_login_em", "TEXT"),
        ("hpb_agenda_cirurgica", "id_hpb_gerado", "TEXT"),
        ("hpb_agenda_cirurgica", "data_hora_candidatura_coletor", "TEXT"),
        ("hpb_agenda_cirurgica", "data_hora_candidatura_proc", "TEXT"),
        ("hpb_agenda_cirurgica", "ultima_atualizacao_coletor_em", "TEXT"),
        ("hpb_coletas_ifera", "coletor_confirmou_travou", "INTEGER DEFAULT 0"),
        ("hpb_coletas_ifera", "data_hora_confirmacao_coletor", "TEXT"),
        ("hpb_coletas_ifera", "processador_confirmou_travou", "INTEGER DEFAULT 0"),
        ("hpb_coletas_ifera", "data_hora_confirmacao_proc", "TEXT"),
        ("hpb_coletas_ifera", "cq_homologado", "INTEGER DEFAULT 0"),
        ("hpb_coletas_ifera", "cq_homologado_por", "TEXT"),
        ("hpb_coletas_ifera", "data_hora_homologacao_cq", "TEXT"),
        ("hpb_coletas_ifera", "score_agilidade_coletor", "REAL DEFAULT 100.0"),
        ("hpb_coletas_ifera", "score_agilidade_proc", "REAL DEFAULT 100.0"),
        ("hpb_coletas_ifera", "score_viabilidade_amostra", "REAL DEFAULT 100.0"),
        ("hpb_coletas_ifera", "ultima_atualizacao_fluxo_em", "TEXT"),
        ("hpb_coletas_ifera", "acao_organoide_realizada", "TEXT"),
        ("hpb_coletas_ifera", "responsavel_organoide", "TEXT"),
        ("hpb_cultivos_organoides_cafs", "protocolo_obtencao_organoide", "TEXT"),
    ]
    for tab_m, col_m, tipo_m in migracoes_gerais:
        try:
            run_sql(f"ALTER TABLE {tab_m} ADD COLUMN {col_m} {tipo_m}")
        except Exception:
            pass

    def _seed_membro(u, nome, s, em, tel, r):
        row_ex = fetch_one("SELECT username FROM hpb_usuarios WHERE username = :u", {"u": u})
        agora_seed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not row_ex:
            salt = os.urandom(16).hex()
            dk = hashlib.pbkdf2_hmac("sha256", s.encode("utf-8"), bytes.fromhex(salt), 100000)
            run_sql(
                """INSERT INTO hpb_usuarios (username, nome_completo, password_hash, email, telefone, role, ativo, ultimo_login_em)
                   VALUES (:u, :n, :p, :e, :t, :r, 1, :ul)""",
                {"u": u, "n": nome, "p": f"{salt}${dk.hex()}", "e": em, "t": tel, "r": r, "ul": agora_seed},
            )
        else:
            run_sql(
                "UPDATE hpb_usuarios SET email = :e, role = :r, nome_completo = :n WHERE username = :u",
                {"e": em, "r": r, "n": nome, "u": u},
            )

    _seed_membro("moacyr", "Prof. Dr. Moacyr Jesus Barreto de Melo Rêgo", "Moacyr@HPB2026", "moacyr.rego@ufpe.br", "(81) 99794-3799", "Pesquisador_Responsavel")
    _seed_membro("valecia.costa", "Valécia de Cássia Mendonça da Costa", "Rede@HPB2026", "valecia.costa@ufpe.br", "(81) 99609-9850", "Coordenacao_Escala")
    _seed_membro("qualidade.hpb", "Controle de Qualidade e Auditoria Rede HPB", "Quali@HPB2026", "qualidade.hpb@ufpe.br", "(81) 2126-8588", "Controle_Qualidade")
    _seed_membro("bioinfo.hpb", "Bioinformata Rede HPB / NUPIT", "Bio@HPB2026", "bioinformatica.hpb@ufpe.br", "(81) 2126-8588", "Bioinformata")
    _seed_membro("agenda_hpb", "Coordenação de Agenda Cirúrgica Rede HPB", "Agenda@HPB2026", "agenda.hpb@ufpe.br", "(81) 2126-8588", "Agenda_HPB")
    _seed_membro("claudio.cordeiro", "Cláudio Leandro Cordeiro da Silva Junior", "Rede@HPB2026", "claudio.cjunior@ufpe.br", "(81) 99882-7297", "Bioinformata")
    _seed_membro("jessica.winely", "Jéssica Winely Batista da Silva", "Rede@HPB2026", "jessica.winely@ufpe.br", "(81) 98166-0726", "Controle_Qualidade")
    _seed_membro("barbara.andrade", "Bárbara Fernanda Pessoa de Andrade", "Rede@HPB2026", "barbara.andrade@ufpe.br", "(81) 98956-9873", "Coletor")
    _seed_membro("cecilia.silva", "Cecília Evellyn da Silva", "Rede@HPB2026", "cecilia.evellyn@ufpe.br", "(81) 99801-0673", "Coletor")
    _seed_membro("matheus.conceicao", "Matheus Ítalo da Conceição", "Rede@HPB2026", "matheus.italo@ufpe.br", "(81) 98583-2402", "Coletor")
    _seed_membro("vanessa.brito", "Vanessa de Albuquerque Brito", "Rede@HPB2026", "vanessa.brito@ufpe.br", "(81) 99806-8764", "Coletor")
    # Processadores habilitados para Organoides (Lauriano e Julia Braga) e demais processadores
    _seed_membro("jose.lauriano", "José Lauriano da Silva Filho", "Proc@HPB2026", "jose.lauriano@ufpe.br", "(81) 98242-1105", "Processador")
    _seed_membro("julia.pereira", "Julia Braga Pereira", "Proc@HPB2026", "julia.braga@ufpe.br", "(81) 98624-2979", "Processador")
    _seed_membro("carol.caf", "Carolina Processamento Rede HPB", "Proc@HPB2026", "carolina.hpb@ufpe.br", "(81) 2126-8588", "Processador")
    _seed_membro("luisa.sandrelly", "Luísa Sandrelly", "Proc@HPB2026", "luisa.sandrelly@ufpe.br", "(81) 2126-8588", "Processador")
    _seed_membro("processador_lab", "Equipe de Processamento LINAT/UFPE", "Proc@HPB2026", "processador.hpb@ufpe.br", "(81) 2126-8588", "Processador")

    st.session_state.db_hpb_v5_initialized = True


# --- 5. FUNÇÕES DE SEGURANÇA, AUDITORIA E DISPARO DE E-MAIL ---
def check_hashes(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, key = stored_hash.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000)
    return dk.hex() == key


def make_hashes(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000)
    return f"{salt}${dk.hex()}"


def log_audit(acao: str, id_hpb_ref: str, detalhe: str):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    run_sql(
        """INSERT INTO hpb_audit_logs (username, role, acao, id_hpb_ref, detalhe, data_hora)
           VALUES (:u, :r, :a, :id, :d, :dh)""",
        {
            "u": st.session_state.get("username", "sistema"),
            "r": st.session_state.get("role", "Sistema"),
            "a": acao,
            "id": id_hpb_ref,
            "d": detalhe,
            "dh": agora,
        },
    )


def inicializar_trava_emails_sistema():
    """Cria a trava persistente de e-mails DESATIVADA (0) por padrão."""
    run_sql("""
        CREATE TABLE IF NOT EXISTS hpb_config_sistema (
            chave TEXT PRIMARY KEY,
            valor_int INTEGER DEFAULT 0,
            atualizado_por TEXT,
            atualizado_em TEXT
        )
    """)
    row = fetch_one(
        "SELECT valor_int FROM hpb_config_sistema WHERE chave = 'disparo_email_moacyr_valecia_ativo'"
    )
    if not row:
        run_sql(
            """INSERT INTO hpb_config_sistema (chave, valor_int, atualizado_por, atualizado_em)
               VALUES ('disparo_email_moacyr_valecia_ativo', 0, 'sistema_padrao_travado', :dh)""",
            {"dh": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        )


def obter_status_trava_email() -> tuple[bool, str, str]:
    """Retorna (esta_ativo, atualizado_por, atualizado_em)."""
    inicializar_trava_emails_sistema()
    row = fetch_one(
        """SELECT valor_int, atualizado_por, atualizado_em
           FROM hpb_config_sistema
           WHERE chave = 'disparo_email_moacyr_valecia_ativo'"""
    )
    if not row:
        return False, "sistema", "-"
    return bool(int(row[0] or 0) == 1), (row[1] or "-"), (row[2] or "-")


def alterar_trava_email_moacyr_valecia(ativar: bool) -> bool:
    """Altera a trava de e-mail SOMENTE se o usuário logado for Moacyr ou Valécia."""
    if not (usuario_eh_moacyr() or usuario_pode_escalar()):
        return False
    agora_cfg = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_sql(
        """UPDATE hpb_config_sistema
           SET valor_int = :v, atualizado_por = :u, atualizado_em = :dh
           WHERE chave = 'disparo_email_moacyr_valecia_ativo'""",
        {
            "v": 1 if ativar else 0,
            "u": st.session_state.get("username", "moacyr"),
            "dh": agora_cfg,
        },
    )
    log_audit(
        "Alteração Trava de E-mail",
        "CONFIG-EMAIL",
        f"Disparo automático de e-mails {'ATIVADO' if ativar else 'DESATIVADO (TRAVADO)'} por {st.session_state.get('nome_usuario', '')}.",
    )
    return True


def disparar_email_hpb(destinatarios: list[str], assunto: str, corpo: str) -> bool:
    """
    TRAVA DE SEGURANÇA: Só executa conexões SMTP e envia e-mails se a chave
    'disparo_email_moacyr_valecia_ativo' tiver sido explicitamente ativada
    dentro do painel de Moacyr e Valécia.
    """
    email_liberado, _, _ = obter_status_trava_email()
    if not email_liberado:
        return False

    try:
        remetente = st.secrets["email"]["remetente"]
        senha = st.secrets["email"]["senha_app"]
        dest_validos = [d.strip() for d in destinatarios if d and "@" in d]
        if not dest_validos:
            return False
        msg = MIMEMultipart()
        msg["From"] = remetente
        msg["To"] = ", ".join(dest_validos)
        msg["Subject"] = assunto
        msg.attach(MIMEText(corpo, "plain", "utf-8"))
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(remetente, senha)
            server.sendmail(remetente, dest_validos, msg.as_string())
        return True
    except Exception:
        return False


def salvar_pdf_pasta_drive_sincronizada(id_hpb: str, tipo_doc: str, uploaded_file) -> str:
    pasta_base = os.path.join("Drive_Rede_HPB", id_hpb.replace(" ", ""))
    os.makedirs(pasta_base, exist_ok=True)
    nome_seguro = f"{id_hpb}_{tipo_doc}_{uploaded_file.name}"
    caminho_completo = os.path.join(pasta_base, nome_seguro)
    with open(caminho_completo, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return caminho_completo


# --- 6. MOTOR DE TERMÔMETROS DUPLOS, CÓDIGO DE CORES E LINHA DO TEMPO DE INATIVIDADE ---
def calcular_diferenca_tempo_legivel(timestamp_str: str) -> tuple[str, float]:
    """Retorna ('Xh Ymin atrás', horas_float) a partir de um timestamp YYYY-MM-DD HH:MM:SS."""
    if not timestamp_str:
        return "Sem registro", 999.0
    for fmt in ["%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M"]:
        try:
            dt_ref = datetime.strptime(str(timestamp_str).strip(), fmt)
            segundos = max(0.0, (datetime.now() - dt_ref).total_seconds())
            horas = segundos / 3600.0
            h_int = int(segundos // 3600)
            m_int = int((segundos % 3600) // 60)
            return f"{h_int}h {m_int}min atrás", round(horas, 2)
        except Exception:
            continue
    return "Sem registro", 999.0


def obter_linha_tempo_coletor_para_processador(id_hpb: str, id_agenda: int = None) -> dict:
    """
    Calcula todas as métricas que o Processador vê ao clicar no paciente:
    - Quem é o Coletador
    - Quando ele se candidatou para fazer a coleta (dia e hora)
    - Há quanto tempo o Coletador está sem logar na plataforma
    - Há quanto tempo o Coletador está sem atualizar o fluxo daquele paciente
    - Status de TCLE, Ficha Clínica, FERA e confirmação final do Coletador
    """
    row_c = fetch_one(
        """SELECT responsavel_coleta, doc_tcle_ok, doc_ficha_clinica_ok, doc_fera_ok,
                  coletor_confirmou_travou, data_hora_confirmacao_coletor,
                  ultima_atualizacao_fluxo_em, data_coleta, hora_coleta, id_agenda
           FROM hpb_coletas_ifera WHERE id_hpb = :id""",
        {"id": id_hpb},
    )
    ag_id = id_agenda or (row_c[9] if row_c else None)
    row_ag = (
        fetch_one(
            """SELECT coletor_escalado, data_hora_candidatura_coletor, ultima_atualizacao_coletor_em, criado_em
               FROM hpb_agenda_cirurgica WHERE id_agenda = :id""",
            {"id": ag_id},
        )
        if ag_id
        else None
    )

    coletor_user = (
        (row_c[0] if row_c and row_c[0] else None)
        or (row_ag[0] if row_ag and row_ag[0] else None)
        or "Não escalado"
    )
    dt_candidatura = (
        (row_ag[1] if row_ag and row_ag[1] else None)
        or (row_ag[3] if row_ag else None)
        or "Candidatura direta em setor"
    )
    ult_atu_fluxo = (
        (row_c[6] if row_c and row_c[6] else None)
        or (row_ag[2] if row_ag and row_ag[2] else None)
        or ""
    )

    ult_login_str = ""
    nome_coletor = coletor_user
    if coletor_user and coletor_user != "Não escalado":
        u_row = fetch_one(
            "SELECT nome_completo, ultimo_login_em FROM hpb_usuarios WHERE username = :u",
            {"u": coletor_user},
        )
        if u_row:
            nome_coletor = f"{u_row[0]} ({coletor_user})"
            ult_login_str = u_row[1] or ""

    tempo_sem_logar_txt, horas_sem_logar = calcular_diferenca_tempo_legivel(ult_login_str)
    tempo_sem_atualizar_txt, horas_sem_atualizar = calcular_diferenca_tempo_legivel(ult_atu_fluxo)

    tcle_ok = bool(row_c[1]) if row_c else False
    ficha_ok = bool(row_c[2]) if row_c else False
    fera_ok = bool(row_c[3]) if row_c else False
    coletor_travou_ok = bool(row_c[4]) if row_c else False

    tudo_ok_coletor = bool(tcle_ok and ficha_ok and fera_ok and coletor_travou_ok)

    return {
        "coletor_username": coletor_user,
        "coletor_exibicao": nome_coletor,
        "quando_se_candidatou": dt_candidatura,
        "ultimo_login_em": ult_login_str or "-",
        "tempo_sem_logar": tempo_sem_logar_txt,
        "horas_sem_logar": horas_sem_logar,
        "ultima_atualizacao_fluxo": ult_atu_fluxo or "-",
        "tempo_sem_atualizar_paciente": tempo_sem_atualizar_txt,
        "horas_sem_atualizar": horas_sem_atualizar,
        "tcle_anexado": tcle_ok,
        "ficha_clinica_anexada": ficha_ok,
        "ifera_preenchido": fera_ok,
        "coletor_revisou_e_confirmou": coletor_travou_ok,
        "data_hora_confirmacao_coletor": (row_c[5] if row_c and row_c[5] else "-"),
        "liberado_para_iniciar_processamento": tudo_ok_coletor,
    }


def obter_status_cromatico_e_termometros_todos_pacientes() -> list[dict]:
    """
    Constrói a lista unificada de pacientes para o painel inicial de Moacyr e Valécia
    seguindo a regra exata de 4 cores:
    - VERMELHO (#ef4444): Somente menção do paciente na agenda, sem coletor escalado.
    - AMARELO (#eab308): Coletor escalado / em fase do Coletador (não liberado ao processamento).
    - AZUL (#3b82f6): Coleta confirmada, em fase de Processamento.
    - VERDE (#10b981): Finalizado (Coleta + Processamento concluídos).
    """
    agendas = fetch_all("""
        SELECT id_agenda, id_hpb_gerado, hospital, setor_coleta, data_cirurgia,
               dia_semana_mapa, sala_horario, nome_paciente, prontuario,
               diagnostico_mapa, sitio_sugerido, coletor_escalado, processador_escalado
        FROM hpb_agenda_cirurgica ORDER BY id_agenda DESC
    """)
    coletas = fetch_all("""
        SELECT id_hpb, id_agenda, hospital, setor_hospital_coleta, nome_paciente,
               prontuario, doc_tcle_ok, doc_ficha_clinica_ok, doc_fera_ok,
               coletor_confirmou_travou, processador_confirmou_travou, status_operacional,
               responsavel_coleta, processador_designado, responsavel_recebimento,
               responsavel_organoide, cq_homologado
        FROM hpb_coletas_ifera ORDER BY data_hora_indexacao_app DESC
    """)

    mapa_coletas_por_id = {r[0]: r for r in coletas}
    mapa_coletas_por_ag = {r[1]: r for r in coletas if r[1]}
    ids_ja_listados = set()
    lista_saida = []

    # 1. Processa pacientes vindos da agenda cirúrgica/setorial
    for ag in agendas:
        (
            id_ag, id_h_ger, hosp, setor, dt_cir, dia_m, sala_h,
            nm_p, pr_p, diag_m, sit_s, col_esc, proc_esc
        ) = ag
        c_row = mapa_coletas_por_ag.get(id_ag) or (mapa_coletas_por_id.get(id_h_ger) if id_h_ger else None)
        id_exibicao = (c_row[0] if c_row else None) or id_h_ger or f"AG-{id_ag:02d}HPB"
        if c_row:
            ids_ja_listados.add(c_row[0])

        if not col_esc and not c_row:
            cor_hex = "#ef4444"
            cor_nome = "VERMELHO (Sem Coletor Escalado)"
            term_col = 0
            term_proc = 0
        elif c_row:
            tcle_ok, fc_ok, fera_ok = int(c_row[6] or 0), int(c_row[7] or 0), int(c_row[8] or 0)
            col_conf = int(c_row[9] or 0)
            proc_conf = int(c_row[10] or 0)
            st_op = c_row[11] or ""
            term_col = int((tcle_ok * 25) + (fc_ok * 25) + (fera_ok * 25) + (col_conf * 25))
            if st_op == "PROCESSADO_E_ARMAZENADO" and proc_conf == 1:
                term_proc = 100
                cor_hex = "#10b981"
                cor_nome = "VERDE (Finalizado)"
            elif st_op == "PROCESSADO_E_ARMAZENADO":
                term_proc = 75
                cor_hex = "#3b82f6"
                cor_nome = "AZUL (Em Processamento - Aguardando Confirmação)"
            elif col_conf == 1:
                term_proc = 25
                cor_hex = "#3b82f6"
                cor_nome = "AZUL (Em Processamento)"
            else:
                term_proc = 0
                cor_hex = "#eab308"
                cor_nome = "AMARELO (Em Fase do Coletador)"
        else:
            term_col = 15
            term_proc = 0
            cor_hex = "#eab308"
            cor_nome = "AMARELO (Coletor Escalado - Aguardando Coleta)"

        lista_saida.append({
            "id_display": id_exibicao,
            "id_hpb": c_row[0] if c_row else None,
            "id_agenda": id_ag,
            "hospital": hosp or "HC",
            "setor": (c_row[3] if c_row and c_row[3] else setor) or "Bloco Cirúrgico",
            "nome_paciente": (c_row[4] if c_row and c_row[4] else nm_p) or "-",
            "prontuario": (c_row[5] if c_row and c_row[5] else pr_p) or "-",
            "coletor": (c_row[12] if c_row and c_row[12] else col_esc) or "Sem Coletor",
            "processador": (c_row[14] if c_row and c_row[14] else (c_row[13] if c_row and c_row[13] else proc_esc)) or "Sem Processador",
            "termometro_coleta": term_col,
            "termometro_processamento": term_proc,
            "cor_hex": cor_hex,
            "cor_status": cor_nome,
        })

    # 2. Adiciona coletas avulsas de setores que não vieram de id_agenda
    for c_row in coletas:
        if c_row[0] in ids_ja_listados:
            continue
        tcle_ok, fc_ok, fera_ok = int(c_row[6] or 0), int(c_row[7] or 0), int(c_row[8] or 0)
        col_conf = int(c_row[9] or 0)
        proc_conf = int(c_row[10] or 0)
        st_op = c_row[11] or ""
        term_col = int((tcle_ok * 25) + (fc_ok * 25) + (fera_ok * 25) + (col_conf * 25))
        if st_op == "PROCESSADO_E_ARMAZENADO" and proc_conf == 1:
            term_proc, cor_hex, cor_nome = 100, "#10b981", "VERDE (Finalizado)"
        elif col_conf == 1 or st_op == "PROCESSADO_E_ARMAZENADO":
            term_proc, cor_hex, cor_nome = 50, "#3b82f6", "AZUL (Em Processamento)"
        else:
            term_proc, cor_hex, cor_nome = 0, "#eab308", "AMARELO (Em Fase do Coletador)"

        lista_saida.append({
            "id_display": c_row[0],
            "id_hpb": c_row[0],
            "id_agenda": c_row[1],
            "hospital": c_row[2] or "HC",
            "setor": c_row[3] or "Bloco Cirúrgico",
            "nome_paciente": c_row[4] or "-",
            "prontuario": c_row[5] or "-",
            "coletor": c_row[12] or "-",
            "processador": c_row[14] or c_row[13] or "Sem Processador",
            "termometro_coleta": term_col,
            "termometro_processamento": term_proc,
            "cor_hex": cor_hex,
            "cor_status": cor_nome,
        })

    return lista_saida


def calcular_rankings_coletores_e_processadores(dias_janela: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Gera o Ranking de Coletores e Processadores para a Semana (7 dias),
    Mês (30 dias) e Semestre (180 dias), combinando:
    - Quantidade de Coletas / Processamentos concluídos no período
    - Score Médio de Agilidade (calculado a partir do tempo coleta/indexação/bancada)
    - Número de Erros apontados pelo Controle de Qualidade (penalidade por erro)
    """
    data_corte = (date.today() - timedelta(days=dias_janela)).strftime("%Y-%m-%d")
    df_col = fetch_df(
        """SELECT responsavel_coleta, data_coleta, score_agilidade_coletor
           FROM hpb_coletas_ifera
           WHERE data_coleta >= :dc AND responsavel_coleta IS NOT NULL AND responsavel_coleta != ''""",
        {"dc": data_corte},
    )
    df_proc = fetch_df(
        """SELECT responsavel_recebimento, data_recebimento, score_agilidade_proc
           FROM hpb_coletas_ifera
           WHERE data_recebimento >= :dc AND responsavel_recebimento IS NOT NULL AND responsavel_recebimento != ''""",
        {"dc": data_corte},
    )
    df_erros = fetch_df(
        "SELECT operador_responsavel, etapa_origem FROM hpb_erros_revisao_cq"
    )

    usuarios = fetch_all("SELECT username, nome_completo, role FROM hpb_usuarios WHERE ativo = 1")
    rank_c, rank_p = [], []

    for u, nome, role in usuarios:
        if role in ["Coletor", "Coordenacao_Escala"]:
            qtd_c = len(df_col[df_col["responsavel_coleta"] == u]) if not df_col.empty else 0
            agil_c = (
                round(float(df_col[df_col["responsavel_coleta"] == u]["score_agilidade_coletor"].fillna(100).mean()), 1)
                if qtd_c > 0 else 0.0
            )
            erros_c = (
                len(df_erros[(df_erros["operador_responsavel"] == u) & (df_erros["etapa_origem"] == "Coleta")])
                if not df_erros.empty else 0
            )
            pontos_c = round((qtd_c * 100.0) + (agil_c * 0.5) - (erros_c * 25.0), 1)
            rank_c.append({
                "Coletador": nome,
                "Username": u,
                "Coletas no Período": qtd_c,
                "Score Agilidade (%)": agil_c,
                "Erros Apontados pelo CQ": erros_c,
                "Pontuação Geral": max(0.0, pontos_c),
            })

        if role in ["Processador", "Coordenacao_Escala"]:
            qtd_p = len(df_proc[df_proc["responsavel_recebimento"] == u]) if not df_proc.empty else 0
            agil_p = (
                round(float(df_proc[df_proc["responsavel_recebimento"] == u]["score_agilidade_proc"].fillna(100).mean()), 1)
                if qtd_p > 0 else 0.0
            )
            erros_p = (
                len(df_erros[(df_erros["operador_responsavel"] == u) & (df_erros["etapa_origem"] == "Processamento")])
                if not df_erros.empty else 0
            )
            pontos_p = round((qtd_p * 100.0) + (agil_p * 0.5) - (erros_p * 25.0), 1)
            rank_p.append({
                "Processador": nome,
                "Username": u,
                "Processamentos no Período": qtd_p,
                "Score Agilidade (%)": agil_p,
                "Erros Apontados pelo CQ": erros_p,
                "Pontuação Geral": max(0.0, pontos_p),
            })

    df_rc = pd.DataFrame(rank_c).sort_values(by=["Pontuação Geral", "Coletas no Período"], ascending=False).reset_index(drop=True)
    df_rp = pd.DataFrame(rank_p).sort_values(by=["Pontuação Geral", "Processamentos no Período"], ascending=False).reset_index(drop=True)
    return df_rc, df_rp
# FIM DA PARTE 1 DE 4
# --- 7. VISUALIZADOR DE GRADE 10x10 DAS CRIOCAIXAS E CALENDÁRIO SEMANAL ---
def listar_caixas_criogenicas_disponiveis() -> list[str]:
    caixas_db = [
        r[0] for r in fetch_all(
            "SELECT DISTINCT caixa FROM hpb_biobanco_aliquotas WHERE caixa IS NOT NULL AND caixa != '0' AND caixa != ''"
        )
    ]
    caixas_padrao = [
        "P1.01", "P2.01", "P3.01", "P4.01", "P5.01", "P6.01",
        "H1.01", "H2.01", "H3.01", "H4.01", "H5.01", "H6.01",
        "B1.01", "B2.01", "B3.01", "B4.01", "B5.01", "B6.01",
    ]
    return sorted(list(set(caixas_padrao + caixas_db)))


def renderizar_visualizador_grade_10x10_criocaixa(caixa_selecionada: str):
    letras = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    matriz = {lin: {col: None for col in letras} for lin in range(1, 11)}

    registros = fetch_all(
        """SELECT id_hpb, amostra_nome, codigo_etiqueta, data_coleta, linha, coluna, n_aliquotas_biobanco
           FROM hpb_biobanco_aliquotas
           WHERE UPPER(caixa) = :cx AND COALESCE(n_aliquotas_biobanco, 0) > 0""",
        {"cx": caixa_selecionada.strip().upper()},
    )

    ocupados_count = 0
    for id_h, am_nome, cod_etq, dt_c, lin_db, col_str, n_aliq in registros:
        try:
            lin_idx = int(float(lin_db or 1))
        except Exception:
            lin_idx = 1
        lin_idx = max(1, min(10, lin_idx))
        cols_extraidas = [
            c.strip().upper()[0]
            for c in re.split(r"[,;\s]+", str(col_str or ""))
            if c.strip() and c.strip().upper()[0] in letras
        ]
        if not cols_extraidas:
            cols_extraidas = ["A"]
        for c_let in cols_extraidas:
            matriz[lin_idx][c_let] = {
                "id_hpb": id_h,
                "etiqueta": cod_etq,
                "biotipo": am_nome,
                "data": dt_c,
            }
            ocupados_count += 1

    livres_count = 100 - min(100, ocupados_count)
    st.markdown(
        f"**Criocaixa `{caixa_selecionada}`** — Ocupação Espacial: **{ocupados_count}/100 poços** (`{livres_count}` livres)"
    )

    html_table = [
        "<div style='overflow-x:auto;'>",
        "<table style='width:100%; border-collapse:collapse; font-family:monospace; font-size:11px; text-align:center;'>",
        "<thead><tr style='background:#0f172a; color:#f8fafc;'>",
        "<th style='padding:6px; border:1px solid #334155;'>Linha \\ Col</th>",
    ]
    for c_let in letras:
        html_table.append(f"<th style='padding:6px; border:1px solid #334155;'>{c_let}</th>")
    html_table.append("</tr></thead><tbody>")

    for lin in range(1, 11):
        html_table.append(
            f"<tr><td style='background:#0f172a; color:#f8fafc; font-weight:700; padding:6px; border:1px solid #334155;'>{lin}</td>"
        )
        for c_let in letras:
            cel = matriz[lin][c_let]
            if cel:
                html_table.append(
                    "<td style='background:#065f46; color:#ecfdf5; padding:5px; "
                    "border:1px solid #10b981; font-weight:700;' "
                    f"title='ID: {cel['id_hpb']} | {cel['biotipo']} | Data: {cel['data']}'>"
                    f"{cel['etiqueta']}<br><span style='font-size:9px; color:#a7f3d0;'>{cel['data']}</span></td>"
                )
            else:
                html_table.append(
                    "<td style='background:#1e293b; color:#64748b; padding:5px; "
                    "border:1px solid #334155;'>"
                    f"Livre<br><span style='font-size:9px;'>{lin}{c_let}</span></td>"
                )
        html_table.append("</tr>")
    html_table.append("</tbody></table></div>")
    st.markdown("".join(html_table), unsafe_allow_html=True)


def obter_dias_semana_vigente() -> list[tuple[date, str, str]]:
    """Retorna os 7 dias da semana vigente (Segunda a Domingo) com datas e rótulos."""
    hoje = date.today()
    seg = hoje - timedelta(days=hoje.weekday())
    nomes = ["SEGUNDA", "TERÇA", "QUARTA", "QUINTA", "SEXTA", "SÁBADO", "DOMINGO"]
    saida = []
    for i in range(7):
        d = seg + timedelta(days=i)
        saida.append((d, nomes[i], d.strftime("%d/%m")))
    return saida


def renderizar_grade_calendario_semanal_operador(username: str, modo: str = "Coletor"):
    """
    Desenha a Grade de Calendário da Semana Vigente (Segunda a Domingo) de forma minimalista:
    - Exibe apenas ID, Hospital de Origem, Setor do HC pelo qual está responsável no dia e Situação das Amostras.
    - Oculta Nome e Prontuário para Coletores e Processadores.
    """
    dias_sem = obter_dias_semana_vigente()
    cols_cal = st.columns(7)

    setores_semana = fetch_all(
        """SELECT data_dia, dia_semana_nome, hospital, setor_hc
           FROM hpb_escala_setores_semana WHERE username = :u""",
        {"u": username},
    )

    if modo == "Coletor":
        agendas_op = fetch_all(
            """SELECT id_agenda, id_hpb_gerado, hospital, setor_coleta, data_cirurgia,
                      dia_semana_mapa, sala_horario, status_escala
               FROM hpb_agenda_cirurgica WHERE coletor_escalado = :u""",
            {"u": username},
        )
        coletas_op = fetch_all(
            """SELECT id_hpb, hospital, setor_hospital_coleta, data_coleta, hora_coleta,
                      status_operacional, coletor_confirmou_travou
               FROM hpb_coletas_ifera WHERE responsavel_coleta = :u""",
            {"u": username},
        )
    else:
        agendas_op = fetch_all(
            """SELECT id_agenda, id_hpb_gerado, hospital, setor_coleta, data_cirurgia,
                      dia_semana_mapa, sala_horario, status_escala
               FROM hpb_agenda_cirurgica WHERE processador_escalado = :u""",
            {"u": username},
        )
        coletas_op = fetch_all(
            """SELECT id_hpb, hospital, setor_hospital_coleta, data_coleta, hora_coleta,
                      status_operacional, processador_confirmou_travou
               FROM hpb_coletas_ifera
               WHERE processador_designado = :u OR responsavel_recebimento = :u OR liberado_para_processador = 1""",
            {"u": username},
        )

    for idx, (dt_obj, nome_dia, dd_mm) in enumerate(dias_sem):
        dt_iso = dt_obj.strftime("%Y-%m-%d")
        eh_hoje = (dt_obj == date.today())
        borda_dia = "#38bdf8" if eh_hoje else "#334155"
        bg_header = "#0284c7" if eh_hoje else "#0f172a"

        cards_html = []

        # 1. Setores do HC sob responsabilidade do operador neste dia
        for s_dt, s_dia, s_hosp, s_setor in setores_semana:
            if s_dt == dt_iso or (s_dia and nome_dia in str(s_dia).upper()):
                cards_html.append(
                    f"<div style='background:#1e3a8a; border-left:3px solid #60a5fa; padding:6px; border-radius:6px; margin-bottom:6px; font-size:11px; color:#f8fafc;'>"
                    f"<b>Setor Responsável ({s_hosp}):</b><br>{s_setor}</div>"
                )

        # 2. Pacientes escalados na agenda neste dia (Apenas ID + Hospital + Setor)
        for id_ag, id_h_ger, hosp, setor, dt_cir, dia_m, sala_h, st_esc in agendas_op:
            bate_dia = (dt_cir == dt_iso) or (dd_mm in str(dia_m or "")) or (nome_dia in str(dia_m or "").upper())
            if bate_dia:
                id_mostrado = id_h_ger or f"AG-{id_ag:02d}HPB"
                cards_html.append(
                    f"<div style='background:#422006; border-left:3px solid #f59e0b; padding:6px; border-radius:6px; margin-bottom:6px; font-size:11px; color:#fef3c7;'>"
                    f"<b>ID: {id_mostrado}</b> | <b>{hosp}</b><br>"
                    f"<span>{setor or 'Bloco Cirúrgico'}</span><br>"
                    f"<span style='font-size:10px;'>{sala_h or ''} • {st_esc}</span></div>"
                )

        # 3. Coletas registradas/ativas neste dia
        for id_h, hosp, setor_h, dt_c, hr_c, st_op, travou_flag in coletas_op:
            if dt_c == dt_iso:
                cor_c = "#064e3b" if travou_flag else "#1e293b"
                borda_c = "#10b981" if travou_flag else "#38bdf8"
                status_curto = "Confirmado/Travado" if travou_flag else st_op
                cards_html.append(
                    f"<div style='background:{cor_c}; border-left:3px solid {borda_c}; padding:6px; border-radius:6px; margin-bottom:6px; font-size:11px; color:#f8fafc;'>"
                    f"<b>ID: {id_h}</b> | <b>{hosp}</b><br>"
                    f"<span>{setor_h or 'Bloco Cirúrgico'}</span><br>"
                    f"<span style='font-size:10px; color:#94a3b8;'>{hr_c or ''} • {status_curto}</span></div>"
                )

        if not cards_html:
            cards_html.append(
                "<div style='color:#64748b; font-size:11px; text-align:center; padding:18px 4px;'>Sem escala no dia</div>"
            )

        cols_cal[idx].markdown(
            f"""
            <div style="background:#0b1120; border:1px solid {borda_dia}; border-radius:8px; min-height:190px; overflow:hidden;">
                <div style="background:{bg_header}; color:#f8fafc; padding:6px; text-align:center; font-weight:700; font-size:12px;">
                    {nome_dia}<br><span style="font-size:11px; font-weight:400;">{dd_mm}</span>
                </div>
                <div style="padding:8px;">
                    {"".join(cards_html)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# --- 8. ASSIDUIDADE, TROCAS (3 + 1 MOACYR), REGRA DE 12H E EXTRATORES PDF ---
def obter_ranking_participacao_coletores() -> pd.DataFrame:
    usuarios_col = fetch_all(
        "SELECT username, nome_completo, email, telefone FROM hpb_usuarios WHERE role IN ('Coletor', 'Coordenacao_Escala') AND ativo = 1"
    )
    coletas_df = fetch_df(
        "SELECT responsavel_coleta, data_coleta FROM hpb_coletas_ifera WHERE data_coleta IS NOT NULL"
    )
    hoje = date.today()
    linhas = []
    for u, nome, em, tel in usuarios_col:
        sub = coletas_df[coletas_df["responsavel_coleta"] == u] if not coletas_df.empty else pd.DataFrame()
        total = len(sub)
        ult_data, dias_sem = None, 999
        if total > 0:
            datas_validas = pd.to_datetime(sub["data_coleta"], errors="coerce").dropna()
            if not datas_validas.empty:
                ult_dt = datas_validas.max().date()
                ult_data = ult_dt.strftime("%d/%m/%Y")
                dias_sem = (hoje - ult_dt).days
        linhas.append({
            "Username": u,
            "Coletor": nome,
            "Telefone": tel or "-",
            "E-mail (@ufpe.br)": em,
            "Total de Coletas Realizadas": total,
            "Data da Última Coleta": ult_data or "Nunca coletou",
            "Dias sem Coletar": dias_sem,
        })
    df_out = pd.DataFrame(linhas)
    if not df_out.empty:
        df_out = df_out.sort_values(by=["Dias sem Coletar", "Total de Coletas Realizadas"], ascending=[False, True]).reset_index(drop=True)
    return df_out


def obter_ranking_participacao_processadores() -> pd.DataFrame:
    usuarios_proc = fetch_all(
        "SELECT username, nome_completo, email, telefone FROM hpb_usuarios WHERE role IN ('Processador', 'Coordenacao_Escala') AND ativo = 1"
    )
    proc_df = fetch_df(
        "SELECT responsavel_recebimento, data_recebimento FROM hpb_coletas_ifera WHERE data_recebimento IS NOT NULL AND data_recebimento != ''"
    )
    hoje = date.today()
    linhas = []
    for u, nome, em, tel in usuarios_proc:
        sub = proc_df[proc_df["responsavel_recebimento"] == u] if not proc_df.empty else pd.DataFrame()
        total = len(sub)
        ult_data, dias_sem = None, 999
        if total > 0:
            datas_validas = pd.to_datetime(sub["data_recebimento"], errors="coerce").dropna()
            if not datas_validas.empty:
                ult_dt = datas_validas.max().date()
                ult_data = ult_dt.strftime("%d/%m/%Y")
                dias_sem = (hoje - ult_dt).days
        linhas.append({
            "Username": u,
            "Processador": nome,
            "Telefone": tel or "-",
            "E-mail (@ufpe.br)": em,
            "Total de Processamentos Realizados": total,
            "Data do Último Processamento": ult_data or "Nunca processou",
            "Dias sem Processar": dias_sem,
        })
    df_out = pd.DataFrame(linhas)
    if not df_out.empty:
        df_out = df_out.sort_values(by=["Dias sem Processar", "Total de Processamentos Realizados"], ascending=[False, True]).reset_index(drop=True)
    return df_out


def contar_trocas_mes_operador(username: str, mes_ano: str = None) -> tuple[int, int]:
    mes_ref = mes_ano or date.today().strftime("%Y-%m")
    rows = fetch_all(
        """SELECT autorizado_extra_moacyr FROM hpb_trocas_escala
           WHERE solicitante_username = :u AND mes_referencia = :m AND status_troca = 'EFETIVADA'""",
        {"u": username, "m": mes_ref},
    )
    normais = sum([1 for r in rows if int(r[0] or 0) == 0])
    extras = sum([1 for r in rows if int(r[0] or 0) == 1])
    return normais, extras


def verificar_regra_12h_escala_cirurgica():
    vagas = fetch_all("""
        SELECT id_agenda, data_cirurgia, sala_horario, hospital, nome_paciente,
               diagnostico_mapa, cirurgia_proposta, indicado_por_moacyr,
               alerta_12h_moacyr_enviado, criado_em
        FROM hpb_agenda_cirurgica
        WHERE status_escala = 'AGUARDANDO_COLETOR' AND (coletor_escalado IS NULL OR coletor_escalado = '')
    """)
    if not vagas:
        return
    agora = datetime.now()
    df_part = obter_ranking_participacao_coletores()
    dest_gestores = [
        r[0] for r in fetch_all(
            "SELECT email FROM hpb_usuarios WHERE role IN ('Pesquisador_Responsavel', 'Coordenacao_Escala') AND ativo = 1"
        )
    ] or ["moacyr.rego@ufpe.br", "valecia.costa@ufpe.br"]

    for v in vagas:
        id_ag, dt_c, sala_h, hosp, pac, diag, cir, ind_moacyr, alerta_env, criado_str = v
        try:
            dt_criado = datetime.strptime(criado_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            dt_criado = agora - timedelta(hours=13)
        horas_decorridas = (agora - dt_criado).total_seconds() / 3600.0
        if horas_decorridas >= PRAZO_LIMITE_ESCALA_HORAS:
            if ind_moacyr and str(ind_moacyr).strip():
                run_sql(
                    """UPDATE hpb_agenda_cirurgica
                       SET coletor_escalado = :c, data_hora_candidatura_coletor = :dh, status_escala = 'ELEITO_AUTOMATICAMENTE_PI'
                       WHERE id_agenda = :id""",
                    {"c": ind_moacyr.strip(), "dh": agora.strftime("%Y-%m-%d %H:%M:%S"), "id": id_ag},
                )
            elif not alerta_env:
                tabela_txt = df_part.to_string(index=False) if not df_part.empty else "Sem coletores."
                disparar_email_hpb(
                    dest_gestores,
                    f"[ALERTA 12h REDE HPB] Paciente Vermelho sem Coletor - {hosp}",
                    f"A cirurgia #{id_ag} no {hosp} ({dt_c} - {sala_h}) completou {horas_decorridas:.1f}h sem coletor.\n\n{tabela_txt}",
                )
                run_sql("UPDATE hpb_agenda_cirurgica SET alerta_12h_moacyr_enviado = 1 WHERE id_agenda = :id", {"id": id_ag})


def gerar_pdf_preenchido_exemplo_hpb(id_hpb: str = "97HPB") -> bytes:
    linhas = [
        "BT", "/F1 10 Tf", "36 800 Td",
        f"(FICHA CLINICA - REDE HPB   FICHA DE REGISTRO N: {id_hpb}) Tj",
        "0 -16 Td (Centro/Hospital: HC   N Prontuario: 22451140) Tj",
        "0 -16 Td (Nome do Paciente: MARIA DE LOURDES DA SILVA) Tj",
        "0 -16 Td (Sexo: Fem   Data de Entrada: 11/11/2026) Tj",
        "0 -16 Td (Naturalidade: Recife - PE   Data de Nascimento: 14/05/1962) Tj",
        "0 -16 Td (Residencia (cidade e estado): Recife - PE   Data da Biopsia: 11/11/2026) Tj",
        "0 -16 Td (Escolaridade: Medio   COR: Parda   TRABALHO: aposentado) Tj",
        "0 -16 Td (Tabagismo: Ex   Tipo/Dose: 10 anos-maco   Etilismo: Nao   IMC: 23.8) Tj",
        "0 -16 Td (Diabetes - S   Hipertensao - S   CA19.9: 485.2   CA125: 38.4) Tj",
        "0 -16 Td (Descricao da Lesao: Adenocarcinoma Ductal de Cabeca de Pancreas   cTNM: cT2N1M0   cEstadio: IIB) Tj",
        "0 -16 Td (Localizacao: CABECA   Tipo de Cirurgia: Duodenopancreatectomia cefalica) Tj",
        "0 -16 Td (TNM-ESTADIAMENTO: pT2N1M0   Margens Livres: Sim   Numero de linfonodos: 18 examinados / 2 positivos) Tj",
        "0 -16 Td (Tto adjuvante instituido: Folfirinox) Tj",
        "0 -22 Td (RP10 - Formulario de entrega e registro de amostras) Tj",
        f"0 -16 Td (ID Paciente: {id_hpb}   Centro: HC   Intervencao: RC00   Ciclo da Quimio: 00) Tj",
        "0 -16 Td (1. Coleta de sangue realizada? (1) Sim   Volume Sangue: 26.0 mL   Caracteristica: (1) Normal) Tj",
        "0 -16 Td (2. Coleta de Tumor realizada? (1) Sim   Peso Biopsia: 42.0 mg   Total aliquotas: 7) Tj",
        "0 -16 Td (2. Local da coleta: Bloco Cirurgico HC-UFPE - Cidade Universitaria - Recife/PE) Tj",
        "0 -16 Td (3. Data da coleta: 11/11/2026   4. Hora: 08:45   8. Responsavel pela coleta: claudio.cordeiro) Tj",
        "0 -16 Td (10. Temperatura no momento do armazenamento: Max.: 6.5 C   Min.: 2.5 C   Atual: 4.0 C) Tj",
        "0 -16 Td (TCLE HC-UFPE - REDE INTERNACIONAL DE PESQUISA PARA CANCER HEPATO-PANCREATO BILIAR) Tj",
        "0 -16 Td (Eu, MARIA DE LOURDES DA SILVA, CPF: 123.456.789-00, abaixo assinado, concordo em participar.) Tj",
        "0 -16 Td (Guarda Biorrepositorio: SIM   Testemunha 1: Amanda Albuquerque   Testemunha 2: Jessica Winely) Tj",
        "ET",
    ]
    stream = "\n".join(linhas).encode("latin-1", errors="ignore")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream\nendobj\n",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offs = []
    for o in objs:
        offs.append(len(pdf))
        pdf.extend(o)
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offs:
        pdf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1"))
    return bytes(pdf)


def _limpar_valor_extraido(val: str) -> str:
    if not val:
        return ""
    limpo = re.sub(r"[|_]{2,}", "", str(val)).strip()
    if limpo.lower() in ["", "nº prontuário:", "data de nascimento:", "data da biópsia:", "cestádio:", "ctnm:"]:
        return ""
    return limpo


def extrair_todos_dados_pdf_universal(arquivo_pdf) -> dict:
    arquivo_pdf.seek(0)
    reader = pypdf.PdfReader(arquivo_pdf)
    txt = "\n".join([p.extract_text() or "" for p in reader.pages])
    txt_up = txt.upper()
    dados = {}

    try:
        fields = reader.get_fields()
        if fields:
            for k, v in fields.items():
                val_f = v.get("/V", "") if isinstance(v, dict) else ""
                if val_f:
                    dados[f"acro_{k}"] = str(val_f).strip()
    except Exception:
        pass

    if "IL-34" in txt_up or "IL-36" in txt_up:
        dados["modelo_tcle"] = "TCLE_HUOC_SO_SANGUE"
    elif "MICRORNA 200" in txt_up or "TP53" in txt_up:
        dados["modelo_tcle"] = "TCLE_HUOC_SANGUE_E_TECIDO"
    elif "REDE INTERNACIONAL" in txt_up or "REDE HPB" in txt_up or "BIORREPOSIT" in txt_up:
        dados["modelo_tcle"] = "TCLE_HC_REDEHPB"

    padroes = {
        "id_hpb": r"(?:FICHA DE REGISTRO N[º°]?|ID Paciente):\s*([0-9]{1,3}\s*HPB)",
        "centro_hospital": r"(?:Centro/Hospital|Centro):\s*(HC|HUOC|HCP|HSE)",
        "prontuario": r"N[º°]?\s*Prontu[áa]rio:\s*([0-9]{5,12})",
        "nome_paciente": r"Nome do Paciente:\s*([A-Za-zÀ-ÿ\s]{4,60}?)(?:\n|Sexo|$)",
        "voluntario_tcle": r"Eu,\s*([A-Za-zÀ-ÿ\s]{4,60}?)\s*,?\s*(?:CPF|abaixo assinado)",
        "cpf": r"CPF[:\s]*(\d{3}\.?\d{3}\.?\d{3}\-?\d{2})",
        "sexo": r"Sexo[:\s]*(Masc|Fem)",
        "data_nascimento": r"Data de Nascimento:\s*(\d{2}/\d{2}/\d{4})",
        "naturalidade": r"Naturalidade:\s*([A-Za-zÀ-ÿ\s\-/]+?)(?:Data de Nascimento|\n|$)",
        "residencia_cidade_uf": r"Resid[êe]ncia\s*\(cidade e estado\):\s*([A-Za-zÀ-ÿ\s\-/]+?)(?:Data da Bi[óo]psia|\n|$)",
        "escolaridade": r"Escolaridade[:\s]*(Analfabeto|B[áa]sico|M[ée]dio|Superior)",
        "cor_raca": r"COR:\s*(Branca|Parda|Negra)",
        "situacao_trabalho": r"TRABALHO:\s*(empregado|desempregado|licen[çc]a de sa[úu]de|aposentado|trabalho dom[ée]stico|outros)",
        "tabagismo": r"Tabagismo[:\s]*(Sim|N[ãa]o|Ex)",
        "etilismo": r"Etilismo[:\s]*(Sim|N[ãa]o|Ex)",
        "imc": r"IMC[:\s]*([0-9]{1,2}[\.,][0-9]{1,2})",
        "diabetes": r"Diabetes\s*[\–\-]\s*(S|N)",
        "hipertensao": r"Hipertens[ãa]o\s*[\–\-]\s*(S|N)",
        "ca19_9": r"CA19\.9:\s*([0-9\.,]+)",
        "ca125": r"CA125:\s*([0-9\.,]+)",
        "descricao_lesao": r"Descri[çc][ãa]o da Les[ãa]o:\s*([^\n]+?)(?:cTNM|$)",
        "ctnm": r"cTNM:\s*([A-Za-z0-9\s]+?)(?:cEst[áa]dio|\n|$)",
        "linfadenectomia_num_linfonodos": r"N[úu]mero de linfonodos:\s*([^\n]+)",
        "hora_coleta": r"4\.\s*Hora:\s*(\d{2}:\d{2})",
        "responsavel_coleta": r"8\.\s*Respons[áa]vel pela coleta:\s*([A-Za-z0-9\._\s]+?)(?:\n|$)",
        "vol_sangue_ml": r"Volume Sangue:\s*([0-9\.,]+)",
        "peso_tumor_mg": r"Peso Bi[óo]psia:\s*([0-9\.,]+)",
        "testemunha_1": r"Testemunha 1:\s*([A-Za-zÀ-ÿ\s]+?)(?:Testemunha 2|\n|$)",
        "testemunha_2": r"Testemunha 2:\s*([A-Za-zÀ-ÿ\s]+?)(?:\n|$)",
    }

    for k, rgx in padroes.items():
        m = re.search(rgx, txt, flags=re.IGNORECASE)
        if m:
            val_l = _limpar_valor_extraido(m.group(1))
            if val_l:
                dados[k] = val_l

    if not dados.get("nome_paciente") and dados.get("voluntario_tcle"):
        dados["nome_paciente"] = dados["voluntario_tcle"]

    return dados


def injetar_dados_pdf_no_session_state(ext: dict, fallback_agenda: dict = None):
    fb = fallback_agenda or {}
    if ext.get("id_hpb"):
        st.session_state["f_id_hpb"] = ext["id_hpb"].upper()
    if ext.get("centro_hospital") in ["HC", "HUOC", "HCP", "HSE"]:
        st.session_state["f_centro_hosp"] = ext["centro_hospital"]
    elif fb.get("hospital") in ["HC", "HUOC", "HCP", "HSE"]:
        st.session_state["f_centro_hosp"] = fb["hospital"]

    if ext.get("nome_paciente") or fb.get("nome_paciente"):
        st.session_state["f_nome_pac"] = ext.get("nome_paciente") or fb.get("nome_paciente", "")
    if ext.get("prontuario") or fb.get("prontuario"):
        st.session_state["f_prontuario"] = ext.get("prontuario") or fb.get("prontuario", "")
    if ext.get("cpf"):
        st.session_state["f_cpf"] = ext["cpf"]
    if ext.get("descricao_lesao") or fb.get("diagnostico"):
        st.session_state["f_hip_diag"] = ext.get("descricao_lesao") or fb.get("diagnostico", "")
    if ext.get("modelo_tcle") in MODELOS_TCLE_HOMOLOGADOS:
        st.session_state["f_modelo_tcle"] = ext["modelo_tcle"]
    if ext.get("testemunha_1"):
        st.session_state["f_test_1"] = ext["testemunha_1"]
    if ext.get("testemunha_2"):
        st.session_state["f_test_2"] = ext["testemunha_2"]
    if ext.get("hora_coleta"):
        st.session_state["f_hr_coleta"] = ext["hora_coleta"]
    if ext.get("responsavel_coleta"):
        st.session_state["f_resp_coleta"] = ext["responsavel_coleta"]
    if ext.get("vol_sangue_ml"):
        try:
            st.session_state["f_vol_sangue"] = float(ext["vol_sangue_ml"].replace(",", "."))
        except Exception:
            pass
    if ext.get("peso_tumor_mg"):
        try:
            st.session_state["f_peso_tumor"] = float(ext["peso_tumor_mg"].replace(",", "."))
        except Exception:
            pass
    if ext.get("sexo") in ["Masc", "Fem"]:
        st.session_state["f_fc_sexo"] = ext["sexo"]
    if ext.get("data_nascimento"):
        st.session_state["f_fc_nasc"] = ext["data_nascimento"]
    if ext.get("naturalidade"):
        st.session_state["f_fc_nat"] = ext["naturalidade"]
    if ext.get("residencia_cidade_uf"):
        st.session_state["f_fc_res"] = ext["residencia_cidade_uf"]
    if ext.get("cor_raca") in ["Parda", "Branca", "Negra"]:
        st.session_state["f_fc_cor"] = ext["cor_raca"]
    if ext.get("imc"):
        try:
            st.session_state["f_fc_imc"] = float(ext["imc"].replace(",", "."))
        except Exception:
            pass
    if ext.get("ca19_9"):
        st.session_state["f_fc_ca199"] = ext["ca19_9"]
    if ext.get("ca125"):
        st.session_state["f_fc_ca125"] = ext["ca125"]
    if ext.get("ctnm"):
        st.session_state["f_fc_ctnm"] = ext["ctnm"]
    if ext.get("linfadenectomia_num_linfonodos"):
        st.session_state["f_fc_linf"] = ext["linfadenectomia_num_linfonodos"]


def extrair_mapa_cirurgico_pdf(arquivo_pdf, hospital_selecionado: str) -> pd.DataFrame:
    arquivo_pdf.seek(0)
    reader = pypdf.PdfReader(arquivo_pdf)
    texto_completo = "\n".join([p.extract_text() or "" for p in reader.pages])
    linhas = [l.strip() for l in texto_completo.splitlines() if l.strip()]

    kw_pancreas = ["PANCREAS", "PÂNCREAS", "PANCREATECTOMIA", "DUODENOPANCREATECTOMIA", "WHIPPLE", "FRANTZ", "FRANZS", "IPMN", "PERIAMPULAR"]
    kw_hepatico = ["HEPATICA", "HEPÁTICA", "HEPATICO", "HEPÁTICO", "HEPATECTOMIA", "FIGADO", "FÍGADO", "HCC", "HEPATOCELULAR"]
    kw_biliar = ["COLEDOCO", "COLÉDOCO", "BILIAR", "VESICULA", "VESÍCULA", "KLATSKIN", "COLANGIO", "PAPILA", "COLELAP", "COLELITIASE", "COLELITÍASE"]
    termos_diag = [
        r"NEOPLASIA DE P[ÂA]NCREAS", r"LIT[ÍI]ASE INTRAHEP[ÁA]TICA\s*\+?\s*COLEDOCOLIT[ÍI]ASE",
        r"COLELIT[ÍI]ASE", r"TUMOR DE KLATSKIN", r"COLANGIOCARCINOMA", r"ADENOCARCINOMA[A-ZÇÃÕÁÉÍÓÚ\s]*",
        r"HCC", r"IPMN", r"H[ÉE]RNIA [A-ZÇÃÕÁÉÍÓÚ\s]+", r"PO COLECTOMIA SUBTOTAL",
        r"DOENÇA DE CROHN[A-ZÇÃÕÁÉÍÓÚ\s]*", r"DOENÇA HEMORROID[ÁA]RIA", r"DRC", r"OBESIDADE",
        r"ACALASIA", r"F[ÍI]STULA [A-ZÇÃÕÁÉÍÓÚ\s]+", r"CISTO PILONIDAL",
    ]
    padrao_diag = "(" + "|".join(termos_diag) + ")"
    dia_atual, sala_hora_atual, registros = "", "", []

    for linha in linhas:
        if "SALA REGISTRO NOME" in linha.upper():
            continue
        m_dia = re.match(r"^(SEGUNDA|TERÇA|TERCA|QUARTA|QUINTA|SEXTA|SÁBADO|SABADO|DOMINGO)\s+(\d{2}/\d{2})", linha.upper())
        if m_dia:
            dia_atual = f"{m_dia.group(1)} {m_dia.group(2)}"
            continue
        m_sala = re.match(r"^(SALA\s*\d+\s*\d{1,2}H)$", linha.upper())
        if m_sala:
            sala_hora_atual = m_sala.group(1)
            continue
        m_reg = re.match(r"^(?:(SALA\s*\d+\s*\d{1,2}H)\s*)?(\d{6,10})\s+(.+)$", linha)
        if m_reg:
            if m_reg.group(1):
                sala_hora_atual = m_reg.group(1).strip()
            prontuario, resto = m_reg.group(2).strip(), m_reg.group(3).strip()
            nome_pac, diag_str, cir_str = resto, "", ""
            m_d = re.search(padrao_diag, resto.upper())
            if m_d:
                i_ini, i_fim = m_d.span()
                nome_pac, diag_str, cir_str = resto[:i_ini].strip(), resto[i_ini:i_fim].strip(), resto[i_fim:].strip()
            up_all = resto.upper()
            sitio, elegivel = "Não-HPB", False
            if any(k in up_all for k in kw_pancreas):
                sitio, elegivel = "P", True
            elif any(k in up_all for k in kw_hepatico):
                sitio, elegivel = "H", True
            elif any(k in up_all for k in kw_biliar):
                sitio, elegivel = ("Ctl" if "COLELAP" in up_all else "B"), True
            registros.append({
                "Incluir_na_Agenda_HPB": elegivel,
                "Dia_Mapa": dia_atual,
                "Sala_Horario": sala_hora_atual,
                "Hospital": hospital_selecionado,
                "Setor": "Bloco Cirúrgico (Agendamento Cirúrgico)",
                "Prontuario": prontuario,
                "Nome_Paciente": nome_pac,
                "Diagnostico": diag_str or resto,
                "Cirurgia_e_Cirurgiao": cir_str,
                "Sitio_HPB": sitio if elegivel else "P",
            })
    return pd.DataFrame(registros)


def gerar_proximo_id_hpb() -> str:
    rows = fetch_all("SELECT id_hpb FROM hpb_coletas_ifera")
    rows_ag = fetch_all("SELECT id_hpb_gerado FROM hpb_agenda_cirurgica WHERE id_hpb_gerado IS NOT NULL")
    max_num = 96
    for r in rows + rows_ag:
        if r[0]:
            m = re.match(r"^(\d+)\s*HPB", str(r[0]).strip().upper())
            if m:
                max_num = max(max_num, int(m.group(1)))
    return f"{max_num + 1:02d}HPB"


def formatar_codigo_intervencao(tipo_rp10: str, ciclo_quimio: str) -> str:
    if "RC" in tipo_rp10:
        return "RC00"
    num = re.sub(r"\D", "", ciclo_quimio or "01") or "01"
    return f"AQ{int(num):02d}" if "AQ" in tipo_rp10 else f"DP{int(num):02d}"


def sugerir_proxima_posicao_caixa_10x10(sitio_hpb: str, cod_biotipo: int, qtd_aliquotas: int) -> tuple[str, str, str, int]:
    pref_sitio = "P" if sitio_hpb in ["P", "PAPILA", "Ctl"] else ("H" if sitio_hpb == "H" else "B")
    caixa_padrao = f"{pref_sitio}{cod_biotipo}.01"
    ocupados = fetch_all("SELECT n_aliquotas_entrada FROM hpb_biobanco_aliquotas WHERE caixa = :cx", {"cx": caixa_padrao})
    total_ja_alocado = sum([int(r[0] or 0) for r in ocupados])
    letras = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    num_caixa_seq = (total_ja_alocado // 100) + 1
    caixa_final = f"{pref_sitio}{cod_biotipo}.{num_caixa_seq:02d}"
    pos_na_caixa = total_ja_alocado % 100
    linha_1_a_10 = (pos_na_caixa // 10) + 1
    col_ini = pos_na_caixa % 10
    cols_usadas = [letras[(col_ini + i) % 10] for i in range(max(1, qtd_aliquotas))]
    return caixa_final, "1.0", ", ".join(cols_usadas), linha_1_a_10


def gerar_pdf_etiquetas_prontas_impressao(lista_etiquetas: list[dict]) -> bytes:
    linhas_stream = [
        "BT", "/F1 11 Tf", "36 805 Td",
        "(REDE HPB (HC / HUOC / HCP) - ETIQUETAS CRIOGENICAS PRONTAS PARA IMPRESSAO) Tj", "ET",
    ]
    col_x, row_y, col_idx = [36, 174, 312, 450], 760, 0
    for etq in lista_etiquetas:
        x, y = col_x[col_idx], row_y
        cod = str(etq.get("codigo", "")).encode("ascii", "ignore").decode("ascii")
        dt = str(etq.get("data", "")).encode("ascii", "ignore").decode("ascii")
        bio = str(etq.get("biotipo", "")).encode("ascii", "ignore").decode("ascii")
        pos = str(etq.get("posicao", "")).encode("ascii", "ignore").decode("ascii")
        hosp = str(etq.get("hospital", "")).encode("ascii", "ignore").decode("ascii")
        linhas_stream.extend([
            f"0.7 w {x} {y-40} 130 50 re S", "BT",
            f"/F1 10 Tf {x+6} {y-5} Td ({cod}) Tj",
            f"/F1 9 Tf 0 -12 Td ({dt}) Tj",
            f"/F1 6.5 Tf 0 -11 Td ({bio} | {hosp}) Tj",
            f"/F1 6.5 Tf 0 -9 Td (Pos: {pos}) Tj", "ET",
        ])
        col_idx += 1
        if col_idx >= 4:
            col_idx, row_y = 0, row_y - 58
            if row_y < 55:
                break
    conteudo_stream = "\n".join(linhas_stream).encode("latin-1", errors="ignore")
    objetos = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(conteudo_stream)} >>\nstream\n".encode("latin-1") + conteudo_stream + b"\nendstream\nendobj\n",
    ]
    pdf, offsets = bytearray(b"%PDF-1.4\n"), []
    for obj in objetos:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objetos)+1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        pdf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(f"trailer\n<< /Size {len(objetos)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1"))
    return bytes(pdf)


def exportar_planilha_rede_hpb_xlsx() -> bytes:
    buffer = io.BytesIO()
    df_coletas = fetch_df("SELECT * FROM hpb_coletas_ifera ORDER BY id_hpb")
    df_biobanco = fetch_df("SELECT * FROM hpb_biobanco_aliquotas ORDER BY id_hpb, cod_biotipo")
    df_ficha = fetch_df("SELECT * FROM hpb_ficha_clinica ORDER BY id_hpb")
    df_rna = fetch_df("SELECT * FROM hpb_extracao_rna_pagina13 ORDER BY id_hpb")
    df_cult = fetch_df("SELECT * FROM hpb_cultivos_organoides_cafs ORDER BY id_hpb")
    df_saidas = fetch_df("SELECT * FROM hpb_saidas_biobanco ORDER BY id_saida")
    df_agenda = fetch_df("SELECT * FROM hpb_agenda_cirurgica ORDER BY data_cirurgia DESC")
    df_trocas = fetch_df("SELECT * FROM hpb_trocas_escala ORDER BY id_troca DESC")
    df_erros = fetch_df("SELECT * FROM hpb_erros_revisao_cq ORDER BY id_erro DESC")

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_coletas.to_excel(writer, sheet_name="Controle dos docs", index=False)
        df_biobanco.to_excel(writer, sheet_name="Biobanco", index=False)
        df_ficha.to_excel(writer, sheet_name="Ficha Clinica", index=False)
        df_rna.to_excel(writer, sheet_name="Página13", index=False)
        df_cult.to_excel(writer, sheet_name="Organoides_e_CAFs", index=False)
        df_saidas.to_excel(writer, sheet_name="Saídas", index=False)
        df_agenda.to_excel(writer, sheet_name="Futuras coletas", index=False)
        df_trocas.to_excel(writer, sheet_name="Trocas de Escala", index=False)
        df_erros.to_excel(writer, sheet_name="Revisao_Qualidade_CQ", index=False)
    return buffer.getvalue()


# --- 9. TELA DE LOGIN ASSOCIADA À CONTA GOOGLE INSTITUCIONAL (@ufpe.br) ---
if "hpb_logged_in" not in st.session_state:
    st.session_state.hpb_logged_in = False

if not st.session_state.hpb_logged_in:
    st.title("Rede HPB — Plataforma Clínica, Biobanco, Qualidade e Governança")
    st.caption(
        "Acesso Institucional Vinculado ao Domínio Google Workspace @ufpe.br | Centros: HC-UFPE, HUOC-UPE e HCP"
    )

    tab_login, tab_cad = st.tabs(["Login Institucional (@ufpe.br)", "Credenciamento de Membro (@ufpe.br)"])

    with tab_login:
        c1, c2 = st.columns(2)
        email_ou_user = c1.text_input(
            "E-mail Google Institucional (@ufpe.br)",
            placeholder="nome.sobrenome@ufpe.br",
            key="login_email_ufpe_input",
        ).strip().lower()
        p_in = c2.text_input("Senha", type="password", key="login_password_input")

        if st.button("Autenticar com Conta @ufpe.br", type="primary", key="btn_entrar_login"):
            row = fetch_one(
                """SELECT username, password_hash, role, nome_completo, email
                   FROM hpb_usuarios
                   WHERE (LOWER(email) = :em OR LOWER(username) = :em) AND ativo = 1""",
                {"em": email_ou_user},
            )
            if not row:
                st.error("Conta institucional não localizada.")
            elif not str(row[4]).lower().endswith("@ufpe.br"):
                st.error("Acesso restrito a contas vinculadas ao domínio institucional '@ufpe.br'.")
            elif check_hashes(p_in, row[1]):
                agora_login = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                run_sql(
                    "UPDATE hpb_usuarios SET ultimo_login_em = :ul WHERE username = :u",
                    {"ul": agora_login, "u": row[0]},
                )
                st.session_state.hpb_logged_in = True
                st.session_state.username = row[0]
                st.session_state.role = row[2]
                st.session_state.nome_usuario = row[3]
                st.session_state.email_ufpe = row[4]
                log_audit("Login @ufpe.br", "-", f"Acesso realizado por {row[3]} ({row[4]} | {row[2]})")
                st.rerun()
            else:
                st.error("Senha incorreta.")

        with st.expander("Contas Institucionais @ufpe.br Pré-Configuradas"):
            st.markdown(
                "- **Pesquisador Responsável (Prof. Moacyr):** `moacyr.rego@ufpe.br` (ou `moacyr`) | senha `Moacyr@HPB2026`\n"
                "- **Pesquisadora / Coordenação (Valécia):** `valecia.costa@ufpe.br` (ou `valecia.costa`) | senha `Rede@HPB2026`\n"
                "- **Controle de Qualidade (2ª Checagem, Agilidade e Viabilidade):** `qualidade.hpb@ufpe.br` (ou `qualidade.hpb`) | senha `Quali@HPB2026`\n"
                "- **Bioinformata (Consulta Completa Clínico-Genômica):** `bioinformatica.hpb@ufpe.br` (ou `bioinfo.hpb`) | senha `Bio@HPB2026`\n"
                "- **Coletores (Interface Minimalista Semanal):** `jessica.winely@ufpe.br`| senha `Rede@HPB2026`\n"
                "- **Processadores Autorizados para Organoides:** `jose.lauriano@ufpe.br` (`jose.lauriano`), `julia.braga@ufpe.br` (`julia.pereira`) | senha `Proc@HPB2026`\n"
                "- **Demais Processadores:** `luisa.sandrelly@ufpe.br`, `carolina.hpb@ufpe.br` | senha `Proc@HPB2026`"
            )

    with tab_cad:
        st.subheader("Credenciamento de Novo Membro (Obrigatório E-mail Google @ufpe.br)")
        c_u, c_n, c_t = st.columns(3)
        novo_u = c_u.text_input("Username (ex: nome.sobrenome)", key="cad_username_input").strip().lower()
        novo_nome = c_n.text_input("Nome Completo", key="cad_nome_input").strip()
        novo_tel = c_t.text_input("Telefone / WhatsApp", key="cad_tel_input").strip()
        c_e, c_r, c_s = st.columns(3)
        novo_email = c_e.text_input("E-mail Institucional (@ufpe.br)", placeholder="usuario@ufpe.br", key="cad_email_input").strip().lower()
        novo_role = c_r.selectbox(
            "Atribuição na Rede HPB",
            ["Coletor", "Processador", "Controle_Qualidade", "Bioinformata", "Agenda_HPB", "Coordenacao_Escala"],
            key="cad_role_select",
        )
        nova_senha = c_s.text_input("Criar Senha de Acesso", type="password", key="cad_password_input")
        chave_aut = st.text_input("Chave de Autorização da Rede HPB (Padrão: REDEHPB-2026)", type="password", key="cad_chave_input")

        if st.button("Cadastrar Membro @ufpe.br", key="btn_cadastrar_membro"):
            if chave_aut != "REDEHPB-2026":
                st.error("Chave de autorização incorreta.")
            elif not novo_email.endswith("@ufpe.br"):
                st.error("O e-mail deve obrigatoriamente pertencer ao domínio institucional '@ufpe.br'.")
            elif not novo_u or not novo_nome or len(nova_senha) < 6:
                st.error("Preencha todos os campos obrigatórios (senha mín. 6 caracteres).")
            elif fetch_one("SELECT username FROM hpb_usuarios WHERE username = :u OR LOWER(email) = :e", {"u": novo_u, "e": novo_email}):
                st.error("Usuário ou e-mail @ufpe.br já cadastrado.")
            else:
                run_sql(
                    "INSERT INTO hpb_usuarios VALUES (:u, :n, :p, :e, :t, :r, 1, :ul)",
                    {
                        "u": novo_u,
                        "n": novo_nome,
                        "p": make_hashes(nova_senha),
                        "e": novo_email,
                        "t": novo_tel,
                        "r": novo_role,
                        "ul": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                st.success(f"Perfil de {novo_role} criado para {novo_nome} ({novo_email})!")

# --- 10. AMBIENTE LOGADO COM INTERFACES ADAPTADAS A CADA PERFIL DE USUÁRIO ---
else:
    verificar_regra_12h_escala_cirurgica()
    role_atual = st.session_state.role

    # Define a lista de interfaces disponíveis conforme a atribuição do usuário logado
    if usuario_eh_moacyr() or usuario_pode_escalar():
        abas_disponiveis = [
            "Painel dos Pesquisadores Responsáveis (Moacyr & Valécia)",
            "Controle de Qualidade (2ª Checagem, Viabilidade e Scores)",
            "Rankings (Semana, Mês e Semestre) & Trocas de Escala",
            "Interface do Coletador (Visão Semanal Minimalista)",
            "Interface do Processador (Visão Semanal + Organoides)",
            "Área do Bioinformata (Consulta Clínico-Genômica e Criocaixas)",
            "Chat Privado entre Operadores",
        ]
    elif role_atual == "Controle_Qualidade":
        abas_disponiveis = [
            "Controle de Qualidade (2ª Checagem, Viabilidade e Scores)",
            "Rankings (Semana, Mês e Semestre) & Trocas de Escala",
            "Chat Privado entre Operadores",
        ]
    elif role_atual == "Bioinformata":
        abas_disponiveis = [
            "Área do Bioinformata (Consulta Clínico-Genômica e Criocaixas)",
            "Rankings (Semana, Mês e Semestre) & Trocas de Escala",
            "Chat Privado entre Operadores",
        ]
    elif role_atual == "Processador":
        abas_disponiveis = [
            "Interface do Processador (Visão Semanal + Organoides)",
            "Rankings (Semana, Mês e Semestre) & Trocas de Escala",
            "Chat Privado entre Operadores",
        ]
    else:
        abas_disponiveis = [
            "Interface do Coletador (Visão Semanal Minimalista)",
            "Rankings (Semana, Mês e Semestre) & Trocas de Escala",
            "Chat Privado entre Operadores",
        ]

    if "modulo_hpb_ativo" not in st.session_state or st.session_state.modulo_hpb_ativo not in abas_disponiveis:
        st.session_state.modulo_hpb_ativo = abas_disponiveis[0]

    # BARRA SUPERIOR COM MENU SUSPENSO '☰'
    top_c1, top_c2, top_c3 = st.columns([2.3, 3.7, 2.5])
    with top_c1:
        with st.popover("☰  Menu de Navegação (Rede HPB)", use_container_width=True):
            escolha_menu = st.radio(
                "Navegar para:",
                options=abas_disponiveis,
                index=abas_disponiveis.index(st.session_state.modulo_hpb_ativo),
                key="radio_menu_suspenso_hpb",
            )
            if escolha_menu != st.session_state.modulo_hpb_ativo:
                st.session_state.modulo_hpb_ativo = escolha_menu
                st.rerun()

            if usuario_pode_ver_pii():
                st.divider()
                xlsx_bytes = exportar_planilha_rede_hpb_xlsx()
                st.download_button(
                    label="Baixar Planilha REDE HPB (.xlsx)",
                    data=xlsx_bytes,
                    file_name="REDE_HPB_Automatizada_Atualizada.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="btn_dl_xlsx_popover",
                )
            st.divider()
            if st.button("Encerrar Sessão", use_container_width=True, key="btn_logout_popover"):
                st.session_state.hpb_logged_in = False
                st.rerun()

    with top_c2:
        st.markdown(f"**Interface Ativa:** `{st.session_state.modulo_hpb_ativo}`")
    with top_c3:
        st.markdown(
            f"<div style='text-align:right;'><b>{st.session_state.nome_usuario}</b><br>"
            f"<span style='font-size:12px; color:#64748b;'>{st.session_state.get('email_ufpe', '')} • <code>{role_atual}</code></span></div>",
            unsafe_allow_html=True,
        )

    st.divider()
    modulo_ativo = st.session_state.modulo_hpb_ativo

    # =========================================================================
    # INTERFACE 1: COLETADOR (MINIMALISTA, CALENDÁRIO SEMANAL E MENU LATERAL)
    # =========================================================================
    if modulo_ativo.startswith("Interface do Coletador"):
        st.subheader("Grade de Calendário da Semana Vigente — Minhas Coletas e Setores no HC")
        st.caption(
            "Visão Minimalista do Coletador: Exibe exclusivamente a ID, o Hospital/Setor de origem e a situação das amostras da sua semana."
        )

        renderizar_grade_calendario_semanal_operador(st.session_state.username, modo="Coletor")

        # Verifica se existem pacientes disponíveis sem coletor escalado na agenda
        vagas_disponiveis_coleta = fetch_all("""
            SELECT id_agenda, id_hpb_gerado, hospital, setor_coleta, dia_semana_mapa, sala_horario
            FROM hpb_agenda_cirurgica
            WHERE coletor_escalado IS NULL OR coletor_escalado = ''
            ORDER BY id_agenda DESC
        """)
        tem_vagas_acesas = len(vagas_disponiveis_coleta) > 0

        st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
        col_btn_add1, col_btn_add2 = st.columns([1.5, 2.5])

        # Botão 'Adicionar mais coletas' fica ACESO apenas se existirem pacientes vagos para assumir
        if col_btn_add1.button(
            f"➕ Adicionar mais coletas ({len(vagas_disponiveis_coleta)} disponível[is])" if tem_vagas_acesas else "➕ Adicionar mais coletas (Nenhum paciente vago na agenda)",
            type="primary" if tem_vagas_acesas else "secondary",
            disabled=(not tem_vagas_acesas),
            use_container_width=True,
            key="btn_abrir_modal_mais_coletas",
        ):
            st.session_state["mostrar_seletor_novas_coletas"] = True

        with col_btn_add2:
            with st.expander("Assumir Dia de Plantão em Setor do HC (Ambulatório, Imagem, LEC, Patologia ou Enfermaria)"):
                s_c1, s_c2, s_c3, s_c4 = st.columns([1, 1.5, 2, 1])
                hosp_plantao = s_c1.selectbox("Hospital", ["HC", "HUOC", "HCP"], key="pl_hosp_col")
                dt_plantao = s_c2.date_input("Dia da Semana", value=date.today(), key="pl_dt_col")
                setor_plantao = s_c3.selectbox("Setor de Atuação", SETORES_COLETA_HC_HPB, key="pl_setor_col")
                if s_c4.button("Registrar Setor", key="btn_reg_setor_sem"):
                    id_novo_setor = gerar_proximo_id_hpb()
                    agora_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    run_sql(
                        """INSERT INTO hpb_escala_setores_semana
                           (username, nome_operador, data_dia, dia_semana_nome, hospital, setor_hc, atribuido_por, criado_em)
                           VALUES (:u, :n, :dt, :dn, :h, :s, :u, :ce)""",
                        {
                            "u": st.session_state.username,
                            "n": st.session_state.nome_usuario,
                            "dt": dt_plantao.strftime("%Y-%m-%d"),
                            "dn": dt_plantao.strftime("%d/%m/%Y"),
                            "h": hosp_plantao,
                            "s": setor_plantao,
                            "ce": agora_s,
                        },
                    )
                    # Gera também um slot de paciente para coleta naquele setor
                    run_sql(
                        """INSERT INTO hpb_agenda_cirurgica
                           (id_hpb_gerado, data_cirurgia, dia_semana_mapa, sala_horario, hospital, setor_coleta,
                            coletor_escalado, data_hora_candidatura_coletor, ultima_atualizacao_coletor_em,
                            status_escala, criado_por, criado_em)
                           VALUES (:id_g, :dt, :dia, 'Setor HC', :h, :setor, :u, :dh, :dh, 'ESCALADO_SETOR', :u, :dh)""",
                        {
                            "id_g": id_novo_setor,
                            "dt": dt_plantao.strftime("%Y-%m-%d"),
                            "dia": dt_plantao.strftime("%d/%m"),
                            "h": hosp_plantao,
                            "setor": setor_plantao,
                            "u": st.session_state.username,
                            "dh": agora_s,
                        },
                    )
                    st.rerun()

        if st.session_state.get("mostrar_seletor_novas_coletas") and tem_vagas_acesas:
            with st.container(border=True):
                st.markdown("#### Pacientes Disponíveis para Assumir Responsabilidade de Coleta")
                opcoes_assumir = {
                    v[0]: f"ID: {v[1] or f'AG-{v[0]:02d}HPB'} | Hospital: {v[2]} | Setor: {v[3]} | Dia/Hora: {v[4]} ({v[5]})"
                    for v in vagas_disponiveis_coleta
                }
                ag_assumir = st.selectbox(
                    "Escolha a coleta disponível:",
                    list(opcoes_assumir.keys()),
                    format_func=lambda k: opcoes_assumir[k],
                    key="sel_assumir_coleta_vaga",
                )
                if st.button("Confirmar Responsabilidade por esta Coleta", type="primary", key="btn_conf_resp_coleta"):
                    agora_cand = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    id_ger = gerar_proximo_id_hpb()
                    run_sql(
                        """UPDATE hpb_agenda_cirurgica
                           SET coletor_escalado = :u,
                               id_hpb_gerado = COALESCE(id_hpb_gerado, :idg),
                               data_hora_candidatura_coletor = :dh,
                               ultima_atualizacao_coletor_em = :dh,
                               status_escala = 'ESCALADO_VOLUNTARIO'
                           WHERE id_agenda = :id""",
                        {"u": st.session_state.username, "idg": id_ger, "dh": agora_cand, "id": ag_assumir},
                    )
                    st.session_state["mostrar_seletor_novas_coletas"] = False
                    log_audit("Coletor Assumiu Paciente", f"AGENDA-{ag_assumir}", f"Coletor {st.session_state.username} assumiu a coleta às {agora_cand}.")
                    st.success("Você agora é responsável por esta coleta! Selecione o paciente abaixo para abrir o Menu Lateral.")
                    st.rerun()

        # Lista de pacientes sob responsabilidade do Coletador que AINDA NÃO foram travados/confirmados
        meus_pacientes_ag = fetch_all("""
            SELECT id_agenda, COALESCE(id_hpb_gerado, 'AG-' || id_agenda || 'HPB'), hospital, setor_coleta,
                   dia_semana_mapa, sala_horario, nome_paciente, prontuario, diagnostico_mapa, sitio_sugerido
            FROM hpb_agenda_cirurgica
            WHERE coletor_escalado = :u
            ORDER BY id_agenda DESC
        """, {"u": st.session_state.username})

        # Filtra pacientes cuja coleta ainda não foi confirmada/travada pelo coletor
        pacientes_ativos_coletor = []
        for p_ag in meus_pacientes_ag:
            id_ag_p, id_h_p, hosp_p, setor_p, dia_p, sala_p, nm_oculto, pr_oculto, diag_oculto, sit_p = p_ag
            row_col_ex = fetch_one(
                "SELECT coletor_confirmou_travou, doc_tcle_ok, doc_ficha_clinica_ok, doc_fera_ok FROM hpb_coletas_ifera WHERE id_hpb = :id",
                {"id": id_h_p},
            )
            ja_travado = bool(row_col_ex[0] == 1) if row_col_ex else False
            if not ja_travado:
                pacientes_ativos_coletor.append({
                    "id_agenda": id_ag_p,
                    "id_hpb": id_h_p,
                    "hospital": hosp_p or "HC",
                    "setor": setor_p or "Bloco Cirúrgico",
                    "dia_sala": f"{dia_p or ''} {sala_p or ''}".strip(),
                    "sitio": sit_p if sit_p in SITIOS_HPB else "P",
                    "nome_interno": nm_oculto or "",
                    "prontuario_interno": pr_oculto or "",
                    "diag_interno": diag_oculto or "",
                    "tcle_ok": bool(row_col_ex[1]) if row_col_ex else False,
                    "ficha_ok": bool(row_col_ex[2]) if row_col_ex else False,
                    "fera_ok": bool(row_col_ex[3]) if row_col_ex else False,
                })

        if not pacientes_ativos_coletor:
            st.info(
                "Você não possui coletas pendentes de preenchimento no momento (ou todas as suas coletas já foram revisadas, confirmadas e enviadas para o Processador e Controle de Qualidade)."
            )
        else:
            st.divider()
            opcoes_pac_ativo = {
                p["id_hpb"]: f"ID: {p['id_hpb']} | Hospital: {p['hospital']} | Setor: {p['setor']} ({p['dia_sala']})"
                for p in pacientes_ativos_coletor
            }
            id_pac_trabalho = st.selectbox(
                "Selecione o Paciente sob sua responsabilidade para trabalhar:",
                list(opcoes_pac_ativo.keys()),
                format_func=lambda k: opcoes_pac_ativo[k],
                key="sel_pac_trabalho_coletor",
            )
            pac_atual = [p for p in pacientes_ativos_coletor if p["id_hpb"] == id_pac_trabalho][0]

            # MENU LATERAL EXCLUSIVO DO PACIENTE SELECIONADO PELO COLETADOR
            st.sidebar.markdown(f"### Paciente Ativo: `{id_pac_trabalho}`")
            st.sidebar.caption(f"Hospital: **{pac_atual['hospital']}** | Setor: **{pac_atual['setor']}**")

            etapa_lateral_coletor = st.sidebar.radio(
                "Menu do Paciente (Coletador):",
                [
                    "1. Anexação de Documentos (FERA, Ficha Clínica e TCLE)",
                    "2. Preenchimento do iFERA",
                    "3. Preenchimento da Ficha Clínica",
                    "4. Revisão Final e Confirmação (Liberar ao Processador)",
                ],
                key=f"menu_lat_col_{id_pac_trabalho}",
            )

            # Verifica no banco ou na sessão se os PDFs de TCLE e Ficha Clínica já foram anexados
            row_db_atu = fetch_one(
                """SELECT doc_tcle_ok, drive_url_tcle, doc_ficha_clinica_ok, drive_url_ficha_clinica,
                          doc_fera_ok, drive_url_fera
                   FROM hpb_coletas_ifera WHERE id_hpb = :id""",
                {"id": id_pac_trabalho},
            )
            tcle_ja_anexado = bool(
                (row_db_atu and row_db_atu[0] == 1)
                or st.session_state.get(f"tcle_ok_{id_pac_trabalho}", False)
            )
            ficha_ja_anexada = bool(
                (row_db_atu and row_db_atu[2] == 1)
                or st.session_state.get(f"ficha_ok_{id_pac_trabalho}", False)
            )
            fera_ja_iniciado = bool(
                (row_db_atu and row_db_atu[4] == 1)
                or st.session_state.get(f"fera_ok_{id_pac_trabalho}", False)
            )

            st.sidebar.markdown("---")
            st.sidebar.markdown("**Checklist Obrigatório:**")
            st.sidebar.markdown(f"- TCLE Anexado: `{'SIM' if tcle_ja_anexado else 'PENDENTE'}`")
            st.sidebar.markdown(f"- Ficha Clínica Anexada: `{'SIM' if ficha_ja_anexada else 'PENDENTE'}`")
            st.sidebar.markdown(f"- iFERA Preenchido: `{'SIM' if fera_ja_iniciado else 'PENDENTE'}`")

            # -----------------------------------------------------------------
            # SUB-TELA 1 DO COLETADOR: ANEXAÇÃO DE DOCUMENTOS (AUTOPREENCHIMENTO IMEDIATO)
            # -----------------------------------------------------------------
            if etapa_lateral_coletor.startswith("1."):
                st.markdown(f"#### 1. Anexação de Documentos Digitalizados — Paciente `{id_pac_trabalho}`")
                st.info(
                    "**Regra Operacional:** Anexe o **TCLE** e a **Ficha Clínica** (e opcionalmente o **FERA** digitalizado). "
                    "O sistema realiza a leitura e autopreenchimento imediato do iFERA e da Ficha Clínica. "
                    "O salvamento parcial do iFERA só é habilitado após anexar o TCLE e a Ficha Clínica."
                )

                c_dl_t = st.columns([3, 1])
                c_dl_t[1].download_button(
                    label="Baixar PDF Modelo Preenchido (Teste)",
                    data=gerar_pdf_preenchido_exemplo_hpb(id_pac_trabalho),
                    file_name=f"Dossie_Teste_{id_pac_trabalho}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key=f"btn_dl_teste_{id_pac_trabalho}",
                )

                u1, u2, u3 = st.columns(3)
                up_tcle = u1.file_uploader("1. Anexar PDF do TCLE *", type=["pdf"], key=f"up_tcle_{id_pac_trabalho}")
                up_ficha = u2.file_uploader("2. Anexar PDF da Ficha Clínica *", type=["pdf"], key=f"up_ficha_{id_pac_trabalho}")
                up_fera = u3.file_uploader("3. Anexar PDF do FERA (Opcional)", type=["pdf"], key=f"up_fera_{id_pac_trabalho}")

                if st.button("Salvar Documentos Anexados e Executar Autopreenchimento Imediato", type="primary", key=f"btn_salvar_docs_{id_pac_trabalho}"):
                    agora_doc = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ext_combinado = {}

                    if up_tcle is not None:
                        u_tcle_path = salvar_pdf_pasta_drive_sincronizada(id_pac_trabalho, "TCLE", up_tcle)
                        st.session_state[f"tcle_ok_{id_pac_trabalho}"] = True
                        st.session_state[f"url_tcle_{id_pac_trabalho}"] = u_tcle_path
                        ext_combinado.update(extrair_todos_dados_pdf_universal(up_tcle))

                    if up_ficha is not None:
                        u_fc_path = salvar_pdf_pasta_drive_sincronizada(id_pac_trabalho, "FICHA_CLINICA", up_ficha)
                        st.session_state[f"ficha_ok_{id_pac_trabalho}"] = True
                        st.session_state[f"url_ficha_{id_pac_trabalho}"] = u_fc_path
                        ext_combinado.update(extrair_todos_dados_pdf_universal(up_ficha))

                    if up_fera is not None:
                        u_fera_path = salvar_pdf_pasta_drive_sincronizada(id_pac_trabalho, "FERA_RP10", up_fera)
                        st.session_state[f"url_fera_{id_pac_trabalho}"] = u_fera_path
                        ext_combinado.update(extrair_todos_dados_pdf_universal(up_fera))

                    if ext_combinado:
                        injetar_dados_pdf_no_session_state(ext_combinado, {"hospital": pac_atual["hospital"]})
                        st.session_state[f"ext_json_{id_pac_trabalho}"] = ext_combinado

                    # Cria ou atualiza o rascunho no banco marcando os documentos anexados e o timestamp do fluxo
                    if not fetch_one("SELECT id_hpb FROM hpb_coletas_ifera WHERE id_hpb = :id", {"id": id_pac_trabalho}):
                        run_sql(
                            """INSERT INTO hpb_coletas_ifera
                               (id_hpb, id_agenda, hospital, setor_hospital_coleta, sitio_hpb,
                                responsavel_coleta, doc_tcle_ok, drive_url_tcle, doc_ficha_clinica_ok,
                                drive_url_ficha_clinica, ultima_atualizacao_fluxo_em, status_operacional)
                               VALUES (:id, :idag, :h, :s, :sit, :u, :tcle, :utcle, :fc, :ufc, :dh, 'DOCUMENTOS_ANEXADOS')""",
                            {
                                "id": id_pac_trabalho,
                                "idag": pac_atual["id_agenda"],
                                "h": pac_atual["hospital"],
                                "s": pac_atual["setor"],
                                "sit": pac_atual["sitio"],
                                "u": st.session_state.username,
                                "tcle": 1 if (up_tcle or tcle_ja_anexado) else 0,
                                "utcle": st.session_state.get(f"url_tcle_{id_pac_trabalho}", ""),
                                "fc": 1 if (up_ficha or ficha_ja_anexada) else 0,
                                "ufc": st.session_state.get(f"url_ficha_{id_pac_trabalho}", ""),
                                "dh": agora_doc,
                            },
                        )
                    else:
                        run_sql(
                            """UPDATE hpb_coletas_ifera
                               SET doc_tcle_ok = CASE WHEN :tcle = 1 THEN 1 ELSE doc_tcle_ok END,
                                   drive_url_tcle = CASE WHEN :utcle != '' THEN :utcle ELSE drive_url_tcle END,
                                   doc_ficha_clinica_ok = CASE WHEN :fc = 1 THEN 1 ELSE doc_ficha_clinica_ok END,
                                   drive_url_ficha_clinica = CASE WHEN :ufc != '' THEN :ufc ELSE drive_url_ficha_clinica END,
                                   ultima_atualizacao_fluxo_em = :dh
                               WHERE id_hpb = :id""",
                            {
                                "tcle": 1 if up_tcle else 0,
                                "utcle": st.session_state.get(f"url_tcle_{id_pac_trabalho}", ""),
                                "fc": 1 if up_ficha else 0,
                                "ufc": st.session_state.get(f"url_ficha_{id_pac_trabalho}", ""),
                                "dh": agora_doc,
                                "id": id_pac_trabalho,
                            },
                        )
                    run_sql(
                        "UPDATE hpb_agenda_cirurgica SET ultima_atualizacao_coletor_em = :dh WHERE id_agenda = :idag",
                        {"dh": agora_doc, "idag": pac_atual["id_agenda"]},
                    )
                    st.success("Documentos indexados e campos do iFERA / Ficha Clínica autopreenchidos!")
                    st.rerun()

            # -----------------------------------------------------------------
            # SUB-TELA 2 DO COLETADOR: PREENCHIMENTO DO iFERA (TRAVA SE FALTAR TCLE/FICHA)
            # -----------------------------------------------------------------
            elif etapa_lateral_coletor.startswith("2."):
                st.markdown(f"#### 2. Preenchimento do iFERA (RP10 — Etapa de Coleta) — `{id_pac_trabalho}`")
                if not (tcle_ja_anexado and ficha_ja_anexada):
                    st.error(
                        "⚠️ TRAVA DOCUMENTAL ATIVA: Você só pode salvar ou dar continuidade ao preenchimento parcial do iFERA "
                        "depois que os documentos de **Ficha Clínica** e de **TCLE** estiverem anexados na aba '1. Anexação de Documentos'."
                    )

                with st.form(f"form_ifera_col_{id_pac_trabalho}"):
                    c1, c2, c3, c4 = st.columns(4)
                    mod_mat = c1.selectbox(
                        "Modalidade da Coleta",
                        MODALIDADES_COLETA_MATERIAL,
                        index=1 if pac_atual["setor"] in ["Ambulatório de Oncologia", "Unidade de Diagnóstico por Imagem"] else (2 if pac_atual["setor"] == "Laboratório e Patologia" else 0),
                        key=f"if_mod_{id_pac_trabalho}",
                    )
                    interv_sel = c2.selectbox(
                        "Intervenção (RP10)",
                        ["(1) Ressecção Cirúrgica - RC", "(2) Antes da Quimioterapia - AQ", "(3) Depois da Quimioterapia - DQ/DP"],
                        key=f"if_intv_{id_pac_trabalho}",
                    )
                    ciclo_q = c3.text_input("Ciclo Quimio (se AQ/DQ)", value="00", key=f"if_cic_{id_pac_trabalho}")
                    tempo_cir = c4.text_input(
                        "Obtido há quanto tempo da cirurgia?",
                        value="No ato cirúrgico (Dia 0)",
                        key=f"if_tcir_{id_pac_trabalho}",
                    )

                    s1, s2 = st.columns(2)
                    with s1:
                        st.markdown("**Coleta de Sangue (RP10 Q1):**")
                        sg_ok = st.checkbox("Sangue coletado?", value=("Apenas Tumor" not in mod_mat), key=f"if_sg_{id_pac_trabalho}")
                        tb_h1 = st.checkbox("Heparina Tubo 1", value=True, key=f"if_h1_{id_pac_trabalho}")
                        tb_h2 = st.checkbox("Heparina Tubo 2", value=True, key=f"if_h2_{id_pac_trabalho}")
                        tb_s1 = st.checkbox("SST Soro Tubo 1", value=True, key=f"if_s1_{id_pac_trabalho}")
                        tb_s2 = st.checkbox("SST Soro Tubo 2", value=True, key=f"if_s2_{id_pac_trabalho}")
                        tb_ed = st.checkbox("EDTA Tubo 1", value=True, key=f"if_ed_{id_pac_trabalho}")
                        vol_sg = st.number_input("Volume de Sangue (mL)", min_value=0.0, value=float(st.session_state.get("f_vol_sangue", 26.0)), key=f"if_vsg_{id_pac_trabalho}")
                        car_sg = st.selectbox("Característica do Sangue", ["(1) Normal", "(2) Lipêmica", "(3) Hemolisada", "(4) Presença de fibrina"], key=f"if_csg_{id_pac_trabalho}")

                    with s2:
                        st.markdown("**Coleta de Tumor / Bloco de Parafina (RP10 Q2):**")
                        tm_ok = st.checkbox("Tumor / Biópsia coletada?", value=("Apenas Sangue" not in mod_mat), key=f"if_tm_{id_pac_trabalho}")
                        bl_ok = st.checkbox("Bloco de Parafina (Setor de Patologia)", value=(pac_atual["setor"] == "Laboratório e Patologia"), key=f"if_bl_{id_pac_trabalho}")
                        cod_bl = st.text_input("Código do Bloco de Parafina", value="", key=f"if_cbl_{id_pac_trabalho}")
                        tm_r1 = st.checkbox("RNA latter (eppendorf) Tubo 1", value=True, key=f"if_r1_{id_pac_trabalho}")
                        tm_tr1 = st.checkbox("Solução de transporte Tubo 1", value=True, key=f"if_tr1_{id_pac_trabalho}")
                        peso_tm = st.number_input("Peso / Quantificação Tumoral (mg)", min_value=0.0, value=float(st.session_state.get("f_peso_tumor", 35.0)), key=f"if_ptm_{id_pac_trabalho}")

                    t1, t2, t3, t4 = st.columns(4)
                    dt_col = t1.date_input("Data da Coleta", value=date.today(), key=f"if_dt_{id_pac_trabalho}")
                    hr_col = t2.text_input("Hora Exata da Coleta (HH:MM)", value=st.session_state.get("f_hr_coleta", datetime.now().strftime("%H:%M")), key=f"if_hr_{id_pac_trabalho}")
                    temp_atu = t3.number_input("Temp. Armazenamento/Transporte (°C)", value=4.0, step=0.5, key=f"if_tmp_{id_pac_trabalho}")
                    hr_cheg = t4.text_input("Hora de Entrega no Laboratório", value=datetime.now().strftime("%H:%M"), key=f"if_hch_{id_pac_trabalho}")

                    btn_salvar_parcial_ifera = st.form_submit_button(
                        "Salvar Preenchimento Parcial do iFERA",
                        type="primary",
                        disabled=not (tcle_ja_anexado and ficha_ja_anexada),
                    )
                    if btn_salvar_parcial_ifera and tcle_ja_anexado and ficha_ja_anexada:
                        agora_if = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        cod_int_f = formatar_codigo_intervencao(interv_sel, ciclo_q)
                        tot_al = int(tb_h1) + int(tb_h2) + int(tb_s1) + int(tb_s2) + int(tb_ed) + int(tm_r1) + int(tm_tr1)
                        run_sql(
                            """UPDATE hpb_coletas_ifera
                               SET modalidade_coleta_material = :mm,
                                   tipo_intervencao_rp10 = :tir,
                                   ciclo_quimio_dq = :cq,
                                   codigo_intervencao_final = :cif,
                                   tempo_relativo_cirurgia_desc = :tcir,
                                   sangue_coletado = :sg,
                                   tubo_heparina_1 = :h1, tubo_heparina_2 = :h2,
                                   tubo_sst_soro_1 = :s1, tubo_sst_soro_2 = :s2, tubo_edta_1 = :ed,
                                   vol_sangue_total_coletado_ml = :vsg,
                                   caracteristica_sangue = :csg,
                                   tumor_coletado = :tm,
                                   bloco_parafina_obtido = :blok,
                                   codigo_bloco_parafina = :cbl,
                                   tumor_rnalater_epp1 = :r1,
                                   tumor_sol_transp_epp1 = :tr1,
                                   peso_tumor_coletado_mg = :ptm,
                                   total_aliquotas_coletadas = :tot,
                                   data_coleta = :dtc,
                                   hora_coleta = :hrc,
                                   data_hora_indexacao_app = COALESCE(data_hora_indexacao_app, :dh),
                                   temp_armaz_atual = :tmp,
                                   hora_chegada = :hch,
                                   doc_fera_ok = 1,
                                   ultima_atualizacao_fluxo_em = :dh,
                                   status_operacional = 'IFERA_PARCIAL_SALVO'
                               WHERE id_hpb = :id""",
                            {
                                "mm": mod_mat, "tir": interv_sel, "cq": ciclo_q, "cif": cod_int_f,
                                "tcir": tempo_cir, "sg": int(sg_ok), "h1": int(tb_h1), "h2": int(tb_h2),
                                "s1": int(tb_s1), "s2": int(tb_s2), "ed": int(tb_ed), "vsg": vol_sg,
                                "csg": car_sg, "tm": int(tm_ok), "blok": int(bl_ok), "cbl": cod_bl,
                                "r1": int(tm_r1), "tr1": int(tm_tr1), "ptm": peso_tm, "tot": tot_al,
                                "dtc": dt_col.strftime("%Y-%m-%d"), "hrc": hr_col, "dh": agora_if,
                                "tmp": temp_atu, "hch": hr_cheg, "id": id_pac_trabalho,
                            },
                        )
                        run_sql(
                            "UPDATE hpb_agenda_cirurgica SET ultima_atualizacao_coletor_em = :dh WHERE id_agenda = :idag",
                            {"dh": agora_if, "idag": pac_atual["id_agenda"]},
                        )
                        st.session_state[f"fera_ok_{id_pac_trabalho}"] = True
                        st.success("iFERA salvo com sucesso! Avance para a Ficha Clínica ou para a Revisão Final no menu lateral.")
                        st.rerun()

            # -----------------------------------------------------------------
            # SUB-TELA 3 DO COLETADOR: PREENCHIMENTO DA FICHA CLÍNICA
            # -----------------------------------------------------------------
            elif etapa_lateral_coletor.startswith("3."):
                st.markdown(f"#### 3. Preenchimento da Ficha Clínica — `{id_pac_trabalho}`")
                with st.form(f"form_fc_col_{id_pac_trabalho}"):
                    fc1, fc2, fc3, fc4 = st.columns(4)
                    fc_nome = fc1.text_input("Nome do Paciente (Extraído do PDF)", value=st.session_state.get("f_nome_pac", pac_atual["nome_interno"]), key=f"fc_nm_{id_pac_trabalho}")
                    fc_pront = fc2.text_input("Nº Prontuário", value=st.session_state.get("f_prontuario", pac_atual["prontuario_interno"]), key=f"fc_pr_{id_pac_trabalho}")
                    fc_sexo = fc3.selectbox("Sexo", ["Fem", "Masc"], key=f"fc_sx_{id_pac_trabalho}")
                    fc_nasc = fc4.text_input("Data de Nascimento", value=st.session_state.get("f_fc_nasc", ""), key=f"fc_dn_{id_pac_trabalho}")

                    fc5, fc6, fc7, fc8 = st.columns(4)
                    fc_imc = fc5.number_input("IMC (kg/m²)", value=float(st.session_state.get("f_fc_imc", 24.0)), step=0.1, key=f"fc_imc_{id_pac_trabalho}")
                    fc_ca199 = fc6.text_input("CA 19.9", value=st.session_state.get("f_fc_ca199", ""), key=f"fc_c19_{id_pac_trabalho}")
                    fc_ca125 = fc7.text_input("CA 125", value=st.session_state.get("f_fc_ca125", ""), key=f"fc_c125_{id_pac_trabalho}")
                    fc_ctnm = fc8.text_input("cTNM / Estádio", value=st.session_state.get("f_fc_ctnm", ""), key=f"fc_ctnm_{id_pac_trabalho}")

                    fc9, fc10, fc11 = st.columns(3)
                    fc_lesao = fc9.text_input("Hipótese Diagnóstica / Descrição da Lesão", value=st.session_state.get("f_hip_diag", pac_atual["diag_interno"]), key=f"fc_les_{id_pac_trabalho}")
                    fc_cir = fc10.selectbox("Tipo de Cirurgia / Procedimento", ["Duodenopancreatectomia cefálica", "Pancreatectomia corpo-caudal", "Hepatectomia", "Biliar / Colecistectomia", "Biópsia / Quimioterapia"], key=f"fc_cir_{id_pac_trabalho}")
                    fc_adj = fc11.selectbox("Esquema Terapêutico", ["Folfirinox", "Folfox", "Genzar", "Não aplicável"], key=f"fc_adj_{id_pac_trabalho}")

                    if st.form_submit_button("Salvar Dados da Ficha Clínica", type="primary"):
                        agora_fc = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        run_sql("DELETE FROM hpb_ficha_clinica WHERE id_hpb = :id", {"id": id_pac_trabalho})
                        run_sql(
                            """INSERT INTO hpb_ficha_clinica
                               (id_hpb, centro_hospital, prontuario, nome_paciente, sexo, data_nascimento,
                                imc, ca19_9, ca125, descricao_lesao, ctnm, tipo_cirurgia, tto_adjuvante_esquema,
                                atualizado_por, atualizado_em)
                               VALUES (:id, :ch, :pr, :nm, :sx, :dn, :imc, :c19, :c125, :les, :ctnm, :cir, :adj, :u, :dh)""",
                            {
                                "id": id_pac_trabalho, "ch": pac_atual["hospital"], "pr": fc_pront,
                                "nm": fc_nome, "sx": fc_sexo, "dn": fc_nasc, "imc": fc_imc,
                                "c19": fc_ca199, "c125": fc_ca125, "les": fc_lesao, "ctnm": fc_ctnm,
                                "cir": fc_cir, "adj": fc_adj, "u": st.session_state.username, "dh": agora_fc,
                            },
                        )
                        run_sql(
                            """UPDATE hpb_coletas_ifera
                               SET nome_paciente = :nm, prontuario = :pr, hipotese_diagnostica = :les,
                                   planilha_dados_clinicos_ok = 1, ultima_atualizacao_fluxo_em = :dh
                               WHERE id_hpb = :id""",
                            {"nm": fc_nome, "pr": fc_pront, "les": fc_lesao, "dh": agora_fc, "id": id_pac_trabalho},
                        )
                        st.success("Ficha Clínica salva!")
                        st.rerun()

            # -----------------------------------------------------------------
            # SUB-TELA 4 DO COLETADOR: REVISÃO FINAL, BLOQUEIO E ENVIO AO PROCESSADOR
            # -----------------------------------------------------------------
            elif etapa_lateral_coletor.startswith("4."):
                st.markdown(f"#### 4. Revisão Final do Coletador e Liberação para o Processador — `{id_pac_trabalho}`")
                st.warning(
                    "**Atenção:** Revise cuidadosamente os dados do iFERA e da Ficha Clínica abaixo. "
                    "Assim que você confirmar, **você não terá mais acesso à visualização/edição deste paciente** "
                    "(a segunda checagem passará para o **Controle de Qualidade**) e o **Processador receberá o e-mail de liberação imediata**."
                )

                pronto_para_confirmar = bool(tcle_ja_anexado and ficha_ja_anexada and fera_ja_iniciado)
                if not pronto_para_confirmar:
                    st.error("Complete os 3 itens do Checklist no menu lateral (TCLE Anexado + Ficha Clínica Anexada + iFERA Preenchido) antes de confirmar.")
                else:
                    check_rev_col = st.checkbox(
                        "Revisei todos os dados digitados e convertidos dos PDFs (iFERA, Ficha Clínica e TCLE) e confirmo o envio definitivo.",
                        key=f"chk_rev_final_col_{id_pac_trabalho}",
                    )
                    if st.button(
                        "Confirmar Revisão Final, Travar Minha Etapa e Notificar Processador por E-mail",
                        type="primary",
                        disabled=(not check_rev_col),
                        key=f"btn_travar_col_{id_pac_trabalho}",
                    ):
                        agora_fim_col = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        run_sql(
                            """UPDATE hpb_coletas_ifera
                               SET coletor_confirmou_travou = 1,
                                   data_hora_confirmacao_coletor = :dh,
                                   dados_pdf_confirmados_ok = 1,
                                   confirmado_pdf_por = :u,
                                   data_hora_confirmacao_pdf = :dh,
                                   liberado_para_processador = 1,
                                   data_hora_liberacao = :dh,
                                   ultima_atualizacao_fluxo_em = :dh,
                                   status_operacional = 'LIBERADO_PARA_PROCESSADOR'
                               WHERE id_hpb = :id""",
                            {"dh": agora_fim_col, "u": st.session_state.username, "id": id_pac_trabalho},
                        )
                        emails_proc = [
                            x[0] for x in fetch_all("SELECT email FROM hpb_usuarios WHERE role = 'Processador' AND ativo = 1")
                        ]
                        disparar_email_hpb(
                            emails_proc,
                            f"[REDE HPB] Amostras Liberadas para Processamento: {id_pac_trabalho} ({pac_atual['hospital']})",
                            f"Prezados Processadores,\n\nO Coletador {st.session_state.nome_usuario} concluiu e revisou toda a documentação "
                            f"(TCLE, Ficha Clínica e iFERA) do paciente {id_pac_trabalho} ({pac_atual['hospital']} - {pac_atual['setor']}).\n"
                            f"As amostras já estão desbloqueadas na sua Grade de Agenda Semanal para iniciar o processamento.",
                        )
                        log_audit(
                            "Coletor Confirmou e Travou Etapa",
                            id_pac_trabalho,
                            f"Coletador {st.session_state.username} confirmou revisão final e liberou amostras ao Processador.",
                        )
                        st.success(f"Etapa de coleta de `{id_pac_trabalho}` concluída e bloqueada! E-mail enviado ao Processador.")
                        st.rerun()
# FIM DA PARTE 2 DE 4
# =========================================================================
    # INTERFACE 2: PROCESSADOR (AGENDA SEMANAL, LINHA DO TEMPO DO COLETOR,
    #              CONTINUAÇÃO DO iFERA, ORGANOIDES [LAURIANO/JULIA] E BLOQUEIO)
    # =========================================================================
    elif modulo_ativo.startswith("Interface do Processador"):
        st.subheader("Grade de Agenda Semanal — Meus Pacientes Escalados para Processamento")
        st.caption(
            "Visão Minimalista do Processador: Exibe exclusivamente a ID, o Hospital de Origem e a situação das amostras e do processamento."
        )

        renderizar_grade_calendario_semanal_operador(st.session_state.username, modo="Processador")

        # Lista todos os pacientes em fluxo (na agenda ou já iniciados pelo coletor) que ainda não foram travados pelo processador
        todos_status = obter_status_cromatico_e_termometros_todos_pacientes()
        pacientes_visiveis_proc = []
        for item_p in todos_status:
            id_h_ref = item_p["id_hpb"]
            if id_h_ref:
                r_tr = fetch_one(
                    "SELECT processador_confirmou_travou, status_operacional FROM hpb_coletas_ifera WHERE id_hpb = :id",
                    {"id": id_h_ref},
                )
                if r_tr and int(r_tr[0] or 0) == 1:
                    continue  # Já confirmado e travado pelo processador (foi para o Controle de Qualidade)
            pacientes_visiveis_proc.append(item_p)

        st.divider()
        if not pacientes_visiveis_proc:
            st.info(
                "Não há pacientes pendentes de processamento no momento (ou todos os seus processamentos já foram revisados, confirmados e enviados para o Controle de Qualidade)."
            )
        else:
            opcoes_proc_sel = {
                p["id_display"]: (
                    f"ID: {p['id_display']} | Hospital: {p['hospital']} | Setor: {p['setor']} | "
                    f"Coleta: {p['termometro_coleta']}% • Proc: {p['termometro_processamento']}%"
                )
                for p in pacientes_visiveis_proc
            }
            id_disp_escolhido = st.selectbox(
                "Clique / Selecione o Paciente na Agenda para abrir a Linha do Tempo do Coletador e o Processamento:",
                list(opcoes_proc_sel.keys()),
                format_func=lambda k: opcoes_proc_sel[k],
                key="sel_pac_agenda_processador",
            )
            pac_proc_obj = [p for p in pacientes_visiveis_proc if p["id_display"] == id_disp_escolhido][0]
            id_hpb_proc = pac_proc_obj["id_hpb"] or pac_proc_obj["id_display"]
            id_ag_proc = pac_proc_obj["id_agenda"]

            # -----------------------------------------------------------------
            # LINHA DO TEMPO DO COLETADOR (EXIBIDA AO CLICAR NO PACIENTE)
            # -----------------------------------------------------------------
            tl_info = obter_linha_tempo_coletor_para_processador(id_hpb_proc, id_ag_proc)

            st.markdown(f"#### Linha do Tempo da Coleta — Paciente `{id_hpb_proc}` ({pac_proc_obj['hospital']})")
            m_tl1, m_tl2, m_tl3, m_tl4 = st.columns(4)
            m_tl1.metric("Quem é o Coletador", tl_info["coletor_exibicao"])
            m_tl2.metric("Candidatura à Coleta (Dia/Hora)", tl_info["quando_se_candidatou"])
            m_tl3.metric("Tempo sem Logar na Plataforma", tl_info["tempo_sem_logar"], f"Último login: {tl_info['ultimo_login_em']}")
            m_tl4.metric("Tempo sem Atualizar o Paciente", tl_info["tempo_sem_atualizar_paciente"], f"Última ação: {tl_info['ultima_atualizacao_fluxo']}")

            d_c1, d_c2, d_c3, d_c4 = st.columns(4)
            for idx_d, (rot_d, ok_d) in enumerate([
                ("1. PDF do TCLE Anexado", tl_info["tcle_anexado"]),
                ("2. PDF da Ficha Clínica Anexado", tl_info["ficha_clinica_anexada"]),
                ("3. iFERA (Coleta) Preenchido", tl_info["ifera_preenchido"]),
                ("4. Coletador Revisou e Confirmou", tl_info["coletor_revisou_e_confirmou"]),
            ]):
                bg_d = "#064e3b" if ok_d else "#450a0a"
                bd_d = "#10b981" if ok_d else "#ef4444"
                tx_d = "OK / Concluído" if ok_d else "Pendente pelo Coletador"
                [d_c1, d_c2, d_c3, d_c4][idx_d].markdown(
                    f"<div style='background:{bg_d}; border:1px solid {bd_d}; padding:10px; border-radius:8px; text-align:center; color:#f8fafc;'>"
                    f"<div style='font-size:12px; font-weight:700;'>{rot_d}</div>"
                    f"<div style='font-size:13px; margin-top:4px;'>{tx_d}</div></div>",
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)

            # Botão só fica ACESO quando 100% da atividade do Coletador estiver concluída
            coleta_100_ok = tl_info["liberado_para_iniciar_processamento"]
            if st.button(
                f"🔓 Iniciar / Continuar Atividade de Processamento de {id_hpb_proc}" if coleta_100_ok else f"🔒 Aguardando Conclusão da Atividade do Coletador para {id_hpb_proc}",
                type="primary" if coleta_100_ok else "secondary",
                disabled=(not coleta_100_ok),
                use_container_width=True,
                key=f"btn_iniciar_proc_aceso_{id_hpb_proc}",
            ):
                st.session_state["paciente_aberto_bancada_proc"] = id_hpb_proc
                run_sql(
                    "UPDATE hpb_coletas_ifera SET processador_designado = COALESCE(processador_designado, :u) WHERE id_hpb = :id",
                    {"u": st.session_state.username, "id": id_hpb_proc},
                )

            # -----------------------------------------------------------------
            # ÁREA DE TRABALHO DO PROCESSADOR (DESBLOQUEADA APÓS CLICAR NO BOTÃO ACESO)
            # -----------------------------------------------------------------
            if coleta_100_ok and st.session_state.get("paciente_aberto_bancada_proc") == id_hpb_proc:
                dados_c_proc = fetch_one(
                    """SELECT id_anterior, hospital, sitio_hpb, codigo_intervencao_final,
                              data_coleta, modalidade_coleta_material, status_operacional,
                              peso_tumor_coletado_mg, vol_sangue_total_coletado_ml
                       FROM hpb_coletas_ifera WHERE id_hpb = :id""",
                    {"id": id_hpb_proc},
                )
                id_ant_p = dados_c_proc[0] if dados_c_proc else "-"
                hosp_p = dados_c_proc[1] if dados_c_proc else "HC"
                sitio_p = dados_c_proc[2] if dados_c_proc else "P"
                interv_p = dados_c_proc[3] if dados_c_proc else "RC00"
                dt_col_str = dados_c_proc[4] if dados_c_proc else date.today().strftime("%Y-%m-%d")
                mod_mat_p = dados_c_proc[5] if dados_c_proc else "Sangue + Tumor"
                st_op_atual = dados_c_proc[6] if dados_c_proc else ""
                peso_tum_col = float(dados_c_proc[7] or 30.0) if dados_c_proc else 30.0

                st.divider()
                st.markdown(f"### Bancada de Processamento — ID `{id_hpb_proc}` | Hospital `{hosp_p}` | Sítio `{sitio_p}`")

                abas_bancada = [
                    "1. Continuação do iFERA (Seções IV a VII), Volumetria e Caixas 10x10",
                    "2. Extração Molecular TRIZOL (Página13) e Mapa Visual 10x10",
                    "3. Obtenção de Organoides (Subatividade Exclusiva: Lauriano & Julia Braga)",
                    "4. Revisão Final e Confirmação do Processador (Enviar ao Controle de Qualidade)",
                ]
                t_b1, t_b2, t_b3, t_b4 = st.tabs(abas_bancada)

                with t_b1:
                    somente_sangue_p = "Apenas Sangue" in str(mod_mat_p)
                    somente_tumor_p = "Apenas Tumor" in str(mod_mat_p)

                    with st.form(f"form_proc_ifera_cont_{id_hpb_proc}"):
                        st.markdown("#### IV e V — Recebimento, Centrifugação e Dissociação Tumoral (RP10 Q20 a Q34)")
                        r1, r2, r3, r4 = st.columns(4)
                        loc_rec = r1.text_input("20. Local do recebimento", value="Laboratório LINAT / Rede HPB", key=f"pr_loc_{id_hpb_proc}")
                        dt_rec = r2.date_input("21. Data do recebimento", value=date.today(), key=f"pr_dt_{id_hpb_proc}")
                        hr_rec = r3.text_input("22. Hora do recebimento (HH:MM)", value=datetime.now().strftime("%H:%M"), key=f"pr_hr_{id_hpb_proc}")
                        resp_rec = r4.text_input("23. Responsável pelo recebimento", value=st.session_state.username, key=f"pr_rr_{id_hpb_proc}")

                        c1, c2, c3, c4 = st.columns(4)
                        hr_ini_c = c1.text_input("25. Início da centrifugação (HH:MM)", value="10:00", key=f"pr_hic_{id_hpb_proc}")
                        hr_fim_c = c2.text_input("26. Fim da centrifugação (HH:MM)", value="10:15", key=f"pr_hfc_{id_hpb_proc}")
                        cent_ok = c3.selectbox("27. Centrifugada no tempo determinado?", ["(1) Sim", "(0) Não"], key=f"pr_cok_{id_hpb_proc}")
                        suf_tum = c4.selectbox(
                            "28. Amostra tumoral suficiente para dissociação e PCR?",
                            ["(1) Sim", "(2) Apenas um", "(0) Não"],
                            key=f"pr_suf_{id_hpb_proc}",
                        )

                        d1, d2, d3, d4 = st.columns(4)
                        dt_ini_diss = d1.text_input("29. Data/Hora início dissociação", value=datetime.now().strftime("%d/%m/%Y %H:%M"), key=f"pr_ddis_{id_hpb_proc}")
                        diss_ok = d2.selectbox("29.1. Dissociada nos parâmetros?", ["(1) Sim", "(0) Não"], key=f"pr_dok_{id_hpb_proc}")
                        dt_ini_rna = d3.text_input("30. Data/Hora início RNA latter", value=datetime.now().strftime("%d/%m/%Y %H:%M"), key=f"pr_drna_{id_hpb_proc}")
                        num_garr = d4.text_input("32.1. Nº garrafas/placas em cultivo", value="1 garrafa / 1 placa", key=f"pr_ngp_{id_hpb_proc}")

                        st.markdown("#### VI e VII — Aliquotagem, Volumetria e Rastreabilidade Espacial nas Criocaixas 10x10")
                        entradas_biobanco = {}
                        cols_b = st.columns(7)
                        vols_padrao = {1: 1.5, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.0, 6: 1.5, 7: 1.5}
                        for cod_b, (nome_b, rotulo_planilha) in BIOTIPOS_REDE_HPB.items():
                            with cols_b[cod_b - 1]:
                                st.markdown(f"**{rotulo_planilha}**")
                                bloq_b = (cod_b == 5 and somente_sangue_p) or (cod_b != 5 and somente_tumor_p)
                                if bloq_b:
                                    st.caption("Não aplicável")
                                    qtd_al, vol_al = 0, 0.0
                                else:
                                    qtd_al = st.number_input("Alíquotas", min_value=0, max_value=20, value=2, key=f"pr_q_{cod_b}_{id_hpb_proc}")
                                    vol_al = st.number_input("Vol (mL)", min_value=0.0, value=vols_padrao[cod_b], step=0.5, key=f"pr_v_{cod_b}_{id_hpb_proc}")
                                dens_b = st.text_input("Densidade PBMC", value="5 X 10^6", key=f"pr_dpbmc_{id_hpb_proc}") if cod_b == 3 else ""
                                entradas_biobanco[cod_b] = (nome_b, qtd_al, vol_al, dens_b)

                        cg1, cg2, cg3 = st.columns(3)
                        dt_cong = cg1.date_input("39. Data do congelamento", value=date.today(), key=f"pr_dtcg_{id_hpb_proc}")
                        hr_cong = cg2.text_input("40. Hora do congelamento (HH:MM)", value=datetime.now().strftime("%H:%M"), key=f"pr_hrcg_{id_hpb_proc}")
                        obs_proc = cg3.text_input("43. Observações do Processamento (RP10)", key=f"pr_obs_{id_hpb_proc}")

                        if st.form_submit_button("Salvar Processamento no iFERA, Endereçar Criocaixas 10x10 e Gerar Etiquetas", type="primary"):
                            try:
                                dt_col_fmt = datetime.strptime(dt_col_str, "%Y-%m-%d").strftime("%d/%m/%Y")
                            except Exception:
                                dt_col_fmt = date.today().strftime("%d/%m/%Y")

                            run_sql("DELETE FROM hpb_biobanco_aliquotas WHERE id_hpb = :id", {"id": id_hpb_proc})
                            etiquetas_geradas = []

                            for cod_b, (nome_b, qtd_al, vol_al, dens_b) in entradas_biobanco.items():
                                if int(qtd_al) > 0:
                                    cod_etq = f"{id_hpb_proc}{cod_b}{interv_p}"
                                    etq_dupla = f"{cod_etq}\n{dt_col_fmt}"
                                    cx, rk, cols_l, lin_n = sugerir_proxima_posicao_caixa_10x10(sitio_p or "P", cod_b, int(qtd_al))
                                    vol_tot = round(int(qtd_al) * float(vol_al), 2) if cod_b != 5 else 0.0
                                else:
                                    cod_etq, etq_dupla, cx, rk, cols_l, lin_n, vol_tot = "AA", "AA", "0", "0.0", "0", 0, 0.0

                                run_sql(
                                    """INSERT INTO hpb_biobanco_aliquotas (
                                        id_anterior, id_hpb, sitio_hpb, intervencao, data_coleta,
                                        cod_biotipo, amostra_nome, codigo_etiqueta, etiqueta_dupla_linha,
                                        amostra_no_150, densidade_celular, n_aliquotas_entrada, vol_aliquota_ml,
                                        n_aliquotas_biobanco, vol_total_biobanco_ml, caixa, rack, coluna, linha,
                                        processador_username, data_hora_processamento
                                    ) VALUES (
                                        :ida, :id, :sit, :intv, :dtc, :cb, :an, :ce, :ed,
                                        :a150, :dens, :nae, :val, :nab, :vtb, :cx, :rk, :col, :lin, :pu, :dhp
                                    )""",
                                    {
                                        "ida": id_ant_p or "-", "id": id_hpb_proc, "sit": sitio_p or "P",
                                        "intv": interv_p or "RC00", "dtc": dt_col_str, "cb": cod_b, "an": nome_b,
                                        "ce": cod_etq, "ed": etq_dupla,
                                        "a150": "Sim (-150°C)" if cod_b == 3 and int(qtd_al) > 0 else "",
                                        "dens": dens_b, "nae": int(qtd_al), "val": float(vol_al),
                                        "nab": int(qtd_al), "vtb": vol_tot, "cx": cx, "rk": rk,
                                        "col": cols_l, "lin": lin_n, "pu": st.session_state.username,
                                        "dhp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    },
                                )
                                for _ in range(int(qtd_al)):
                                    etiquetas_geradas.append({
                                        "codigo": cod_etq,
                                        "data": dt_col_fmt,
                                        "biotipo": f"{nome_b} ({vol_al}mL)" if cod_b != 3 else f"PBMC {dens_b}",
                                        "posicao": f"{cx} L{lin_n}:{cols_l}",
                                        "hospital": hosp_p or "HC",
                                    })

                            agora_p = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            run_sql(
                                """UPDATE hpb_coletas_ifera
                                   SET local_recebimento = :lr, data_recebimento = :dr, hora_recebimento = :hr,
                                       responsavel_recebimento = :rr, responsavel_centrifugacao = :rr,
                                       hora_ini_centrifugacao = :hic, hora_fim_centrifugacao = :hfc,
                                       centrifugada_tempo_ok = :cok, amostra_suficiente_dissoc_pcr = :suf,
                                       data_ini_dissociacao = :ddis, dissociada_parametros_ok = :dok,
                                       data_ini_extracao_rna = :drna, num_garrafas_placas = :ngp,
                                       data_congelamento = :dcg, hora_congelamento = :hcg,
                                       responsavel_armazenamento_final = :rr, observacoes_rp10 = :obs,
                                       planilha_biobanco_ok = 1, ultima_atualizacao_fluxo_em = :dh,
                                       status_operacional = 'PROCESSADO_E_ARMAZENADO'
                                   WHERE id_hpb = :id""",
                                {
                                    "lr": loc_rec, "dr": dt_rec.strftime("%Y-%m-%d"), "hr": hr_rec,
                                    "rr": resp_rec, "hic": hr_ini_c, "hfc": hr_fim_c,
                                    "cok": 1 if "Sim" in cent_ok else 0, "suf": suf_tum,
                                    "ddis": dt_ini_diss, "dok": 1 if "Sim" in diss_ok else 0,
                                    "drna": dt_ini_rna, "ngp": num_garr,
                                    "dcg": dt_cong.strftime("%Y-%m-%d"), "hcg": hr_cong,
                                    "obs": obs_proc, "dh": agora_p, "id": id_hpb_proc,
                                },
                            )
                            st.session_state[f"etq_pdf_{id_hpb_proc}"] = gerar_pdf_etiquetas_prontas_impressao(etiquetas_geradas)
                            st.success("Processamento salvo! O caminho para Obtenção de Organoides (Aba 3) e Revisão Final (Aba 4) está liberado.")
                            st.rerun()

                    if st.session_state.get(f"etq_pdf_{id_hpb_proc}"):
                        st.download_button(
                            label=f"🖨️ Baixar Folha de Etiquetas Prontas para Impressão ({id_hpb_proc}) [PDF]",
                            data=st.session_state[f"etq_pdf_{id_hpb_proc}"],
                            file_name=f"Etiquetas_{id_hpb_proc}.pdf",
                            mime="application/pdf",
                            type="primary",
                            key=f"dl_etq_{id_hpb_proc}",
                        )

                with t_b2:
                    st.markdown("#### Extração Molecular de RNA por TRIZOL (Aba `Página13`)")
                    p1, p2, p3, p4 = st.columns(4)
                    peso_b = p1.number_input("Peso da Biópsia (mg)", min_value=0.0, value=peso_tum_col, step=1.0, key=f"p13_w_{id_hpb_proc}")
                    trizol_u = peso_b * 14.0
                    p2.metric("Volume TRIZOL (Peso × 14)", f"{trizol_u:.0f} uL")
                    conc_r = p3.number_input("Concentração (ng/uL)", min_value=0.0, value=920.0, step=10.0, key=f"p13_c_{id_hpb_proc}")
                    raz_r = p4.number_input("Razão A260/A280", min_value=0.0, value=2.04, step=0.01, key=f"p13_r_{id_hpb_proc}")
                    if st.button("Salvar Extração TRIZOL", key=f"btn_p13_{id_hpb_proc}"):
                        run_sql("DELETE FROM hpb_extracao_rna_pagina13 WHERE id_hpb = :id", {"id": id_hpb_proc})
                        run_sql(
                            "INSERT INTO hpb_extracao_rna_pagina13 VALUES (:id, :p, :t, :c, :r, '30uL', :u, :dt)",
                            {"id": id_hpb_proc, "p": peso_b, "t": trizol_u, "c": conc_r, "r": raz_r, "u": st.session_state.username, "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                        )
                        st.success("Extração TRIZOL registrada!")

                    st.divider()
                    cx_disp = listar_caixas_criogenicas_disponiveis()
                    cx_escolhida = st.selectbox("Inspecionar Ocupação na Criocaixa 10x10:", cx_disp, key=f"cx_view_{id_hpb_proc}")
                    renderizar_visualizador_grade_10x10_criocaixa(cx_escolhida)

                # -------------------------------------------------------------
                # SUBATIVIDADE EXCLUSIVA DE OBTENÇÃO DE ORGANOIDES
                # (RESTRITA INICIALMENTE A JOSÉ LAURIANO E JULIA BRAGA)
                # -------------------------------------------------------------
                with t_b3:
                    st.markdown(f"#### 3. Subatividade de Obtenção de Organoides — Paciente `{id_hpb_proc}`")
                    if st_op_atual != "PROCESSADO_E_ARMAZENADO":
                        st.warning(
                            "🔒 Trava de Sequência: Conclua e salve primeiro a Aba 1 (Processamento Biológico e Acondicionamento das Amostras no iFERA) para liberar o registro de obtenção de organoides."
                        )
                    elif not usuario_autorizado_organoides():
                        st.error(
                            "🔒 Acesso Restrito: Nesta fase da Rede HPB, a subatividade de **Obtenção de Organoides** está habilitada exclusivamente para os processadores **José Lauriano (`jose.lauriano`)** e **Julia Braga (`julia.pereira`)**."
                        )
                    else:
                        st.success(
                            f"Operador Credenciado para Organoides (`{st.session_state.nome_usuario}`): Indique abaixo o que foi realizado no processamento tumoral para obtenção e estabelecimento do organoide."
                        )
                        with st.form(f"form_organoide_exclusivo_{id_hpb_proc}"):
                            or1, or2, or3 = st.columns(3)
                            tipo_cult_org = or1.selectbox("Linhagem Derivada", ["Organoide (PDO)", "CAF (Fibroblasto Associado)"], key=f"org_t_{id_hpb_proc}")
                            prot_dissoc = or2.selectbox(
                                "Protocolo Aplicado na Obtenção Tumoral",
                                [
                                    "Dissociação Enzimática (Colagenase IV + Dispase) + Domo de Matrigel",
                                    "Dissociação Mecânica/Enzimática + Geltrex + Meio Completo (Wnt3a/R-Spondina/Noggin)",
                                    "Resgate Celular com Suplementação de R-Spondina",
                                    "Explante Direto para Isolamento de CAFs",
                                ],
                                key=f"org_prot_{id_hpb_proc}",
                            )
                            pass_org = or3.text_input("Passagem Inicial / Atual", value="P0 (Estabelecimento Primário)", key=f"org_p_{id_hpb_proc}")

                            or4, or5, or6, or7 = st.columns(4)
                            n_amp_org = or4.number_input("Nº de Domos / Poços / Ampolas", min_value=1, value=4, key=f"org_na_{id_hpb_proc}")
                            dens_org = or5.text_input("Densidade Celular Semeada", value="4 x 10^5", key=f"org_d_{id_hpb_proc}")
                            pos_150_org = or6.text_input("Localização (Incubadora CO2 37°C / -150°C Rack)", value="Incubadora 37°C 5% CO2 / Rack 1", key=f"org_pos_{id_hpb_proc}")
                            obs_org_det = or7.text_input("Descrição Detalhada do Que Foi Feito para o Organoide", value="Fragmentos tumorais digeridos, filtrados em 100um e plaqueados em domos 3D.", key=f"org_obs_{id_hpb_proc}")

                            if st.form_submit_button("Registrar Obtenção do Organoide no Dossiê do Paciente", type="primary"):
                                run_sql(
                                    """INSERT INTO hpb_cultivos_organoides_cafs
                                       (id_hpb, tipo_linhagem, protocolo_obtencao_organoide, n_ampolas, densidade_celular,
                                        passagem, data_congelamento, posicao_150c, rack, responsavel, obs)
                                       VALUES (:id, :tl, :pr, :na, :dc, :ps, :dt, :pos, 'Rack 1', :r, :ob)""",
                                    {
                                        "id": id_hpb_proc,
                                        "tl": "Organoide" if "Organoide" in tipo_cult_org else "CAF",
                                        "pr": prot_dissoc,
                                        "na": int(n_amp_org),
                                        "dc": dens_org,
                                        "ps": pass_org,
                                        "dt": date.today().strftime("%Y-%m-%d"),
                                        "pos": pos_150_org,
                                        "r": st.session_state.username,
                                        "ob": obs_org_det,
                                    },
                                )
                                run_sql(
                                    """UPDATE hpb_coletas_ifera
                                       SET acao_organoide_realizada = :ac,
                                           responsavel_organoide = :ro,
                                           ultima_atualizacao_fluxo_em = :dh
                                       WHERE id_hpb = :id""",
                                    {
                                        "ac": f"{prot_dissoc} ({n_amp_org} domos/ampolas - {pass_org}): {obs_org_det}",
                                        "ro": st.session_state.username,
                                        "dh": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "id": id_hpb_proc,
                                    },
                                )
                                log_audit("Obtenção de Organoide", id_hpb_proc, f"{st.session_state.username} registrou obtenção de organoide: {prot_dissoc}.")
                                st.success(f"Protocolo de obtenção de organoide registrado para `{id_hpb_proc}`!")

                # -------------------------------------------------------------
                # REVISÃO FINAL E BLOQUEIO DO PROCESSADOR -> CONTROLE DE QUALIDADE
                # -------------------------------------------------------------
                with t_b4:
                    st.markdown(f"#### 4. Revisão Final e Confirmação do Processador — `{id_hpb_proc}`")
                    st.warning(
                        "**Atenção:** Revise todos os dados de processamento, acondicionamento e volumetria registrados no iFERA. "
                        "Assim que confirmar, **você não terá mais acesso à visualização/edição deste paciente** e o registro "
                        "seguirá para a **segunda checagem no Controle de Qualidade**."
                    )
                    if st_op_atual != "PROCESSADO_E_ARMAZENADO":
                        st.error("Salve primeiro o processamento na Aba 1 antes de confirmar a revisão final.")
                    else:
                        chk_proc_fim = st.checkbox(
                            "Confirmo que revisei todos os registros de processamento, volumetria e rastreabilidade do iFERA.",
                            key=f"chk_fim_proc_{id_hpb_proc}",
                        )
                        if st.button(
                            "Confirmar Revisão do Processamento e Enviar para o Controle de Qualidade",
                            type="primary",
                            disabled=(not chk_proc_fim),
                            key=f"btn_travar_proc_{id_hpb_proc}",
                        ):
                            agora_fp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            run_sql(
                                """UPDATE hpb_coletas_ifera
                                   SET processador_confirmou_travou = 1,
                                       data_hora_confirmacao_proc = :dh,
                                       ultima_atualizacao_fluxo_em = :dh
                                   WHERE id_hpb = :id""",
                                {"dh": agora_fp, "id": id_hpb_proc},
                            )
                            st.session_state.pop("paciente_aberto_bancada_proc", None)
                            log_audit(
                                "Processador Confirmou e Travou Etapa",
                                id_hpb_proc,
                                f"Processador {st.session_state.username} confirmou revisão final. Enviado ao Controle de Qualidade.",
                            )
                            st.success(f"Processamento de `{id_hpb_proc}` finalizado e encaminhado ao Controle de Qualidade!")
                            st.rerun()

    # =========================================================================
    # INTERFACE 3: CONTROLE DE QUALIDADE (2ª CHECAGEM, AGILIDADE, VIABILIDADE,
    #              ACESSO A NOMES/PRONTUÁRIOS/PDFs E RELATÓRIO DE ERROS PARA PI)
    # =========================================================================
    elif modulo_ativo.startswith("Controle de Qualidade"):
        st.header("Controle de Qualidade — 2ª Checagem, Parâmetros de Viabilidade, Scores de Agilidade e Auditoria Documental")
        st.caption(
            "Atribuição Exclusiva de Qualidade: Acesso completo a Nomes, Prontuários, PDFs digitalizados e dados digitados. "
            "Todas as correções realizadas nesta segunda checagem registram automaticamente o erro e o tipo de erro para os Pesquisadores Responsáveis (Moacyr e Valécia)."
        )

        df_cq = fetch_df("""
            SELECT id_hpb, hospital, setor_hospital_coleta, nome_paciente, prontuario,
                   responsavel_coleta, data_coleta, hora_coleta, data_hora_indexacao_app,
                   coletor_confirmou_travou, responsavel_recebimento, data_recebimento,
                   hora_recebimento, processador_confirmou_travou, doc_tcle_ok, doc_ficha_clinica_ok,
                   doc_fera_ok, temp_armaz_atual, caracteristica_sangue, centrifugada_tempo_ok,
                   score_agilidade_coletor, score_agilidade_proc, score_viabilidade_amostra,
                   cq_homologado, status_operacional
            FROM hpb_coletas_ifera
            ORDER BY data_hora_indexacao_app DESC
        """)

        if df_cq.empty:
            st.info("Nenhum registro disponível para checagem do Controle de Qualidade.")
        else:
            st.dataframe(df_cq, use_container_width=True, hide_index=True)

            id_cq_sel = st.selectbox(
                "Selecione o Paciente (ID HPB) para Realizar a 2ª Checagem, Auditar PDFs e Avaliar Viabilidade/Agilidade:",
                df_cq["id_hpb"].tolist(),
                key="sel_pac_cq_inspecao",
            )
            r_cq = fetch_one(
                """SELECT nome_paciente, prontuario, hospital, setor_hospital_coleta,
                          responsavel_coleta, data_coleta, hora_coleta, data_hora_indexacao_app,
                          responsavel_recebimento, data_recebimento, hora_recebimento,
                          temp_armaz_atual, caracteristica_sangue, centrifugada_tempo_ok,
                          vol_sangue_total_coletado_ml, peso_tumor_coletado_mg,
                          drive_url_tcle, drive_url_ficha_clinica, drive_url_fera,
                          hipotese_diagnostica, laudo_histopatologico, cq_homologado
                   FROM hpb_coletas_ifera WHERE id_hpb = :id""",
                {"id": id_cq_sel},
            )

            (
                nm_cq, pr_cq, hosp_cq, set_cq, col_cq, dt_c_cq, hr_c_cq, dh_idx_cq,
                proc_cq, dt_r_cq, hr_r_cq, tmp_cq, car_sg_cq, cent_ok_cq,
                v_sg_cq, p_tm_cq, u_tcle_cq, u_fc_cq, u_fera_cq, hip_cq, laudo_cq, homol_cq
            ) = r_cq

            # Cálculo automático dedutível dos Scores de Agilidade e Viabilidade
            _, horas_atraso_idx = calcular_diferenca_tempo_legivel(dh_idx_cq)
            score_agil_col_calc = max(20.0, min(100.0, 100.0 - max(0.0, (horas_atraso_idx if horas_atraso_idx < 100 else 1.0) - 2.0) * 4.0))
            score_viab_calc = 100.0
            if tmp_cq is not None and (float(tmp_cq) > 8.0 or float(tmp_cq) < 0.0):
                score_viab_calc -= 25.0
            if car_sg_cq and ("Hemolisada" in str(car_sg_cq) or "fibrina" in str(car_sg_cq)):
                score_viab_calc -= 20.0
            if not int(centrifugada_tempo_ok := (cent_ok_cq if cent_ok_cq is not None else 1)):
                score_viab_calc -= 15.0

            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("Paciente / Prontuário (Visível ao CQ)", f"{nm_cq or 'Sem Nome'}", f"Prontuário: {pr_cq or '-'}")
            sc2.metric("Score de Viabilidade da Amostra", f"{score_viab_calc:.1f}%", f"Temp: {tmp_cq}°C | {car_sg_cq}")
            sc3.metric("Coletador Responsável", col_cq or "-", f"Coleta: {dt_c_cq} {hr_c_cq}")
            sc4.metric("Processador Responsável", proc_cq or "-", f"Receb.: {dt_r_cq or '-'} {hr_r_cq or '-'}")

            st.markdown("#### 1. Checagem Obrigatória: Dados Digitados + PDFs Anexados")
            b_pdf1, b_pdf2, b_pdf3 = st.columns(3)
            for idx_b, (titulo_b, path_b) in enumerate([
                ("PDF do TCLE", u_tcle_cq),
                ("PDF da Ficha Clínica", u_fc_cq),
                ("PDF do FERA (RP10)", u_fera_cq),
            ]):
                col_b = [b_pdf1, b_pdf2, b_pdf3][idx_b]
                if path_b and os.path.exists(path_b):
                    with open(path_b, "rb") as f_b:
                        col_b.download_button(
                            label=f"📄 Abrir / Baixar {titulo_b}",
                            data=f_b.read(),
                            file_name=os.path.basename(path_b),
                            mime="application/pdf",
                            use_container_width=True,
                            key=f"cq_dl_{idx_b}_{id_cq_sel}",
                        )
                else:
                    col_b.error(f"{titulo_b}: Arquivo PDF não localizado!")

            st.markdown("#### 2. Segunda Checagem, Correção de Divergências e Registro de Erro para Moacyr e Valécia")
            with st.form(f"form_cq_revisao_{id_cq_sel}"):
                f_q1, f_q2, f_q3, f_q4 = st.columns(4)
                novo_nm_cq = f_q1.text_input("Nome do Paciente (Revisado pelo CQ)", value=nm_cq or "", key=f"cq_nm_{id_cq_sel}")
                novo_pr_cq = f_q2.text_input("Nº Prontuário (Revisado pelo CQ)", value=pr_cq or "", key=f"cq_pr_{id_cq_sel}")
                novo_vsg_cq = f_q3.number_input("Vol. Sangue (mL)", min_value=0.0, value=float(v_sg_cq or 0.0), key=f"cq_vsg_{id_cq_sel}")
                novo_ptm_cq = f_q4.number_input("Peso Tumor (mg)", min_value=0.0, value=float(p_tm_cq or 0.0), key=f"cq_ptm_{id_cq_sel}")

                f_q5, f_q6, f_q7, f_q8 = st.columns(4)
                nova_hip_cq = f_q5.text_input("Hipótese Diagnóstica", value=hip_cq or "", key=f"cq_hip_{id_cq_sel}")
                novo_laudo_cq = f_q6.text_input("Laudo Histopatológico", value=laudo_cq or "", key=f"cq_ld_{id_cq_sel}")
                sc_agil_col_in = f_q7.number_input("Score Agilidade Coletador (0–100)", min_value=0.0, max_value=100.0, value=float(score_agil_col_calc), key=f"cq_sac_{id_cq_sel}")
                sc_agil_proc_in = f_q8.number_input("Score Agilidade Processador (0–100)", min_value=0.0, max_value=100.0, value=95.0, key=f"cq_sap_{id_cq_sel}")

                st.markdown("**Se houve erro do Coletador ou do Processador, classifique abaixo (Será exibido no painel de Moacyr e Valécia):**")
                e_c1, e_c2, e_c3, e_c4 = st.columns(4)
                houve_erro_cq = e_c1.checkbox("Registrar Erro / Divergência Detectada na 2ª Checagem", key=f"cq_err_chk_{id_cq_sel}")
                etapa_erro_cq = e_c2.selectbox("Etapa de Origem do Erro", ["Coleta", "Processamento"], key=f"cq_err_et_{id_cq_sel}")
                tipo_erro_cq = e_c3.selectbox(
                    "Tipo de Erro Detectado",
                    [
                        "Erro de Transcrição de PDF (Ficha Clínica / TCLE / FERA)",
                        "Atraso na Indexação ou Atualização do Fluxo",
                        "Divergência de Volumetria / Número de Alíquotas",
                        "Desvio de Temperatura / Acondicionamento Pré-Analítico",
                        "Omissão de Campo Obrigatório na Ficha Clínica / iFERA",
                        "Documentação Digitalizada Incompleta ou Ilegível",
                    ],
                    key=f"cq_err_tp_{id_cq_sel}",
                )
                obs_erro_cq = e_c4.text_input("Detalhamento da Correção Realizada pelo CQ", key=f"cq_err_ob_{id_cq_sel}")

                if st.form_submit_button("Salvar 2ª Checagem do Controle de Qualidade, Atualizar Scores e Homologar", type="primary"):
                    agora_cq = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    alteracoes_detectadas = []
                    if (novo_nm_cq != (nm_cq or "")) or (novo_pr_cq != (pr_cq or "")):
                        alteracoes_detectadas.append(f"Identificação: '{nm_cq}/{pr_cq}' -> '{novo_nm_cq}/{novo_pr_cq}'")
                    if float(novo_vsg_cq) != float(v_sg_cq or 0.0) or float(novo_ptm_cq) != float(p_tm_cq or 0.0):
                        alteracoes_detectadas.append(f"Volumetria: '{v_sg_cq}mL/{p_tm_cq}mg' -> '{novo_vsg_cq}mL/{novo_ptm_cq}mg'")
                    if nova_hip_cq != (hip_cq or ""):
                        alteracoes_detectadas.append(f"Diagnóstico: '{hip_cq}' -> '{nova_hip_cq}'")

                    if houve_erro_cq or alteracoes_detectadas:
                        op_errado = (col_cq if etapa_erro_cq == "Coleta" else proc_cq) or "Operador não identificado"
                        resumo_ant = f"Nome={nm_cq} | Pront={pr_cq} | Vol={v_sg_cq}mL | Peso={p_tm_cq}mg | Diag={hip_cq}"
                        resumo_corr = f"Nome={novo_nm_cq} | Pront={novo_pr_cq} | Vol={novo_vsg_cq}mL | Peso={novo_ptm_cq}mg | Diag={nova_hip_cq}"
                        run_sql(
                            """INSERT INTO hpb_erros_revisao_cq
                               (id_hpb, etapa_origem, operador_responsavel, tipo_erro, campo_alterado,
                                valor_anterior, valor_corrigido, observacao_cq, avaliador_cq, data_hora)
                               VALUES (:id, :et, :op, :tp, :ca, :va, :vc, :ob, :av, :dh)""",
                            {
                                "id": id_cq_sel,
                                "et": etapa_erro_cq,
                                "op": op_errado,
                                "tp": tipo_erro_cq,
                                "ca": "; ".join(alteracoes_detectadas) if alteracoes_detectadas else "Apontamento Direto CQ",
                                "va": resumo_ant,
                                "vc": resumo_corr,
                                "ob": obs_erro_cq,
                                "av": st.session_state.username,
                                "dh": agora_cq,
                            },
                        )

                    run_sql(
                        """UPDATE hpb_coletas_ifera
                           SET nome_paciente = :nm, prontuario = :pr,
                               vol_sangue_total_coletado_ml = :vsg, peso_tumor_coletado_mg = :ptm,
                               hipotese_diagnostica = :hip, laudo_histopatologico = :ld,
                               score_agilidade_coletor = :sac, score_agilidade_proc = :sap,
                               score_viabilidade_amostra = :svi,
                               cq_homologado = 1, cq_homologado_por = :u, data_hora_homologacao_cq = :dh
                           WHERE id_hpb = :id""",
                        {
                            "nm": novo_nm_cq, "pr": novo_pr_cq, "vsg": novo_vsg_cq, "ptm": novo_ptm_cq,
                            "hip": nova_hip_cq, "ld": novo_laudo_cq, "sac": sc_agil_col_in,
                            "sap": sc_agil_proc_in, "svi": score_viab_calc,
                            "u": st.session_state.username, "dh": agora_cq, "id": id_cq_sel,
                        },
                    )
                    log_audit("2ª Checagem Controle de Qualidade", id_cq_sel, f"Homologado por {st.session_state.username} (Erro registrado: {'SIM' if (houve_erro_cq or alteracoes_detectadas) else 'NÃO'}).")
                    st.success(f"Segunda checagem de `{id_cq_sel}` concluída! Alterações e tipos de erro já estão visíveis para Moacyr e Valécia.")
                    st.rerun()

            st.subheader("Histórico de Erros e Correções Apontados pelo Controle de Qualidade")
            df_err_view = fetch_df("SELECT * FROM hpb_erros_revisao_cq ORDER BY id_erro DESC")
            if not df_err_view.empty:
                st.dataframe(df_err_view, use_container_width=True, hide_index=True)
# FIM DA PARTE 3 DE 4
# =========================================================================
    # INTERFACE 4: PAINEL DOS PESQUISADORES RESPONSÁVEIS (MOACYR & VALÉCIA)
    #              LISTA CROMÁTICA (VERDE/AZUL/AMARELO/VERMELHO) + ÁREA GRÁFICA DO PACIENTE
    # =========================================================================
    elif modulo_ativo.startswith("Painel dos Pesquisadores"):
        if not (usuario_eh_moacyr() or usuario_pode_escalar()):
            st.error("Acesso restrito aos Pesquisadores Responsáveis (Prof. Dr. Moacyr e Valécia).")
        else:
            st.header("Painel dos Pesquisadores Responsáveis — Visão Cromática de Pacientes e Dossiê Gráfico")
            # --- CONTROLE EXCLUSIVO DA TRAVA DE DISPARO DE E-MAILS (MOACYR & VALÉCIA) ---
            email_ativo_atu, quem_alt_em, quando_alt_em = obter_status_trava_email()
            with st.container(border=True):
                c_tr1, c_tr2 = st.columns([3.5, 1.5])
                with c_tr1:
                    status_cor_em = "#10b981" if email_ativo_atu else "#ef4444"
                    status_txt_em = (
                        "ATIVO (Disparos de e-mail liberados)"
                        if email_ativo_atu
                        else "TRAVADO / DESATIVADO (Nenhum e-mail externo é disparado pelo sistema)"
                    )
                    st.markdown(
                        f"**Trava de Disparo Automático de E-mails (12h e Alertas):** "
                        f"<span style='color:{status_cor_em}; font-weight:800;'>{status_txt_em}</span><br>"
                        f"<span style='font-size:12px; color:#94a3b8;'>Última configuração por: <code>{quem_alt_em}</code> em <code>{quando_alt_em}</code></span>",
                        unsafe_allow_html=True,
                    )
                with c_tr2:
                    novo_estado_email = st.toggle(
                        "Ativar Disparo de E-mails",
                        value=email_ativo_atu,
                        key="toggle_exclusivo_email_moacyr_valecia",
                    )
                    if novo_estado_email != email_ativo_atu:
                        alterar_trava_email_moacyr_valecia(novo_estado_email)
                        st.rerun()
                        
            st.caption(
                "Legenda de Status: 🟢 VERDE = Finalizado (Coleta + Processamento 100%) | "
                "🔵 AZUL = Em Processamento | 🟡 AMARELO = Em Fase do Coletador | "
                "🔴 VERMELHO = Paciente mencionado na agenda sem Coletador escalado (permite escalação imediata sem aguardar 12h)."
            )

            # Botão expansível para lançar novas cirurgias/pacientes na agenda (PDF do Mapa Cirúrgico ou Manual)
            with st.expander("➕ Lançar Mapa Cirúrgico Semanal (PDF) ou Novo Paciente na Agenda (HC / HUOC / HCP)", expanded=False):
                c_ag1, c_ag2 = st.columns([1, 2])
                hosp_m_pi = c_ag1.selectbox("Hospital do Mapa", ["HC", "HUOC", "HCP", "HSE"], key="pi_hosp_mapa")
                pdf_m_pi = c_ag2.file_uploader("Upload do PDF do Mapa Cirúrgico", type=["pdf"], key="pi_up_mapa_pdf")
                if pdf_m_pi is not None:
                    df_ext_pi = extrair_mapa_cirurgico_pdf(pdf_m_pi, hosp_m_pi)
                    df_ed_pi = st.data_editor(df_ext_pi, use_container_width=True, hide_index=True, key="pi_ed_mapa")
                    if st.button("Publicar Pacientes Selecionados na Lista", type="primary", key="pi_btn_pub_mapa"):
                        selecionadas_pi = df_ed_pi[df_ed_pi["Incluir_na_Agenda_HPB"] == True]
                        agora_pi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for _, r_p in selecionadas_pi.iterrows():
                            id_novo_g = gerar_proximo_id_hpb()
                            run_sql(
                                """INSERT INTO hpb_agenda_cirurgica
                                   (id_hpb_gerado, data_cirurgia, dia_semana_mapa, sala_horario, hospital, setor_coleta,
                                    prontuario, nome_paciente, diagnostico_mapa, cirurgia_proposta, sitio_sugerido,
                                    status_escala, criado_por, criado_em)
                                   VALUES (:idg, :dt, :dia, :sh, :h, :setor, :pr, :nm, :dg, :cr, :st, 'AGUARDANDO_COLETOR', :cp, :ce)""",
                                {
                                    "idg": id_novo_g, "dt": date.today().strftime("%Y-%m-%d"),
                                    "dia": r_p["Dia_Mapa"], "sh": r_p["Sala_Horario"], "h": r_p["Hospital"],
                                    "setor": r_p.get("Setor", "Bloco Cirúrgico (Agendamento Cirúrgico)"),
                                    "pr": r_p["Prontuario"], "nm": r_p["Nome_Paciente"],
                                    "dg": r_p["Diagnostico"], "cr": r_p["Cirurgia_e_Cirurgiao"],
                                    "st": r_p["Sitio_HPB"], "cp": st.session_state.username, "ce": agora_pi,
                                },
                            )
                        st.success(f"{len(selecionadas_pi)} paciente(s) lançado(s) na lista!")
                        st.rerun()

                st.markdown("---")
                with st.form("form_pi_novo_pac_manual"):
                    m1, m2, m3, m4 = st.columns(4)
                    p_hosp = m1.selectbox("Hospital", ["HC", "HUOC", "HCP", "HSE"], key="pi_m_hosp")
                    p_setor = m2.selectbox("Setor", SETORES_COLETA_HC_HPB, key="pi_m_setor")
                    p_dt = m3.date_input("Data", value=date.today(), key="pi_m_dt")
                    p_sit = m4.selectbox("Sítio HPB", ["P", "H", "B", "PAPILA", "Ctl"], key="pi_m_sit")
                    m5, m6, m7 = st.columns(3)
                    p_nome = m5.text_input("Nome do Paciente", key="pi_m_nome")
                    p_pront = m6.text_input("Prontuário", key="pi_m_pront")
                    p_diag = m7.text_input("Suspeita / Procedimento", key="pi_m_diag")
                    if st.form_submit_button("Adicionar Paciente na Lista"):
                        if p_nome:
                            agora_m = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            run_sql(
                                """INSERT INTO hpb_agenda_cirurgica
                                   (id_hpb_gerado, data_cirurgia, dia_semana_mapa, sala_horario, hospital, setor_coleta,
                                    prontuario, nome_paciente, diagnostico_mapa, cirurgia_proposta, sitio_sugerido,
                                    status_escala, criado_por, criado_em)
                                   VALUES (:idg, :dt, :dia, '07H', :h, :setor, :pr, :nm, :dg, :dg, :st, 'AGUARDANDO_COLETOR', :cp, :ce)""",
                                {
                                    "idg": gerar_proximo_id_hpb(), "dt": p_dt.strftime("%Y-%m-%d"),
                                    "dia": p_dt.strftime("%d/%m"), "h": p_hosp, "setor": p_setor,
                                    "pr": p_pront, "nm": p_nome, "dg": p_diag, "st": p_sit,
                                    "cp": st.session_state.username, "ce": agora_m,
                                },
                            )
                            st.rerun()

            # -----------------------------------------------------------------
            # PAINEL INICIAL MINIMALISTA COM CÓDIGO DE CORES E TERMÔMETROS DUPLOS
            # (APENAS ID DO PACIENTE + TERMÔMETRO DE COLETA + TERMÔMETRO DE PROCESSAMENTO)
            # -----------------------------------------------------------------
            lista_pacientes_pi = obter_status_cromatico_e_termometros_todos_pacientes()

            if not lista_pacientes_pi:
                st.info("Nenhum paciente registrado na agenda ou no biobanco até o momento.")
            else:
                st.subheader("Quadro Geral de Pacientes (Clique no ID do Paciente para abrir a Área Gráfica do Paciente)")

                for idx_p, pac_item in enumerate(lista_pacientes_pi):
                    id_disp = pac_item["id_display"]
                    cor_c = pac_item["cor_hex"]
                    t_col = pac_item["termometro_coleta"]
                    t_proc = pac_item["termometro_processamento"]
                    status_lbl = pac_item["cor_status"]

                    c_row1, c_row2, c_row3, c_row4 = st.columns([2.2, 3.0, 3.0, 1.8])
                    with c_row1:
                        st.markdown(
                            f"<div style='background:#0f172a; border-left:6px solid {cor_c}; padding:10px 14px; border-radius:8px;'>"
                            f"<div style='color:{cor_c}; font-size:17px; font-weight:900; letter-spacing:0.5px;'>{id_disp}</div>"
                            f"<div style='color:{cor_c}; font-size:11px; font-weight:700;'>{status_lbl}</div></div>",
                            unsafe_allow_html=True,
                        )
                    with c_row2:
                        st.markdown(
                            f"<div style='background:#0f172a; padding:10px 14px; border-radius:8px; border:1px solid #1e293b;'>"
                            f"<div style='display:flex; justify-content:space-between; color:{cor_c}; font-size:12px; font-weight:700; margin-bottom:5px;'>"
                            f"<span>🌡️ Termômetro de Coleta</span><span>{t_col}%</span></div>"
                            f"<div style='width:100%; background:#1e293b; height:10px; border-radius:999px; overflow:hidden;'>"
                            f"<div style='width:{t_col}%; background:{cor_c}; height:100%;'></div></div></div>",
                            unsafe_allow_html=True,
                        )
                    with c_row3:
                        st.markdown(
                            f"<div style='background:#0f172a; padding:10px 14px; border-radius:8px; border:1px solid #1e293b;'>"
                            f"<div style='display:flex; justify-content:space-between; color:{cor_c}; font-size:12px; font-weight:700; margin-bottom:5px;'>"
                            f"<span>⚙️ Termômetro de Processamento</span><span>{t_proc}%</span></div>"
                            f"<div style='width:100%; background:#1e293b; height:10px; border-radius:999px; overflow:hidden;'>"
                            f"<div style='width:{t_proc}%; background:{cor_c}; height:100%;'></div></div></div>",
                            unsafe_allow_html=True,
                        )
                    with c_row4:
                        if st.button(
                            f"🔍 Abrir {id_disp}",
                            key=f"btn_pi_abrir_pac_{idx_p}_{id_disp}",
                            use_container_width=True,
                        ):
                            st.session_state["pi_paciente_selecionado"] = pac_item

                # -------------------------------------------------------------
                # ÁREA VISUAL E GRÁFICA DO PACIENTE SELECIONADO
                # -------------------------------------------------------------
                pac_sel_pi = st.session_state.get("pi_paciente_selecionado") or lista_pacientes_pi[0]
                id_sel_disp = pac_sel_pi["id_display"]
                id_sel_hpb = pac_sel_pi["id_hpb"] or id_sel_disp
                id_sel_ag = pac_sel_pi["id_agenda"]
                cor_sel = pac_sel_pi["cor_hex"]

                st.divider()
                st.markdown(
                    f"<div style='background:#0f172a; border:2px solid {cor_sel}; padding:16px 20px; border-radius:12px; margin-bottom:16px;'>"
                    f"<h3 style='color:{cor_sel}; margin:0;'>Área Visual do Paciente: {id_sel_disp} — {pac_sel_pi['nome_paciente']} (Prontuário: {pac_sel_pi['prontuario']})</h3>"
                    f"<p style='color:#cbd5e1; margin:4px 0 0 0; font-size:13.5px;'>"
                    f"<b>Hospital:</b> {pac_sel_pi['hospital']} | <b>Setor:</b> {pac_sel_pi['setor']} | <b>Status Cromático:</b> <span style='color:{cor_sel}; font-weight:700;'>{pac_sel_pi['cor_status']}</span></p></div>",
                    unsafe_allow_html=True,
                )

                # ESCALAÇÃO IMEDIATA SEM ESPERAR 12H (ESPECIALMENTE QUANDO VERMELHO OU SEM RESPONSÁVEL)
                with st.expander(
                    "⚡ Escalar / Alterar Imediatamente Coletador e Processador para este Paciente (Sem aguardar 12h)",
                    expanded=(cor_sel == "#ef4444"),
                ):
                    df_r_col = obter_ranking_participacao_coletores()
                    df_r_proc = obter_ranking_participacao_processadores()
                    e_c1, e_c2, e_c3 = st.columns([2, 2, 1.2])
                    col_esc_now = e_c1.selectbox(
                        "Selecionar Coletador (Com histórico de coletas e dias sem coletar):",
                        df_r_col["Username"].tolist() if not df_r_col.empty else ["claudio.cordeiro"],
                        format_func=lambda u: (
                            f"{df_r_col[df_r_col['Username']==u]['Coletor'].iloc[0]} "
                            f"({df_r_col[df_r_col['Username']==u]['Total de Coletas Realizadas'].iloc[0]} coletas | "
                            f"{df_r_col[df_r_col['Username']==u]['Dias sem Coletar'].iloc[0]}d inativo)"
                        ) if not df_r_col.empty else u,
                        key=f"pi_esc_col_imediato_{id_sel_disp}",
                    )
                    proc_esc_now = e_c2.selectbox(
                        "Selecionar Processador (Com histórico de processamentos e dias sem processar):",
                        df_r_proc["Username"].tolist() if not df_r_proc.empty else ["jose.lauriano"],
                        format_func=lambda u: (
                            f"{df_r_proc[df_r_proc['Username']==u]['Processador'].iloc[0]} "
                            f"({df_r_proc[df_r_proc['Username']==u]['Total de Processamentos Realizados'].iloc[0]} proc. | "
                            f"{df_r_proc[df_r_proc['Username']==u]['Dias sem Processar'].iloc[0]}d inativo)"
                        ) if not df_r_proc.empty else u,
                        key=f"pi_esc_proc_imediato_{id_sel_disp}",
                    )
                    if e_c3.button("Designar Equipe Agora", type="primary", key=f"btn_pi_designar_now_{id_sel_disp}"):
                        agora_des = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        if id_sel_ag:
                            run_sql(
                                """UPDATE hpb_agenda_cirurgica
                                   SET coletor_escalado = :c, indicado_por_moacyr = :c,
                                       data_hora_candidatura_coletor = COALESCE(data_hora_candidatura_coletor, :dh),
                                       processador_escalado = :p, processador_indicado_por = :aut,
                                       id_hpb_gerado = COALESCE(id_hpb_gerado, :idg),
                                       status_escala = 'DESIGNADO_PELO_PI'
                                   WHERE id_agenda = :idag""",
                                {
                                    "c": col_esc_now, "p": proc_esc_now, "dh": agora_des,
                                    "aut": st.session_state.username, "idg": id_sel_hpb, "idag": id_sel_ag,
                                },
                            )
                        if fetch_one("SELECT id_hpb FROM hpb_coletas_ifera WHERE id_hpb = :id", {"id": id_sel_hpb}):
                            run_sql(
                                """UPDATE hpb_coletas_ifera
                                   SET responsavel_coleta = :c, processador_designado = :p, eleito_processador_por = :aut
                                   WHERE id_hpb = :id""",
                                {"c": col_esc_now, "p": proc_esc_now, "aut": st.session_state.username, "id": id_sel_hpb},
                            )
                        log_audit(
                            "Designação Imediata PI/Coordenação",
                            id_sel_hpb,
                            f"{st.session_state.nome_usuario} escalou Coletor={col_esc_now} e Processador={proc_esc_now}.",
                        )
                        st.success(f"Coletador `{col_esc_now}` e Processador `{proc_esc_now}` designados para `{id_sel_disp}`!")
                        st.rerun()

                # Busca dados completos do paciente no banco para os painéis gráficos
                row_det_pi = fetch_one(
                    """SELECT responsavel_coleta, responsavel_armaz_pos_coleta, responsavel_transporte,
                              responsavel_recebimento, responsavel_centrifugacao, responsavel_armazenamento_final,
                              responsavel_organoide, cq_homologado_por,
                              data_coleta, hora_coleta, hora_saida, hora_chegada,
                              hora_recebimento, hora_ini_centrifugacao, hora_fim_centrifugacao,
                              hora_congelamento, data_hora_indexacao_app,
                              score_agilidade_coletor, score_agilidade_proc, score_viabilidade_amostra,
                              vol_sangue_total_coletado_ml, peso_tumor_coletado_mg,
                              acao_organoide_realizada, drive_url_tcle, drive_url_ficha_clinica, drive_url_fera
                       FROM hpb_coletas_ifera WHERE id_hpb = :id""",
                    {"id": id_sel_hpb},
                )

                # 1. CADEIA VISUAL DE RESPONSÁVEIS POR ÁREA
                st.markdown("#### 1. Cadeia de Responsáveis por Área Operacional")
                resp_cols = st.columns(5)
                resp_cards = [
                    ("Coleta In Loco", (row_det_pi[0] if row_det_pi and row_det_pi[0] else pac_sel_pi["coletor"]), "#38bdf8"),
                    ("Transporte / Receb.", (row_det_pi[3] if row_det_pi and row_det_pi[3] else pac_sel_pi["processador"]), "#a855f7"),
                    ("Processamento / Biobanco", (row_det_pi[5] if row_det_pi and row_det_pi[5] else pac_sel_pi["processador"]), "#10b981"),
                    ("Cultivo Organoide 3D", (row_det_pi[6] if row_det_pi and row_det_pi[6] else "Não acionado"), "#f59e0b"),
                    ("2ª Checagem (Qualidade)", (row_det_pi[7] if row_det_pi and row_det_pi[7] else "Pendente CQ"), "#ec4899"),
                ]
                for idx_rc, (area_lbl, nome_r, cor_r) in enumerate(resp_cards):
                    resp_cols[idx_rc].markdown(
                        f"<div style='background:#0f172a; border-top:4px solid {cor_r}; padding:10px; border-radius:8px; text-align:center;'>"
                        f"<div style='font-size:11px; color:#94a3b8; font-weight:700;'>{area_lbl}</div>"
                        f"<div style='font-size:14px; color:#f8fafc; font-weight:800; margin-top:4px;'>{nome_r}</div></div>",
                        unsafe_allow_html=True,
                    )

                # 2. GRÁFICO VISUAL DE TEMPOS OPERACIONAIS DO FERA E SCORES DE QUALIDADE
                st.markdown("#### 2. Cronometria Visual da Coleta ao Ultrafreezer (Tempos Relatados no FERA) & Scores")
                if not row_det_pi:
                    st.warning("O Coletador ainda não iniciou o preenchimento do iFERA para este paciente.")
                else:
                    def _calc_min_entre(h1: str, h2: str, padrao_m: int = 20) -> int:
                        try:
                            t1 = datetime.strptime(str(h1).strip(), "%H:%M")
                            t2 = datetime.strptime(str(h2).strip(), "%H:%M")
                            diff = int((t2 - t1).total_seconds() // 60)
                            return diff if diff > 0 else padrao_m
                        except Exception:
                            return padrao_m

                    min_transporte = _calc_min_entre(row_det_pi[10] or row_det_pi[9], row_det_pi[11], 25)
                    min_receb_bancada = _calc_min_entre(row_det_pi[11], row_det_pi[12], 15)
                    min_centrif = _calc_min_entre(row_det_pi[13], row_det_pi[14], 15)
                    min_ate_congelamento = _calc_min_entre(row_det_pi[12], row_det_pi[15], 45)

                    g_col1, g_col2 = st.columns([3, 2])
                    with g_col1:
                        etapas_tempo = [
                            ("1. Ato da Coleta (" + str(row_det_pi[9] or "08:00") + ")", 15, "#38bdf8"),
                            ("2. Transporte Hospital -> Lab (" + str(min_transporte) + " min)", min_transporte, "#60a5fa"),
                            ("3. Recebimento e Triagem (" + str(min_receb_bancada) + " min)", min_receb_bancada, "#818cf8"),
                            ("4. Centrifugação (" + str(min_centrif) + " min)", min_centrif, "#a855f7"),
                            ("5. Fracionamento até Congelamento (" + str(min_ate_congelamento) + " min)", min_ate_congelamento, "#10b981"),
                        ]
                        html_barras_t = ["<div style='background:#0f172a; padding:14px; border-radius:10px; border:1px solid #334155;'>"]
                        for rot_t, val_min, cor_b in etapas_tempo:
                            larg_pct = min(100, max(12, int((val_min / 90.0) * 100)))
                            html_barras_t.append(
                                f"<div style='margin-bottom:8px;'>"
                                f"<div style='display:flex; justify-content:space-between; font-size:12px; color:#e2e8f0; margin-bottom:2px;'>"
                                f"<span>{rot_t}</span><b>{val_min} min</b></div>"
                                f"<div style='width:100%; background:#1e293b; height:10px; border-radius:999px;'>"
                                f"<div style='width:{larg_pct}%; background:{cor_b}; height:100%; border-radius:999px;'></div></div></div>"
                            )
                        html_barras_t.append("</div>")
                        st.markdown("".join(html_barras_t), unsafe_allow_html=True)

                    with g_col2:
                        sc_ag_c = float(row_det_pi[17] or 100.0)
                        sc_ag_p = float(row_det_pi[18] or 100.0)
                        sc_viab = float(row_det_pi[19] or 100.0)
                        st.markdown(
                            f"""
                            <div style='background:#0f172a; padding:14px; border-radius:10px; border:1px solid #334155;'>
                                <div style='font-size:13px; font-weight:700; color:#f8fafc; margin-bottom:8px;'>Índices Dedutíveis de Qualidade e Agilidade</div>
                                <div style='font-size:12px; color:#cbd5e1; margin-bottom:6px;'>⚡ Score Agilidade Coletador: <b style='color:#38bdf8;'>{sc_ag_c:.1f}%</b></div>
                                <div style='font-size:12px; color:#cbd5e1; margin-bottom:6px;'>⚙️ Score Agilidade Processador: <b style='color:#a855f7;'>{sc_ag_p:.1f}%</b></div>
                                <div style='font-size:12px; color:#cbd5e1; margin-bottom:6px;'>🧪 Viabilidade Pré-Analítica: <b style='color:#10b981;'>{sc_viab:.1f}%</b></div>
                                <div style='font-size:12px; color:#cbd5e1;'>🧬 Organoide 3D: <b>{row_det_pi[22] or 'Não realizado'}</b></div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    # 3. PAINEL VISUAL DE VOLUMETRIA E RASTREABILIDADE ESPACIAL DAS AMOSTRAS
                    st.markdown("#### 3. Rastreabilidade Volumétrica e Espacial por Biotipo Amostral")
                    aliquotas_pac = fetch_all(
                        """SELECT amostra_nome, codigo_etiqueta, n_aliquotas_biobanco, vol_aliquota_ml,
                                  vol_total_biobanco_ml, caixa, linha, coluna, densidade_celular
                           FROM hpb_biobanco_aliquotas WHERE id_hpb = :id ORDER BY cod_biotipo""",
                        {"id": id_sel_hpb},
                    )
                    if aliquotas_pac:
                        cols_v = st.columns(len(aliquotas_pac))
                        caixas_do_paciente = set()
                        for idx_al, (am_n, cod_e, n_al, v_un, v_tot, cx_a, lin_a, col_a, dens_a) in enumerate(aliquotas_pac):
                            ativo_al = int(n_al or 0) > 0
                            if ativo_al and cx_a and cx_a != "0":
                                caixas_do_paciente.add(cx_a)
                            bg_v = "#064e3b" if ativo_al else "#1e293b"
                            bd_v = "#10b981" if ativo_al else "#334155"
                            cols_v[idx_al].markdown(
                                f"<div style='background:{bg_v}; border:1px solid {bd_v}; padding:8px; border-radius:8px; text-align:center; color:#f8fafc;'>"
                                f"<div style='font-size:11px; font-weight:800;'>{am_n}</div>"
                                f"<div style='font-size:13px; color:#34d399; font-weight:900; margin:3px 0;'>{n_al} alíq. ({v_tot} mL)</div>"
                                f"<div style='font-size:10px; color:#cbd5e1;'> Caixa: <b>{cx_a}</b><br>L{lin_a} : C({col_a})</div></div>",
                                unsafe_allow_html=True,
                            )
                        if caixas_do_paciente:
                            cx_pi_view = st.selectbox(
                                "Mapa Espacial 10x10 das Criocaixas deste Paciente:",
                                sorted(list(caixas_do_paciente)),
                                key=f"pi_cx_sel_{id_sel_hpb}",
                            )
                            renderizar_visualizador_grade_10x10_criocaixa(cx_pi_view)

                # 4. ERROS E TIPOS DE ERRO APONTADOS PELO CONTROLE DE QUALIDADE PARA ESTE PACIENTE
                st.markdown("#### 4. Relatório de Erros e Alterações Detectadas pelo Controle de Qualidade (2ª Checagem)")
                df_erros_pac = fetch_df(
                    """SELECT data_hora AS "Data/Hora", etapa_origem AS "Etapa",
                              operador_responsavel AS "Operador Responsável", tipo_erro AS "Tipo de Erro",
                              campo_alterado AS "Campos Corrigidos", valor_anterior AS "Valor Original",
                              valor_corrigido AS "Valor Corrigido pelo CQ", observacao_cq AS "Parecer do CQ"
                       FROM hpb_erros_revisao_cq WHERE id_hpb = :id ORDER BY id_erro DESC""",
                    {"id": id_sel_hpb},
                )
                if df_erros_pac.empty:
                    st.success("Nenhuma divergência ou erro apontado pelo Controle de Qualidade para este paciente até o momento.")
                else:
                    st.error(f"Foram registradas {len(df_erros_pac)} correção(ões) pelo Controle de Qualidade neste paciente:")
                    st.dataframe(df_erros_pac, use_container_width=True, hide_index=True)

            # BUSCA POR NOME DE OPERADOR NA AUDITORIA GERAL
            with st.expander("🔎 Auditoria Global Filtrável pelo Nome da Pessoa (Coletas, Processamentos, Trocas e Erros CQ)"):
                busca_op = st.text_input("Digite o Nome ou Username da pessoa:", key="pi_busca_global_nome").strip()
                df_logs_all = fetch_df("SELECT * FROM hpb_audit_logs ORDER BY id DESC LIMIT 400")
                if busca_op and not df_logs_all.empty:
                    m_b = df_logs_all.astype(str).apply(lambda c: c.str.contains(busca_op, case=False, na=False)).any(axis=1)
                    df_logs_all = df_logs_all[m_b]
                st.dataframe(df_logs_all, use_container_width=True, hide_index=True)

    # =========================================================================
    # INTERFACE 5: RANKINGS (SEMANA, MÊS E SEMESTRE) & TROCAS DE ESCALA (3 + 1 MOACYR)
    # =========================================================================
    elif modulo_ativo.startswith("Rankings"):
        st.header("🏆 Rankings de Desempenho (Semana, Mês e Semestre) & Trocas de Escala")

        r_tab1, r_tab2 = st.tabs([
            "1. Rankings de Coletores e Processadores (Semana / Mês / Semestre)",
            "2. Trocas de Escala entre Operadores (Até 3/mês + 1 se escolha de Moacyr)",
        ])

        with r_tab1:
            periodo_sel = st.radio(
                "Selecione a Janela Temporal do Ranking:",
                ["Semana Vigente (Últimos 7 dias)", "Mês Vigente (Últimos 30 dias)", "Semestre Vigente (Últimos 180 dias)"],
                horizontal=True,
                key="rad_janela_ranking",
            )
            dias_j = 7 if "Semana" in periodo_sel else (30 if "Mês" in periodo_sel else 180)
            df_rank_c, df_rank_p = calcular_rankings_coletores_e_processadores(dias_j)

            rc1, rc2 = st.columns(2)
            with rc1:
                st.subheader(f"🥇 Ranking de Coletores — {periodo_sel}")
                st.dataframe(df_rank_c, use_container_width=True, hide_index=True)
            with rc2:
                st.subheader(f"🥇 Ranking de Processadores — {periodo_sel}")
                st.dataframe(df_rank_p, use_container_width=True, hide_index=True)

        with r_tab2:
            mes_atual_str = date.today().strftime("%Y-%m")
            norm_u, ext_u = contar_trocas_mes_operador(st.session_state.username, mes_atual_str)
            st.info(
                f"**Seu Saldo no Mês (`{mes_atual_str}`):** `{norm_u}/{LIMITE_TROCAS_MENSAL_PADRAO}` trocas utilizadas "
                f"| **Bônus (+1 Escolha de Moacyr):** `{ext_u}/{BONUS_TROCA_MOACYR}`."
            )
            df_ag_tr = fetch_df("SELECT id_agenda, id_hpb_gerado, hospital, setor_coleta, dia_semana_mapa FROM hpb_agenda_cirurgica ORDER BY id_agenda DESC")
            usuarios_tr = fetch_all("SELECT username, nome_completo, role FROM hpb_usuarios WHERE ativo = 1")
            mapa_u_tr = {r[0]: f"{r[1]} ({r[0]} - {r[2]})" for r in usuarios_tr}

            if not df_ag_tr.empty and st.session_state.role != "Bioinformata":
                with st.form("form_troca_escala_v5"):
                    tc1, tc2, tc3 = st.columns(3)
                    tipo_tr = tc1.selectbox("Escala", ["Coleta", "Processamento"], key="tr5_tipo")
                    ag_tr = tc2.selectbox("Agendamento (ID)", df_ag_tr["id_agenda"].tolist(), format_func=lambda x: f"Agenda #{x} ({df_ag_tr[df_ag_tr['id_agenda']==x]['hospital'].iloc[0]})", key="tr5_ag")
                    sol_tr = tc3.selectbox("Quem sai da escala", list(mapa_u_tr.keys()), format_func=lambda u: mapa_u_tr[u], key="tr5_sol")

                    tc4, tc5, tc6 = st.columns(3)
                    sub_tr = tc4.selectbox("Quem assume a escala", list(mapa_u_tr.keys()), format_func=lambda u: mapa_u_tr[u], key="tr5_sub")
                    mot_tr = tc5.text_input("Motivo da Troca", key="tr5_mot")
                    bonus_m = tc6.checkbox("Troca Extra (+1 Escolha de Moacyr)", disabled=(not usuario_eh_moacyr()), key="tr5_bonus")

                    if st.form_submit_button("Efetivar Troca de Escala (Auditada)", type="primary"):
                        n_us, e_us = contar_trocas_mes_operador(sol_tr, mes_atual_str)
                        if sol_tr == sub_tr:
                            st.error("Selecione operadores diferentes.")
                        elif not bonus_m and n_us >= LIMITE_TROCAS_MENSAL_PADRAO:
                            st.error("Limite de 3 trocas mensais atingido. Somente o Prof. Moacyr pode autorizar +1 troca extra.")
                        elif bonus_m and e_us >= BONUS_TROCA_MOACYR:
                            st.error("Cota máxima (3 + 1 Moacyr) já atingida neste mês.")
                        else:
                            agora_t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            run_sql(
                                """INSERT INTO hpb_trocas_escala
                                   (mes_referencia, tipo_escala, id_referencia, solicitante_username, solicitante_nome,
                                    substituto_username, substituto_nome, autorizado_extra_moacyr, autorizado_por, motivo_troca, status_troca, data_hora_troca)
                                   VALUES (:m, :t, :idref, :solu, :soln, :subu, :subn, :ext, :aut, :mot, 'EFETIVADA', :dh)""",
                                {
                                    "m": mes_atual_str, "t": tipo_tr, "idref": f"AGENDA-{ag_tr}",
                                    "solu": sol_tr, "soln": mapa_u_tr[sol_tr], "subu": sub_tr, "subn": mapa_u_tr[sub_tr],
                                    "ext": 1 if bonus_m else 0, "aut": st.session_state.username, "mot": mot_tr, "dh": agora_t,
                                },
                            )
                            col_up_tr = "coletor_escalado" if tipo_tr == "Coleta" else "processador_escalado"
                            run_sql(f"UPDATE hpb_agenda_cirurgica SET {col_up_tr} = :sub WHERE id_agenda = :id", {"sub": sub_tr, "id": ag_tr})
                            log_audit("Troca de Escala", f"AGENDA-{ag_tr}", f"{tipo_tr}: {sol_tr} -> {sub_tr} (Extra Moacyr: {bonus_m}).")
                            st.success("Troca registrada!")
                            st.rerun()

            st.dataframe(fetch_df("SELECT * FROM hpb_trocas_escala ORDER BY id_troca DESC"), use_container_width=True, hide_index=True)

    # =========================================================================
    # INTERFACE 6: BIOINFORMATA (ACESSO TOTAL DE CONSULTA, SEM PODER DE ESCALA)
    # =========================================================================
    elif modulo_ativo.startswith("Área do Bioinformata"):
        st.header("Área do Bioinformata — Consulta Integrada Clínico-Genômica, Biobanco e Criocaixas 10x10")
        st.info("Acesso liberado a Nomes, Prontuários, Ficha Clínica, iFERA, TRIZOL (Página13), Organoides/CAFs e Criocaixas 10x10 (sem permissão para alterar escalas).")
        bt1, bt2, bt3 = st.tabs(["1. Coletas e Ficha Clínica", "2. Criocaixas 10x10 e Biobanco", "3. Extração TRIZOL (Página13) e Organoides"])
        with bt1:
            st.dataframe(fetch_df("SELECT * FROM hpb_coletas_ifera ORDER BY id_hpb DESC"), use_container_width=True, hide_index=True)
            st.dataframe(fetch_df("SELECT * FROM hpb_ficha_clinica ORDER BY id_hpb DESC"), use_container_width=True, hide_index=True)
        with bt2:
            cx_b = st.selectbox("Criocaixa 10x10:", listar_caixas_criogenicas_disponiveis(), key="bio_cx_sel")
            renderizar_visualizador_grade_10x10_criocaixa(cx_b)
            st.dataframe(fetch_df("SELECT * FROM hpb_biobanco_aliquotas ORDER BY id_hpb DESC"), use_container_width=True, hide_index=True)
        with bt3:
            st.dataframe(fetch_df("SELECT * FROM hpb_extracao_rna_pagina13 ORDER BY id_hpb DESC"), use_container_width=True, hide_index=True)
            st.dataframe(fetch_df("SELECT * FROM hpb_cultivos_organoides_cafs ORDER BY id_reg DESC"), use_container_width=True, hide_index=True)

    # =========================================================================
    # INTERFACE 7: CHAT PRIVADO ENTRE OPERADORES (NUNCA EXPOSTO NA AUDITORIA)
    # =========================================================================
    elif modulo_ativo.startswith("Chat Privado"):
        st.header("💬 Chat Privado entre Operadores da Rede HPB")
        st.caption("Sigilo de Comunicação: As mensagens trocadas entre operadores não são registradas nem exibidas na trilha de auditoria.")

        todos_ops = fetch_all("SELECT username, nome_completo, role FROM hpb_usuarios WHERE ativo = 1 ORDER BY nome_completo")
        opcoes_dest = {"TODOS_EQUIPE": "Canal Geral da Equipe Rede HPB"}
        for u_op, n_op, r_op in todos_ops:
            if u_op != st.session_state.username:
                opcoes_dest[u_op] = f"Mensagem Direta: {n_op} ({u_op} - {r_op})"

        dest_chat = st.selectbox("Conversar com:", list(opcoes_dest.keys()), format_func=lambda k: opcoes_dest[k], key="chat_v5_dest")
        if dest_chat == "TODOS_EQUIPE":
            msgs = fetch_all("SELECT remetente_nome, remetente_username, mensagem, data_hora FROM hpb_chat_operadores WHERE destinatario_username = 'TODOS_EQUIPE' ORDER BY id_msg DESC LIMIT 80")
        else:
            msgs = fetch_all(
                """SELECT remetente_nome, remetente_username, mensagem, data_hora FROM hpb_chat_operadores
                   WHERE (remetente_username = :eu AND destinatario_username = :ele)
                      OR (remetente_username = :ele AND destinatario_username = :eu)
                   ORDER BY id_msg DESC LIMIT 80""",
                {"eu": st.session_state.username, "ele": dest_chat},
            )

        with st.form("form_chat_v5", clear_on_submit=True):
            cm1, cm2 = st.columns([5, 1])
            txt_m = cm1.text_input("Mensagem:", key="chat_v5_msg")
            if cm2.form_submit_button("Enviar", type="primary") and txt_m.strip():
                run_sql(
                    "INSERT INTO hpb_chat_operadores (remetente_username, remetente_nome, destinatario_username, mensagem, data_hora) VALUES (:ru, :rn, :du, :m, :dh)",
                    {"ru": st.session_state.username, "rn": st.session_state.nome_usuario, "du": dest_chat, "m": txt_m.strip(), "dh": datetime.now().strftime("%d/%m/%Y %H:%M")},
                )
                st.rerun()

        for r_n, r_u, m_t, dh_t in reversed(msgs):
            alin = "right" if r_u == st.session_state.username else "left"
            bg_c = "#065f46" if r_u == st.session_state.username else "#1e293b"
            st.markdown(
                f"<div style='text-align:{alin}; margin-bottom:6px;'>"
                f"<div style='display:inline-block; background:{bg_c}; color:#f8fafc; padding:8px 12px; border-radius:10px; border:1px solid #334155; text-align:left;'>"
                f"<div style='font-size:10.5px; color:#94a3b8;'><b>{r_n}</b> ({r_u}) • {dh_t}</div>"
                f"<div style='font-size:13px;'>{m_t}</div></div></div>",
                unsafe_allow_html=True,
            )
# FIM DO ARQUIVO rede_hpb_cloud.py (PARTE 4 DE 4)
