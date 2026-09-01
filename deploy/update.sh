#!/bin/bash
# =============================================================================
# AXEN Intelligence — Script de atualização (deploy de nova versão)
# Execute no servidor como root: bash /var/www/axen/deploy/update.sh
# =============================================================================
set -e

APP_DIR="/var/www/axen"
GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }

echo ""
echo "======================================"
echo "  AXEN Intelligence — Atualizando..."
echo "======================================"
echo ""

cd "$APP_DIR"

# ── 1. Baixar código novo ─────────────────────────────────────────────────────
info "Baixando atualizações do GitHub..."
git pull origin main
success "Código atualizado."

# ── 2. Dependências Python ────────────────────────────────────────────────────
info "Atualizando dependências Python..."
source venv/bin/activate
pip install --quiet -r requirements.txt
success "Dependências Python OK."

# ── 3. Build do frontend ──────────────────────────────────────────────────────
info "Reconstruindo frontend..."
cd frontend
npm install --silent
npm run build
cd ..
success "Frontend reconstruído."

# ── 4. Permissões ─────────────────────────────────────────────────────────────
chown -R www-data:www-data "$APP_DIR/frontend/dist"

# ── 5. Reiniciar serviços ─────────────────────────────────────────────────────
info "Reiniciando serviços..."
systemctl restart axen-api
systemctl restart axen-scheduler
sleep 2

echo ""
echo "======================================"
echo -e "  ${GREEN}✓ Atualização concluída!${NC}"
echo "======================================"
echo "  API:       $(systemctl is-active axen-api)"
echo "  Scheduler: $(systemctl is-active axen-scheduler)"
echo ""
