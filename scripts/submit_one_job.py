import numpy as np
import json

with open("approved_runs.json", "r") as f:
    app_dict = json.load(f)


from reanalyze.reanalyze import PERerun

pe = PERerun(   approvals=app_dict,
                project_dir="/home/vaishak.prasad/Projects/ligo/rean",
             working_dir="/home/pe.o4/GWTC4/working",
             apx='IMRPhenomXPHM')

pe.run()

pe.submit_one_job("S230601bf")
#pe.submit_next_job()
