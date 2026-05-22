TOKEN=$(cat /home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt)

curl -s \
  -X POST \
  -H "Content-Type: application/json" \
  -H "X-Purohit-Token: ${TOKEN}" \
  -d '{"mode":"status"}' \
  https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/cgi-bin/purohit_mailbox.cgi
