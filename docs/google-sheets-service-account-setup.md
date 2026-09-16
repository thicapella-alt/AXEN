# Service account do Google — passo a passo (item 0.6)

> Estes passos usam a **sua conta Google pessoal** no Google Cloud Console.
> Nada aqui foi executado por mim — a service account precisa ser criada
> por você. O que fiz foi implementar o cliente que consome a chave
> depois de pronta (`integrations/axen_sheets.py`).

## 1. Criar (ou reaproveitar) um projeto no Google Cloud

1. Acesse https://console.cloud.google.com/
2. No seletor de projeto (topo da página), clique em **Novo Projeto**
   (ou reaproveite um projeto existente, se já tiver algum ligado à
   AXEN).
3. Nome sugerido: `axen-intelligence`. Não precisa de organização/billing
   account — Sheets API e Drive API são gratuitas para este volume de uso.

## 2. Habilitar as APIs necessárias

Com o projeto selecionado:

1. Menu ☰ → **APIs e serviços** → **Biblioteca**
2. Busque **Google Sheets API** → clique **Ativar**
3. Busque **Google Drive API** → clique **Ativar**
   (necessária porque `gspread.open_by_key()` usa a Drive API para
   resolver metadados da planilha antes de ler as células)

## 3. Criar a service account

1. Menu ☰ → **APIs e serviços** → **Credenciais**
2. **Criar credenciais** → **Conta de serviço**
3. Nome: `axen-sheets-reader` (o e-mail gerado será algo como
   `axen-sheets-reader@axen-intelligence.iam.gserviceaccount.com` —
   **copie esse e-mail**, você vai precisar dele no passo 5)
4. Papel (role): pode pular esse passo / deixar em branco — o acesso à
   planilha é controlado por **compartilhamento da planilha em si**
   (passo 5), não por papel de IAM no projeto.
5. Clique **Concluir**.

## 4. Gerar a chave JSON

1. Na lista de contas de serviço, clique na `axen-sheets-reader` recém-criada
2. Aba **Chaves** → **Adicionar chave** → **Criar nova chave** → formato **JSON** → **Criar**
3. O navegador baixa um arquivo `.json` — **guarde-o**, ele não pode ser
   baixado de novo depois (só recriado).

## 5. Compartilhar as planilhas com a service account

Para cada planilha que o VPS vai ler ("DRE AXEN" — que já contém as abas
`Movimentos`/`Contagem` — e "Compras", caso seja um arquivo separado):

1. Abra a planilha no Google Sheets
2. Botão **Compartilhar** (canto superior direito)
3. Cole o e-mail da service account (do passo 3) no campo de
   compartilhamento
4. Papel: **Leitor** (somente leitura — é tudo que o módulo precisa)
5. Desmarque "Notificar pessoas" (é uma conta de serviço, não lê e-mail)
   e confirme

## 6. Colocar a chave no VPS

**Não cole o JSON no `.env` como uma variável de texto** — é mais seguro
salvar como arquivo próprio com permissão restrita:

```bash
# via SSH, no servidor:
mkdir -p /var/www/axen/credentials
nano /var/www/axen/credentials/google-sheets-sa.json
# cole o conteúdo do JSON baixado no passo 4, salve (Ctrl+O, Enter, Ctrl+X)

chown www-data:www-data /var/www/axen/credentials/google-sheets-sa.json
chmod 600 /var/www/axen/credentials/google-sheets-sa.json
```

Depois, no `.env` do servidor:

```bash
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_CREDENTIALS_PATH=/var/www/axen/credentials/google-sheets-sa.json
GOOGLE_SHEETS_DRE_ID=<id da planilha DRE AXEN — o trecho entre /d/ e /edit na URL>
GOOGLE_SHEETS_COMPRAS_ID=<id da planilha Compras — pode repetir o mesmo valor acima se for a mesma planilha>
```

## 7. Instalar as dependências novas e reiniciar

```bash
cd /var/www/axen && source venv/bin/activate
pip install -r requirements.txt   # traz gspread + google-auth
sudo systemctl restart axen-api axen-scheduler
```

## Como confirmar que funcionou

Nenhum endpoint/rotina ainda **chama** `axen_sheets.py` em produção
nesta sessão (é só o módulo + testes, sem integração ligada em nenhum
job) — a ligação real (ler `Movimentos`/`Contagem` periodicamente e
gravar em `stock_movements`) é trabalho da sessão S2. Pra conferir que
as credenciais e o compartilhamento estão certos antes disso, dá pra
testar manualmente depois do passo 6:

```bash
cd /var/www/axen && source venv/bin/activate
python -c "
from integrations.axen_sheets import AxenSheetsClient
import os
c = AxenSheetsClient()
r = c.read_movimentos(os.environ['GOOGLE_SHEETS_DRE_ID'])
print(f'{len(r.rows)} linha(s) válida(s), {len(r.errors)} erro(s)')
for e in r.errors:
    print(f'  linha {e.row_number}: {e.message}')
"
```
