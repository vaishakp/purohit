mkdir -p /home/vaishak.prasad/public_html/cgi-bin

python scripts/install_cgi_mailbox_ingress.py \
  --spool-root /var/tmp \
  --mailbox-name purohit-vaishak-rean5 \
  --cgi-path /home/vaishak.prasad/public_html/cgi-bin/purohit_mailbox.cgi \
  --token-file /home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt \
  --repo-root /home/vaishak.prasad/Projects/Codes/purohit \
  --python-executable python3

chmod 711 /home/vaishak.prasad
chmod 755 /home/vaishak.prasad/public_html
chmod 755 /home/vaishak.prasad/public_html/cgi-bin
chmod 755 /home/vaishak.prasad/public_html/cgi-bin/purohit_mailbox.cgi
