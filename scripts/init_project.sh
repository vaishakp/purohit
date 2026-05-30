#!/usr/bin/env bash

python scripts/init_project.py \
  --hosts $PUROHIT_REPO/scripts/hosts.yaml \
  --source-host cit \
  --source-dir /home/pe.o4/GWTC5-HLV/project/working/ \
  --project-dir $HOME/Projects/ligo/run1 \
  --apx IMRPhenomXPHM \
  --approvals-yaml $PUROHIT_REPO/approved_runs.json \
  --token-file "$TOKEN_FILE" \
