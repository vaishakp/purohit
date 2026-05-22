mkdir -p ~/public_html/cgi-bin

cat > ~/public_html/cgi-bin/test.cgi <<'EOF'
#!/usr/bin/env bash
echo "Content-Type: text/plain"
echo
echo "CGI works"
echo "USER=$USER"
echo "HOST=$(hostname -f)"
date
EOF

chmod 755 ~/public_html ~/public_html/cgi-bin ~/public_html/cgi-bin/test.cgi
chmod 711 ~
