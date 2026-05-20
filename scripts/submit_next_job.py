import numpy as np
from purohit.reanalyze import PERerun

pe = PERerun(project_dir="/home/vaishak.prasad/Projects/ligo/rean",
             working_dir="/home/pe.o4/GWTC4/working",)

pe.run()

#pe.submit_one_job("S230601bf")

print("Submitted jobs", pe.submitted_jobs, len(pe.submitted_jobs))
print("Pending jobs", pe.pending_jobs, len(pe.pending_jobs))

pe.submit_next_job()
