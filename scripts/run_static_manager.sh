cd /home/vaishak.prasad/Projects/Codes/purohit

python scripts/run_static_manager_with_mailbox.py \
  --project-dir /home/vaishak.prasad/Projects/ligo/rean5 \
  --webdir /home/vaishak.prasad/public_html/monitor \
  --mailbox-url https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/cgi-bin/purohit_mailbox.cgi \
  --token-file /home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt \
  --interval 60 \
  --plot-interval 300 \
  --mailbox-status-probes 3 \
  --env-mode redacted
