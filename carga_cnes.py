#Script para download, criação e ingestão de tabelas DataSUS do Cnes
#Feito por Rafael Campos - V4

import pysus
import pandas as pd
import itertools
import os
import csv
import gc
import shutil
import time
from io import StringIO
from getpass import getpass
from datetime import datetime
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import URL
from pathlib import Path

#Parâmetros do que está sendo buscado dentro da base do CNES
tabelas = [
    'ST', 'LT', 'PF', 'DC', 'EQ', 'SR', 'HB', 'EP', 'RC', 'IN', 'EE', 'EF', 'GM'
] #Tabelas

ufs = [
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO',
    'MA', 'MT', 'MS', 'MG', 'PA', 'PB', 'PR', 'PE', 'PI',
    'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO',
] #UFS
anos = [2025] #Anos
meses = range(1, 13) #Meses 1 a 12

#Limpeza do cache do PySUS
apagar_apos_gravar = True #Apaga cada parquet logo após gravar no banco com sucesso
limpar_cache_no_fim = True #Apaga a pasta de downloads do CNES no fim (inclusive arquivos de cargas que falharam)

#Pasta onde o PySUS guarda os parquets baixados do CNES
pasta_cache_cnes = Path(pysus.CACHEPATH) / 'downloads' / 'ducklake' / 'cnes'

#Pasta do script
try:
    pasta_script = Path(__file__).resolve().parent
except NameError:
    pasta_script = Path.cwd()

#Arquivo de falhas
arquivo_falhas = pasta_script / f'falhas_cnes_{datetime.now():%Y%m%d_%H%M%S}.txt'
colunas_falhas = ['tabela', 'uf', 'ano', 'mes', 'etapa', 'erro']
falhas = []


#Ingestão via COPY
def psql_insert_copy(table, conn, keys, data_iter):
    dbapi_conn = conn.connection
    with dbapi_conn.cursor() as cur:
        s_buf = StringIO()
        csv.writer(s_buf).writerows(data_iter)
        s_buf.seek(0)
        colunas = ', '.join(f'"{k}"' for k in keys)
        nome_tabela = f'"{table.schema}"."{table.name}"' if table.schema else f'"{table.name}"'
        cur.copy_expert(f'COPY {nome_tabela} ({colunas}) FROM STDIN WITH CSV', s_buf)


#Registro de falhas na tela, na lista e no arquivo txt
def registra_falha(tabela, uf, ano, mes, etapa, e):
    erro = str(getattr(e, 'orig', e))
    erro = ' '.join(erro.split())[:500]
    print(f'  -> falhou ({etapa}): {erro}')
    falhas.append((tabela, uf, ano, mes, etapa, erro))

    novo = not os.path.exists(arquivo_falhas)
    with open(arquivo_falhas, 'a', newline='', encoding='utf-8-sig' if novo else 'utf-8') as f:
        w = csv.writer(f, delimiter='\t')
        if novo:
            w.writerow(colunas_falhas)
        w.writerow([tabela, uf, ano, mes, etapa, erro])


#Remoção do parquet de uma carga do cache do PySUS
def apaga_arquivo_cache(tabela, uf, ano, mes):
    arquivo = pasta_cache_cnes / f'{tabela}{uf}{ano % 100:02d}{mes:02d}.parquet'
    try:
        arquivo.unlink(missing_ok=True)
    except OSError as e:
        print(f'  -> não foi possível apagar {arquivo.name}: {e}')


#Tamanho de uma pasta em MB
def tamanho_mb(pasta):
    if not pasta.exists():
        return 0
    return sum(f.stat().st_size for f in pasta.rglob('*') if f.is_file()) / 1024**2


def main():
    inicio = time.perf_counter()
    print(f'Início do processo - {datetime.now()}')

    #URL de conexão no banco
    url = URL.create(
        drivername='postgresql+psycopg2',
        username='rafael.campos',
        password=os.getenv('PGPASSWORD') or getpass('Senha do Postgres: '),
        host='localhost',
        port=5432,
        database='DataSUS',
    )

    #Engine de conexão com o banco de dados
    engine = create_engine(url, pool_pre_ping=True)

    #Criação do schema, caso não exista
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS cnes'))

    #Loop principal de download e escrita no banco de dados
    for tabela, uf, ano, mes in itertools.product(tabelas, ufs, anos, meses):
        t0 = time.perf_counter()
        nome = tabela.lower()
        competen = f'{ano}{mes:02d}'
        print(f'{uf}/{tabela}: {mes:02d}/{ano}')

        try:
            df_t = pysus.ftp.cnes(
                state=uf, year=ano, month=mes, group=tabela, as_dataframe=True
            )
        except Exception as e:
            registra_falha(tabela, uf, ano, mes, 'download', e)
            continue

        #Sem arquivo publicado para a UF/competência, o PySUS devolve um dataframe vazio, sem colunas
        if df_t.empty:
            registra_falha(tabela, uf, ano, mes, 'sem dados', 'nenhum arquivo publicado para esta UF/competência')
            del df_t
            continue

        try:
            #Padronização para o Postgres
            df_t.columns = df_t.columns.astype(str).str.lower()
            df_t = df_t.replace('', None)
            df_t = df_t.assign(uf=uf, competen=competen)

            with engine.begin() as conn:
                #Se a tabela já existe, remove a carga anterior da mesma UF/competência
                if inspect(conn).has_table(nome, schema='cnes'):
                    conn.execute(
                        text(f'DELETE FROM cnes.{nome} WHERE uf = :uf AND competen = :competen'),
                        {'uf': uf, 'competen': competen},
                    )

                #Se não existe, o to_sql cria a tabela
                df_t.to_sql(nome, conn, schema='cnes', if_exists='append',
                            index=False, method=psql_insert_copy)

                #Índice para o DELETE não varrer a tabela inteira
                conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS ix_{nome}_uf_competen ON cnes.{nome} (uf, competen)'
                ))
            print(f'  -> cnes.{nome}: {len(df_t):,} linhas ({time.perf_counter() - t0:.1f}s)')

            #Só apaga o parquet se a gravação deu certo, para reaproveitar em caso de nova tentativa
            if apagar_apos_gravar:
                apaga_arquivo_cache(tabela, uf, ano, mes)
        except Exception as e:
            registra_falha(tabela, uf, ano, mes, 'gravação', e)

        #Libera da memória o dataframe da iteração
        del df_t
        gc.collect()

    #Resumo das falhas
    df_falhas = pd.DataFrame(falhas, columns=colunas_falhas)
    if df_falhas.empty:
        print('Nenhuma falha registrada.')
    else:
        print(f'{len(df_falhas)} falhas gravadas em {os.path.abspath(arquivo_falhas)}')
        print(df_falhas.to_string(index=False))

    #Limpeza final da pasta de downloads do CNES no cache do PySUS
    if limpar_cache_no_fim and pasta_cache_cnes.exists():
        mb = tamanho_mb(pasta_cache_cnes)
        shutil.rmtree(pasta_cache_cnes, ignore_errors=True)
        print(f'Cache do PySUS limpo: {pasta_cache_cnes} ({mb:,.1f} MB)')

    engine.dispose()

    print(f'Fim do processo - {datetime.now()}')
    print(f'Tempo total: {(time.perf_counter() - inicio) / 60:.1f} min')


if __name__ == '__main__':
    main()