from reanalyze.reanalyze import PERerun
import json

with open("approved_runs.json", "r") as f:
    app_dict = json.load(f)


pe = PERerun(
    approvals=app_dict,
    source_dir="/home/pe.o4/GWTC5-HLV/project/working",
    project_dir="/home/vaishak.prasad/Projects/ligo/rean5",
    apx="IMRPhenomXPHM",
    accounting="ligo.dev.o4.cbc.pe.bilby",
    accounting_user="auto",
    reconfigure_existing_configs=True,
)

pe.run()

print("Submitted jobs", pe.submitted_jobs, len(pe.submitted_jobs))
print("Pending jobs", pe.pending_jobs, len(pe.pending_jobs))

pe.submit_next_job()
