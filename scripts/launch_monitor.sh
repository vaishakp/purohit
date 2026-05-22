while true; do
  python scripts/publish_web_monitor.py \
    --project-dir /home/vaishak.prasad/Projects/ligo/rean5 \
    --webdir /home/vaishak.prasad/public_html/monitor \
    --once

  chmod 644 /home/vaishak.prasad/public_html/monitor/index.html \
            /home/vaishak.prasad/public_html/monitor/status.json

  sleep 300
done
