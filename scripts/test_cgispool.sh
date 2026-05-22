cat > ~/public_html/cgi-bin/spool-test.cgi <<'EOF'
#!/usr/bin/env bash
echo "Content-Type: text/plain"
echo

STAMP=$(date +%s)
for d in /tmp /var/tmp; do
  f="$d/purohit-cgi-spool-test-vaishak-${STAMP}.json"
  echo "== $d =="
  echo "{\"host\":\"$(hostname -f)\",\"time\":${STAMP},\"path\":\"$f\"}" > "$f" 2>&1
  if [ -f "$f" ]; then
    chmod 600 "$f"
    echo "WROTE $f"
    cat "$f"
  else
    echo "WRITE FAILED $f"
  fi
  echo
done
EOF

chmod 755 ~/public_html/cgi-bin/spool-test.cgi
