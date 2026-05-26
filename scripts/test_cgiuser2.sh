cat > ~/public_html/cgi-bin/write-test.cgi <<'EOF'
#!/usr/bin/env bash
echo "Content-Type: text/plain"
echo
echo "id:"
id
echo
echo "hostname:"
hostname -f
echo
echo "mount info:"
findmnt -T /home/vaishak.prasad/public_html 2>&1
findmnt -T /home/vaishak.prasad/Projects/ligo/rean5/control 2>&1
echo
echo "path info:"
namei -l /home/vaishak.prasad/Projects/ligo/rean5/control 2>&1
echo
echo "write tests:"
touch /home/vaishak.prasad/Projects/ligo/rean5/control/test-write-project 2>&1 \
  && echo "project control write: OK" \
  || echo "project control write: FAIL"
touch /tmp/purohit-cgi-test-${USER:-nouser} 2>&1 \
  && echo "/tmp write: OK" \
  || echo "/tmp write: FAIL"
EOF

chmod 755 ~/public_html/cgi-bin/write-test.cgi
