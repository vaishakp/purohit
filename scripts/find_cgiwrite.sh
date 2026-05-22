cat > ~/public_html/cgi-bin/find-writable.cgi <<'EOF'
#!/usr/bin/env bash
echo "Content-Type: text/plain"
echo

for d in \
  /tmp \
  /var/tmp \
  /usr1 \
  /usr1/vaishak.prasad \
  /scratch \
  /scratch/vaishak.prasad \
  /local \
  /local/vaishak.prasad \
  /ldas_outgoing \
  /archive \
  /frames
do
  echo "== $d =="
  if [ -d "$d" ]; then
    hostname -f
    findmnt -T "$d" 2>&1
    testfile="$d/purohit-cgi-write-test.$$"
    touch "$testfile" 2>&1 && echo "WRITE OK: $testfile" && rm -f "$testfile" || echo "WRITE FAIL"
  else
    echo "missing"
  fi
  echo
done
EOF

chmod 755 ~/public_html/cgi-bin/find-writable.cgi
