#!/bin/bash
# =============================================================================
# AXEN Intelligence — Script de instalação no servidor DigitalOcean
# Ubuntu 24.04 LTS · app.useaxen.com.br
#
# Execute como root:
#   bash setup.sh
# =============================================================================
set -e

DOMAIN="app.useaxen.com.br"
APP_DIR="/var/www/axen"
REPO_URL=""   # preenchido interativamente abaixo

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[AVISO]${NC} $1"; }
error()   { echo -e "${RED}[ERRO]${NC} $1"; exit 1; }

echo ""
echo "======================================================"
echo "  AXEN Intelligence — Setup de Produção"
echo "  Domínio: $DOMAIN"
echo "======================================================"
echo ""

# ── Verificar root ────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Execute este script como root: sudo bash setup.sh"

# ── URL do repositório GitHub ─────────────────────────────────────────────────
echo -n "Cole a URL do repositório GitHub (ex: https://github.com/usuario/axen.git): "
read -r REPO_URL
[[ -z "$REPO_URL" ]] && error "URL do repositório não pode ser vazia."

# ── Usuário e senha Basic Auth ────────────────────────────────────────────────
echo ""
echo -n "Usuário para login no dashboard AXEN: "
read -r AUTH_USER
echo -n "Senha para login no dashboard AXEN: "
read -rs AUTH_PASS
echo ""
[[ -z "$AUTH_USER" || -z "$AUTH_PASS" ]] && error "Usuário e senha não podem ser vazios."

# =============================================================================
# 1. SWAP — crítico para máquinas com 1 GB RAM
# =============================================================================
info "Configurando swap de 2 GB (necessário para Playwright + API)..."
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    success "Swap de 2 GB ativado."
else
    warn "Swap já configurado — pulando."
fi

# =============================================================================
# 2. PACOTES DO SISTEMA
# =============================================================================
info "Atualizando pacotes do sistema..."
apt-get update -qq

info "Instalando dependências base..."
apt-get install -y -qq \
    git curl wget gnupg ca-certificates \
    python3 python3-pip python3-venv \
    apache2-utils \
    certbot python3-certbot-nginx \
    build-essential libssl-dev libffi-dev python3-dev \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2t64

success "Pacotes instalados."

# ── Node.js 20 ────────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null || [[ $(node -v | cut -d'v' -f2 | cut -d'.' -f1) -lt 20 ]]; then
    info "Instalando Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - &>/dev/null
    apt-get install -y -qq nodejs
    success "Node.js $(node -v) instalado."
else
    success "Node.js $(node -v) já instalado."
fi

# =============================================================================
# 3. CÓDIGO DA APLICAÇÃO
# =============================================================================
info "Clonando repositório em $APP_DIR..."
if [[ -d "$APP_DIR/.git" ]]; then
    warn "Repositório já existe em $APP_DIR — fazendo git pull."
    cd "$APP_DIR" && git pull origin main
else
    git clone "$REPO_URL" "$APP_DIR"
fi
success "Código clonado."

cd "$APP_DIR"

# =============================================================================
# 4. AMBIENTE PYTHON
# =============================================================================
info "Criando ambiente virtual Python..."
python3 -m venv venv
source venv/bin/activate

info "Instalando dependências Python..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
success "Dependências Python instaladas."

# ── Playwright (Chromium para o scraper) ──────────────────────────────────────
info "Instalando Chromium para o Playwright..."
python -m playwright install chromium
success "Chromium instalado."

# =============================================================================
# 5. FRONTEND — build de produção
# =============================================================================
info "Instalando dependências do frontend..."
cd frontend
npm install --silent
info "Gerando build de produção do React/Vite..."
npm run build
cd ..
success "Frontend compilado em frontend/dist/"

# =============================================================================
# 6. ARQUIVO .env
# =============================================================================
if [[ ! -f "$APP_DIR/.env" ]]; then
    info "Criando arquivo .env a partir do exemplo..."
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    warn "IMPORTANTE: edite $APP_DIR/.env e preencha as credenciais!"
    warn "  nano $APP_DIR/.env"
else
    warn ".env já existe — não sobrescrito."
fi

# =============================================================================
# 7. PERMISSÕES
# =============================================================================
info "Ajustando permissões para www-data..."
chown -R www-data:www-data "$APP_DIR"
chmod -R 755 "$APP_DIR"
chmod 600 "$APP_DIR/.env"
success "Permissões ajustadas."

# =============================================================================
# 8. NGINX — site AXEN
# =============================================================================
info "Configurando nginx para $DOMAIN..."

# Cria pasta para desafio ACME (certbot antes do SSL)
mkdir -p /var/www/certbot

# Copia config provisória (sem SSL) para obter o certificado primeiro
cat > /etc/nginx/sites-available/axen <<'NGINX_TEMP'
server {
    listen 80;
    server_name app.useaxen.com.br;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 "aguardando SSL"; }
}
NGINX_TEMP

# Ativa o site (sem conflito com Tradebit — arquivo separado)
ln -sf /etc/nginx/sites-available/axen /etc/nginx/sites-enabled/axen
nginx -t && systemctl reload nginx
success "nginx configurado (modo temporário sem SSL)."

# =============================================================================
# 9. SSL — Let's Encrypt
# =============================================================================
info "Obtendo certificado SSL para $DOMAIN..."
certbot certonly --nginx \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "admin@useaxen.com.br" \
    --redirect
success "Certificado SSL obtido."

# ── Instala config final do nginx (com SSL + Basic Auth + proxy) ──────────────
cp "$APP_DIR/deploy/nginx-axen.conf" /etc/nginx/sites-available/axen

# Basic Auth
info "Configurando autenticação Basic Auth..."
htpasswd -cb /etc/nginx/.htpasswd-axen "$AUTH_USER" "$AUTH_PASS"
chmod 640 /etc/nginx/.htpasswd-axen
chown root:www-data /etc/nginx/.htpasswd-axen
success "Usuário '$AUTH_USER' criado para acesso ao dashboard."

nginx -t && systemctl reload nginx
success "nginx recarregado com SSL e Basic Auth."

# =============================================================================
# 10. SYSTEMD — serviços
# =============================================================================
info "Instalando serviços systemd..."

cp "$APP_DIR/deploy/axen-api.service"       /etc/systemd/system/axen-api.service
cp "$APP_DIR/deploy/axen-scheduler.service" /etc/systemd/system/axen-scheduler.service

systemctl daemon-reload
systemctl enable axen-api axen-scheduler
systemctl start  axen-api axen-scheduler

sleep 2
success "Serviço API:       $(systemctl is-active axen-api)"
success "Serviço Scheduler: $(systemctl is-active axen-scheduler)"

# =============================================================================
# RESUMO FINAL
# =============================================================================
echo ""
echo "======================================================"
echo -e "  ${GREEN}✓ AXEN Intelligence instalado com sucesso!${NC}"
echo "======================================================"
echo ""
echo "  Dashboard:  https://$DOMAIN"
echo "  Usuário:    $AUTH_USER"
echo "  API docs:   https://$DOMAIN/api/docs"
echo ""
echo "  Próximos passos:"
echo "  1. Edite o .env com suas credenciais:"
echo "     nano $APP_DIR/.env"
echo "  2. Reinicie a API após editar o .env:"
echo "     systemctl restart axen-api"
echo "  3. Para ver os logs:"
echo "     journalctl -u axen-api -f"
echo "     journalctl -u axen-scheduler -f"
echo ""
echo "  Para atualizar o sistema no futuro:"
echo "     bash $APP_DIR/deploy/update.sh"
echo ""
