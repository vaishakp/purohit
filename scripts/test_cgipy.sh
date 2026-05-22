cat > ~/public_html/cgi-bin/test.py <<'EOF'
#!/usr/bin/env python3
import os, socket, time
print("Content-Type: text/plain")
print()
print("Python CGI works")
print("USER=", os.environ.get("USER"))
print("HOST=", socket.gethostname())
print("TIME=", time.time())
EOF

chmod 755 ~/public_html/cgi-bin/test.py
