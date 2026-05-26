mkdir -p /home/vaishak.prasad/Projects/ligo/rean5/control

python - <<'PY'
import secrets
from pathlib import Path

path = Path("/home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt")
path.write_text(secrets.token_urlsafe(32) + "\n")
path.chmod(0o600)
print(path)
PY
