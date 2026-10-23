 Carga DataSUS – CNES

Script em Python que baixa os arquivos do **CNES** (Cadastro Nacional de Estabelecimentos de Saúde) do FTP do DataSUS e carrega os dados em um banco **PostgreSQL**, com uma tabela por grupo do CNES.

## O que o script faz

1. Percorre todas as combinações de **grupo × UF × ano × mês** definidas nos parâmetros.
2. Baixa cada arquivo com o [PySUS](https://github.com/AlertaDengue/PySUS).
3. Padroniza os dados: colunas em minúsculas, textos vazios convertidos em `NULL`, colunas `uf` e `competen` (AAAAMM) adicionadas.
4. Grava no schema `cnes` do Postgres, em uma tabela por grupo (`cnes.st`, `cnes.pf`, `cnes.lt`...):
   - cria o schema e as tabelas automaticamente, caso não existam;
   - antes de gravar, apaga os dados da mesma UF/competência, então rodar de novo **não duplica** registros;
   - usa `COPY` para gravar, bem mais rápido que `INSERT`;
   - cria um índice em `(uf, competen)` em cada tabela.
5. Registra as falhas de download ou gravação em um arquivo `falhas_cnes_AAAAMMDD_HHMMSS.txt`, na pasta do script, sem interromper a carga.

## Grupos do CNES

| Grupo | Conteúdo |
|---|---|
| ST | Estabelecimentos: dados gerais da unidade (CNES, CNPJ, tipo de unidade, esfera administrativa, atendimento SUS) |
| LT | Leitos: tipo de leito e quantidades existentes e disponíveis para o SUS |
| PF | Profissionais: vínculo dos profissionais com os estabelecimentos (CBO, atendimento SUS, tipo de vínculo) |
| DC | Dados complementares |
| EQ | Equipamentos: tipo, quantidade e disponibilidade para o SUS |
| SR | Serviços especializados |
| HB | Habilitações junto ao Ministério da Saúde |
| EP | Equipes (ex.: Saúde da Família, Saúde Bucal) |
| RC | Regras contratuais |
| IN | Incentivos |
| EE | Estabelecimentos de ensino |
| EF | Estabelecimentos filantrópicos |
| GM | Gestão e metas |

## Requisitos

- Python 3.10+
- PostgreSQL
- Bibliotecas:

```bash
pip install pysus pandas sqlalchemy psycopg2-binary
```

## Configuração

No início do `carga_cnes_v3.py`:

```python
tabelas = ['ST', 'LT', 'PF', ...]   # grupos do CNES
ufs     = ['AC', 'AL', ...]          # UFs
anos    = [2025]
meses   = range(1, 13)
```

Os dados de conexão (usuário, host, porta e banco) ficam no bloco `URL.create(...)`.

A senha **não fica no código**. O script lê da variável de ambiente `PGPASSWORD` ou pede no terminal.

## Como executar

```bash
python carga_cnes_v3.py
```

Para um primeiro teste, reduza os parâmetros (por exemplo, `ufs = ['AC']` e `meses = [1]`).

## Observações

- A carga completa (13 grupos × 27 UFs × 12 meses) tem mais de 4 mil arquivos e pode levar horas.
- O PySUS guarda uma cópia dos arquivos baixados na pasta `pysus` do usuário. Na carga completa, isso pode ocupar dezenas de GB.
- A estrutura de cada tabela é definida pelo primeiro arquivo carregado daquele grupo. Se um arquivo posterior trouxer uma coluna nova, a gravação dele falha e aparece no arquivo de falhas.

## Fonte dos dados

[DataSUS – Transferência de arquivos (CNES)](https://datasus.saude.gov.br/transferencia-de-arquivos/). A documentação oficial (dicionário de dados, layout e domínios) também está disponível nesse site.
