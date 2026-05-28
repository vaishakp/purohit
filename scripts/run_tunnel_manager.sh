export PROJECT_DIR=$HOME/Projects/ligo/GWTC5-HLV-purohit
export WEBDIR=$PROJECT_DIR/web

mkdir -p "$WEBDIR"

python scripts/run_tunnel_manager.py \
  --project-dir "$PROJECT_DIR" \
  --webdir "$WEBDIR" \
  --host 127.0.0.1 \
  --port 8766 \
  --token-file "$PROJECT_DIR/control/tunnel_token.txt" \
  --interval 10 \
  --plot-interval 300 \
  --env-mode redacted
