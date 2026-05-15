import htcondor2 as htcondor

def get_condor_job_status(cluster_id: int, proc_id: int) -> str:
    """
    Reads the status of a specific HTCondor job and returns it as a string.

    Args:
        cluster_id: The cluster ID of the job.
        proc_id: The process ID of the job.

    Returns:
        A string representing the job's status, or an error message if the job
        is not found.
    """
    try:
        # Get a reference to the HTCondor schedd daemon
        schedd = htcondor.Schedd()
        
        # Define a constraint to find the specific job
        constraint = f"ClusterId == {cluster_id} && ProcId == {proc_id}"
        
        # Query the schedd for the job's ClassAd (a dictionary-like object)
        # We only need the 'JobStatus' attribute for the projection
        job_ad = schedd.query(
            constraint=constraint,
            projection=["JobStatus"]
        )

        if not job_ad:
            return None# "Job not found in the queue."
            
        # The query returns a list of matching jobs. We expect one result.
        status_code = job_ad[0]["JobStatus"]

        # Map the numeric status code to a human-readable string
        # See HTCondor documentation for a complete list of codes
        # 1: Idle, 2: Running, 3: Completed, 4: Removed, 5: Held, 6: Suspended
        status_map = {
            1: "idle",
            2: "running",
            3: "completed",
            4: "removed",
            5: "held",
            6: "suspended",
        }
        return status_map.get(status_code, None)#"Unknown status")
        
    except htcondor.HTCondorIOError as e:
        return None#f"Error connecting to the HTCondor schedd: {e}"
    except Exception as e:
        return None#f"An unexpected error occurred: {e}"


