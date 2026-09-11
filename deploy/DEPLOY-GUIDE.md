# Guia de Deploy — AXEN Intelligence
## app.useaxen.com.br · DigitalOcean Ubuntu 24.04 · 1 GB RAM

---

## PARTE 1 — DNS (fazer no provedor do domínio useaxen.com.br)

Adicione um registro DNS **tipo A**:

| Campo | Valor |
|---|---|
| Tipo | A |
| Nome / Host | `app` |
| Valor / Destino | `IP_DA_MAQUINA_DIGITAL_OCEAN` |
| TTL | 3600 (ou padrão) |

> Para descobrir o IP da máquina: painel DigitalOcean → seu Droplet → o IP aparece logo abaixo do nome.

Aguarde 5–30 minutos para o DNS propagar antes de continuar.

---

## PARTE 2 — GitHub (fazer no seu computador Windows)

### 2.1 Criar repositório privado

1. Acesse https://github.com → **New repository**
2. Nome: `axen-intelligence`
3. Visibilidade: **Private**
4. Clique em **Create repository**

### 2.2 Subir o projeto para o GitHub

Abra o PowerShell na pasta do projeto e execute:

```powershell
cd "C:\Users\thcap\OneDrive\BKP 23_06_2019\Área de Trabalho\AXEN\AXEN Inteligence"

git init
git add .
git commit -m "feat: AXEN Intelligence v1.0"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/axen-intelligence.git
git push -u origin main
```

> Substitua `SEU_USUARIO` pelo seu usuário GitHub.  
> O `.gitignore` já protege o `.env`, `axen.db` e outros arquivos sensíveis.

---

## PARTE 3 — Servidor (executar via SSH no DigitalOcean)

### 3.1 Conectar ao servidor

```bash
ssh root@IP_DA_MAQUINA
```

### 3.2 Executar o script de instalação

```bash
curl -O https://raw.githubusercontent.com/SEU_USUARIO/axen-intelligence/main/deploy/setup.sh
bash setup.sh
```

O script vai perguntar:
1. **URL do GitHub** → cole a URL do repositório (ex: `https://github.com/SEU_USUARIO/axen-intelligence.git`)
2. **Usuário** → escolha um usuário para login no dashboard (ex: `axen`)
3. **Senha** → escolha uma senha forte

O setup demora ~5 minutos e instala tudo automaticamente.

### 3.3 Configurar as credenciais reais

Após o setup, edite o arquivo `.env` com suas credenciais reais:

```bash
nano /var/www/axen/.env
```

Após salvar (`Ctrl+O`, `Enter`, `Ctrl+X`):

```bash
systemctl restart axen-api
```

---

## PARTE 4 — Verificar que está funcionando

```bash
# Status dos serviços
systemctl status axen-api
systemctl status axen-scheduler

# Logs em tempo real
journalctl -u axen-api -f

# Testar a API diretamente
curl http://127.0.0.1:8001/healthz
```

Acesse no browser: **https://app.useaxen.com.br**

---

## PARTE 5 — Atualizar o sistema no futuro

Sempre que fizer mudanças no código:

**No seu PC (Windows):**
```powershell
git add .
git commit -m "descrição da mudança"
git push
```

**No servidor:**
```bash
bash /var/www/axen/deploy/update.sh
```

---

## Comandos úteis no servidor

```bash
# Ver logs da API
journalctl -u axen-api -f

# Ver logs do scheduler
journalctl -u axen-scheduler -f

# Ver logs do nginx
tail -f /var/log/nginx/axen-error.log

# Reiniciar serviços
systemctl restart axen-api
systemctl restart axen-scheduler

# Rodar o scraper manualmente agora
cd /var/www/axen
source venv/bin/activate
python axen_scheduler.py --run-now

# Ver uso de memória RAM
free -h

# Ver espaço em disco
df -h
```

---

## Trocar a senha do dashboard

```bash
htpasswd /etc/nginx/.htpasswd-axen SEU_USUARIO
systemctl reload nginx
```

---

## Estrutura de portas (não conflita com Tradebit)

| Serviço | Porta | Visível externamente? |
|---|---|---|
| nginx (HTTPS) | 443 | Sim — entrada pública |
| nginx (HTTP→redirect) | 80 | Sim — redireciona para 443 |
| axen-api (uvicorn) | 8001 | Não — apenas interno |
| Tradebit | qualquer outra | Não afetado |
