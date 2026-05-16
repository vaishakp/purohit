import numpy as np
import subprocess
import os
from subprocess import PIPE, run as sprun
from pathlib import Path
import yaml
import pandas as pd
from reanalyze.utils import get_condor_job_status
from waveformtools.waveformtools import message
from tqdm import tqdm
import shutil

class PERerun:

    def __init__(self,
                 working_dir,
                 project_dir,
                 apx='NRSur7dq4',
                 approvals={}):

        self.working_dir = Path(working_dir)
        self.project_dir = Path(project_dir)
        self.apx = apx
        self.approvals=approvals

        if not os.path.exists(self.project_dir):
            os.makedirs(self.project_dir)

        self.submitted_jobs_list_file = self.project_dir/"submitted_jobs.txt"
        
        if not os.path.isfile(self.submitted_jobs_list_file):
            self.load=False

            with open(self.submitted_jobs_list_file, "w") as file:
                pass
    
    def event_dir(self, event):
        return Path(os.path.dirname(self.config_paths[event]))

    def run_cmd(self,
            command, 
            shell=True, 
            capture_output=True, 
            check=True, 
            text=True):

        try:
            out = subprocess.run(command, shell=shell, capture_output=capture_output, check=check, text=text)
        except subprocess.CalledProcessError as e:
            print(f"sed command: \n {command} \n failed!")
            print(f"Exit code: {e.returncode}")

        return out
    

    def find_bilby_configs(self,  
                          ):
        

        message("Running find...", message_verbosity=2)
        print("Running find")
        command = f'find {self.working_dir} -type f -iname "*{self.apx}*.ini" '
        output = self.run_cmd(command, shell=True, capture_output=True, text=True)
        files = output.stdout.split('\n')[:-1]
        event_dict = {}
        events = []

        message("Parsing event names", message_verbosity=2)
        for item in files:
            event_files = []
            event_name = item.split('/')[5]
            #print(event_name)
            events.append(event_name)

        events = set(events)
        event_sdict = {}
        event_dict = {}

        message("Finding configs", message_verbosity=2)

        for idx, event in tqdm(enumerate(events)):
            event_files = sorted([item for item in files if event in item])

            if event in self.approvals.keys():
                tfile = self.approvals[event]
                fil_files = [item for item in event_files if tfile in item]
                if len(fil_files)>1:
                    message(f"Found {len(fil_files)} approved files for {event}", message_verbosity=2)
                event_file = fil_files[0]

                message(f"Choosing {event_file} for {event}", message_verbosity=2)
            else:
                event_file = event_files[0]

            event_dict.update({event: event_file})
            event_sdict.update({event: event_files})

        return event_dict


    def copy_inis(self):

        all_outs = {}
        config_paths = {}
        project_dir = Path(self.project_dir)
        working_dir = project_dir/"working"
        
        if not os.path.isdir(working_dir):
            os.mkdir(working_dir)

        for key, val in self.source_dict.items():
            event_dir = working_dir/key

            if not os.path.isdir(event_dir):
                os.mkdir(event_dir)
            
            filename = os.path.basename(val)

            if not self.load:
                command = f"cp {val} {event_dir}/"
                output = subprocess.run(command, shell=True,  capture_output=True, text=True)
                all_outs.update({key : output})
            config_paths.update({key : Path(f"{event_dir}/{filename}")})

        return all_outs, config_paths

    def reconfigure_one_ini(self, event):
        ''' reconfigure one ini
            
            Changes:
            1. label
            2. outdir
            3. webdir
            4. accounting user
            5. Priors
            '''
            
        to_change = ["label" ,
                    "accounting-user",
                    "outdir",
                    "webdir",
                    "request-memory", 
                    "request-disk",
                    "analysis-executable",
                    "prior-dict",
                    "sampler-kwargs"
                    ]

        config_path = self.config_paths[event]
        print(config_path)
        user = os.getcwd().split('/')[2]
        webdir = f"/home/{user}/public_html/rean/{event}"
        outdir = f"{os.path.dirname(config_path)}/pe"
        print(outdir)
        command = f"sed -i '/^label/c\\label={event}_p2' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^accounting-user/c\\accounting-user={user}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^outdir/c\\outdir={outdir}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^webdir/c\\webdir={webdir}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^request-memory=/c\\request-memory=8' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^request-disk/c\\request-disk=16' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        bilby_pipe_analysis_path = shutil.which("bilby_pipe_analysis")
        command = f"sed -i '/^analysis-executable=/c\\analysis-executable={bilby_pipe_analysis_path}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^submit=/c\\submit=condor' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^additional-transfer-paths=/c\\additional-transfer-paths=[\/scratch\/lalsimulation/NRSur7dq4_v1.0.h5]' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)

        cmd = [
            "sed", "-Ei",
            r"s/a_1[[:space:]]*=[[:space:]]*Uniform[[:space:]]*\([[:space:]]*name[[:space:]]*=[[:space:]]*'a_1',[[:space:]]*minimum[[:space:]]*=[[:space:]]*0,[[:space:]]*maximum[[:space:]]*=[[:space:]]*0\.99[[:space:]]*\)/a_1 = PowerLaw(name='a_1', minimum=0, maximum=1, alpha=2)/g",
            config_path
            ] 

        out = self.run_cmd(cmd, shell=False)
        # print(out)
        cmd = [
            "sed", "-Ei",
            r"s/[[:space:]]*a_2[[:space:]]*=[[:space:]]*Uniform[[:space:]]*\([[:space:]]*name[[:space:]]*=[[:space:]]*'a_2',[[:space:]]*minimum[[:space:]]*=[[:space:]]*0,[[:space:]]*maximum[[:space:]]*=[[:space:]]*0\.99[[:space:]]*\)/ a_2 = PowerLaw(name='a_2', minimum=0, maximum=1, alpha=2)/g",
            config_path
            ]
        
        out = self.run_cmd(cmd, shell=False)
        sampler_kwargs = "sampler-kwargs={'nlive': 2000, 'naccept': 60, 'check_point_plot': True, 'check_point_delta_t': 1800, 'print_method': 'interval-60', 'sample': 'acceptance-walk', 'npool': 16, 'dlogz': 0.01}"
        command = f"sed -i '/^sampler-kwargs/c\\{sampler_kwargs}' {config_path}"
        out = self.run_cmd(command)



    def prepare_configs(self, 
                        working_dir="/home/pe.o4/GWTC4/working", 
                        apx='NRSur7dq4'):

        message("Running find configs", message_verbosity=2)
        self.source_dict = self.find_bilby_configs()
        message("Copying inis", message_verbosity=2)
        outs, config_paths = self.copy_inis()
        self.config_paths = config_paths

    
    def reconfigure(self):
        if not self.load:
            for event, config_path in self.config_paths.items():
                self.reconfigure_one_ini(event)

    def read_job_status(self, event):
         
        job_file = Path(os.path.dirname(self.config_paths[event]))/"status.yaml"

        if not os.path.isfile(job_file):
            status = 'pending'
            jobid = None

        else:
            with open(job_file, 'r') as file:
                info = yaml.safe_load(file)        

            if 'status' in info.keys():
                status = info[status]
            else:
                status = 'unknown'
        
            if 'jobid' in info.keys():
                jobid = info['jobid']

        return status, jobid

    def all_job_status(self):
        
        status_dict = {}
        for key in self.config_paths.keys():

            status, jobid = self.read_job_status(key)
            status = self.query_job_status(key, jobid)
            status_dict.update({key : {'status': status, 'jobid': jobid}})

        df= pd.DataFrame(status_dict).T
        print(df)

        self.file_jobs_statuses = df

        return df

    def add_to_submitted_jobs_list(self, event):
        
        with open(self.submitted_jobs_list_file, "a") as file:
        # This writes the list of strings to the file.
            file.write(f"{event}\n")
    
    def parse_submitted_jobs_list(self):
        
        with open(self.submitted_jobs_list_file, "r") as file:
        # This writes the list of strings to the file.
            sub_jobs = file.readlines()

        self.submitted_jobs = [item.strip("\n") for item in sub_jobs]
        self.pending_jobs = [item for item in self.config_paths.keys() if item not in self.submitted_jobs]

        return sub_jobs
    
    def query_job_status(self, event, jobid):
        self.parse_submitted_jobs_list()

        if event not in self.submitted_jobs:
            status = "pending"
        else:
            #try:
            status = get_condor_job_status(jobid, 0)
            print(jobid, status)
            #except:
            #    status = None
        
        if status is None:
            status = self.check_for_completion(event)

        return status

    def update_job_status_file(self, event, info):
        job_file = Path(os.path.dirname(self.config_paths[event]))/"status.yaml"

        if not os.path.isfile(job_file):
            with open(job_file, 'w') as file:
                yaml.dump({}, file)
        
        with open(job_file, 'r') as file:
            status = yaml.safe_load(file)
        
        status.update(info)
        
        with open(job_file, 'w') as file:
            yaml.safe_dump(status, file, sort_keys=False)
            
    def check_for_completion(self, event):

        final_results_dir = self.event_dir(event)/"pe/final_result"
        files = os.listdir(final_results_dir)

        if not files:
            status = "incomplete"
        else:
            file = files[0]
            if 'hdf5' in file:
                status = 'completed'
            else:
                status = 'incomplete'
            

        return status
    
    def submit_one_job(self, event):
        
        self.parse_submitted_jobs_list()
        if event not in self.submitted_jobs:
            conf_file = self.config_paths[event]
            command = ["bilby_pipe", conf_file, "--submit"]
            out = self.run_cmd(command, shell=False)
            stdout = out.stdout
            jobid = stdout.split("\n")[1].split(" ")[-1][:-1]
            self.add_to_submitted_jobs_list(event)
            self.update_job_status_file(event, {"jobid": jobid})
        else:
            print(f"Job {event} previosly submitted")
        return out

    def submit_next_job(self):

        self.parse_submitted_jobs_list()

        event = self.pending_jobs[0]
        self.submit_one_job(event)
        print(f"Submitted {event}")

    def submit_jobs(self, njobs=1):
        
        self.parse_submitted_jobs_list()

        for idx in range(njobs):
            event = self.pending_jobs[idx]
            self.submit_one_job(event)
            print(f"Submitted {event}")

    def load(self):
        self.find_bilby_configs()

    def run(self):

        self.prepare_configs()
        self.reconfigure()
        self.parse_submitted_jobs_list()
        status = self.all_job_status()

        return status
