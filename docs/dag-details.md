# DAG detail page

Purohit records the top-level DAGMan cluster id for each submitted event in `working/<event>/status.yaml` as `jobid`.

For a DAG submission, `condor_q` may show a top-level DAGMan job, for example:

```text
OWNER           BATCH_NAME                         DONE RUN IDLE TOTAL JOB_IDS
vaishak.prasad  dag_S240413p_p2.submit+4024676    1    1   1    6     4025696.0 ... 4025699.0
```

In this case, the DAGMan cluster id is:

```text
4024676
```

The child jobs are separate Condor clusters/procs and are discovered with a constraint like:

```bash
condor_q -constraint '(ClusterId == 4024676) || (DAGManJobId == 4024676)' -json
```

Purohit now publishes:

```text
status.json          # per-event summary
dag_details.json     # per-event DAGMan and child-job details
dag.html             # browser page for a selected event
```

Clicking an event name from the monitor opens:

```text
dag.html?event=S240413p
```

The page shows the top-level DAG id, live child jobs from `condor_q`, recent child history from `condor_history`, node names when available, runtime, requested resources, hold reasons, and common log/out/err paths from the Condor classads.

This is read-only. Job control continues to use the top-level DAGMan cluster id stored in `status.yaml`.
