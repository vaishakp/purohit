cat > ~/public_html/cgi-bin/whoami.cgi <<'EOF'
#!/usr/bin/env bash
echo "Content-Type: text/plain"
echo
echo "id:"
id
echo
echo "whoami:"
whoami
echo
echo "pwd:"
pwd
echo
echo "HOME=$HOME"
echo "USER=$USER"
echo "LOGNAME=$LOGNAME"
echo
echo "write tests:"
touch /home/vaishak.prasad/public_html/cgi-bin/test-write-public 2>&1 && echo "public_html write: OK" || echo "public_html write: FAIL"
touch /home/vaishak.prasad/Projects/ligo/rean5/control/test-write-project 2>&1 && echo "project control write: OK" || echo "project control write: FAIL"
EOF

chmod 755 ~/public_html/cgi-bin/whoami.cgi
