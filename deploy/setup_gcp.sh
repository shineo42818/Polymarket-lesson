#!/bin/bash
# ============================================================
# GCP VM Setup for Polymarket Arb Bot
# ============================================================
#
# PREREQUISITES:
#   1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. Login: gcloud auth login
#   3. Set project: gcloud config set project YOUR_PROJECT_ID
#
# USAGE:
#   bash deploy/setup_gcp.sh
#
# This script:
#   1. Creates an e2-small VM in us-east4-a (Northern Virginia)
#   2. Opens port 8000 for dashboard access
#   3. Prints SSH + deployment instructions
# ============================================================

set -e

# ── Configuration ──
VM_NAME="polymarket-bot"
ZONE="us-east4-a"
MACHINE_TYPE="e2-small"
IMAGE_FAMILY="debian-12"
IMAGE_PROJECT="debian-cloud"
DISK_SIZE="20GB"

echo "============================================================"
echo "  Creating GCP VM for Polymarket Arb Bot"
echo "============================================================"
echo ""
echo "  VM:       $VM_NAME"
echo "  Zone:     $ZONE (Northern Virginia -- closest to Polymarket servers)"
echo "  Machine:  $MACHINE_TYPE (2 vCPU, 2 GB RAM, ~\$14/month)"
echo "  Disk:     $DISK_SIZE standard"
echo ""

# ── Step 1: Create firewall rule ──
echo "[1/3] Creating firewall rule for port 8000..."
gcloud compute firewall-rules create allow-bot-dashboard \
  --allow=tcp:8000 \
  --target-tags=bot-server \
  --description="Allow Polymarket bot dashboard" \
  --quiet 2>/dev/null || echo "  (firewall rule already exists)"

# ── Step 2: Create VM ──
echo "[2/3] Creating VM..."
gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family="$IMAGE_FAMILY" \
  --image-project="$IMAGE_PROJECT" \
  --boot-disk-size="$DISK_SIZE" \
  --tags=bot-server \
  --metadata=startup-script='#!/bin/bash
    apt-get update -qq
    apt-get install -y -qq python3.11 python3.11-venv python3-pip git
  '

# ── Step 3: Get external IP ──
echo ""
echo "[3/3] Getting VM external IP..."
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" \
  --format="get(networkInterfaces[0].accessConfigs[0].natIP)")

echo ""
echo "============================================================"
echo "  VM CREATED SUCCESSFULLY!"
echo "============================================================"
echo ""
echo "  External IP:  $EXTERNAL_IP"
echo "  Dashboard:    http://$EXTERNAL_IP:8000"
echo ""
echo "  NEXT STEPS:"
echo ""
echo "  1. SSH into the VM:"
echo "     gcloud compute ssh $VM_NAME --zone=$ZONE"
echo ""
echo "  2. Clone your code (or upload via scp):"
echo "     # Option A: git clone"
echo "     git clone YOUR_REPO_URL ~/polymarket"
echo ""
echo "     # Option B: upload from local machine (run from YOUR computer):"
echo "     gcloud compute scp --recurse --zone=$ZONE \\"
echo "       src/ requirements.txt .env $VM_NAME:~/polymarket/"
echo ""
echo "  3. Set up Python venv on the VM:"
echo "     cd ~/polymarket"
echo "     python3.11 -m venv venv"
echo "     source venv/bin/activate"
echo "     pip install -r requirements.txt"
echo ""
echo "  4. Create .env file:"
echo "     nano ~/polymarket/.env"
echo "     # Add: BOT_MODE=PAPER"
echo ""
echo "  5. Start the bot:"
echo "     # Manual (for testing):"
echo "     cd ~/polymarket"
echo "     source venv/bin/activate"
echo "     uvicorn src.bot.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "     # Or install as systemd service (auto-restart):"
echo "     sudo cp deploy/bot.service /etc/systemd/system/"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable bot"
echo "     sudo systemctl start bot"
echo ""
echo "  6. Open dashboard in browser:"
echo "     http://$EXTERNAL_IP:8000"
echo ""
echo "  COST: ~\$14/month (\$300 credit = ~21 months)"
echo "============================================================"
