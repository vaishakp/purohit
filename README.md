# purohit

Utilities for preparing, submitting, and monitoring `bilby_pipe` reruns.

## Installation

### pip / editable install

From the repository root:

```bash
python -m pip install -e .
```

This preserves the existing package layout. The import path remains:

```python
from reanalyze.reanalyze import PERerun
```

### conda environment

Create a conda environment with the runtime dependencies available on conda-forge:

```bash
conda env create -f conda-environment.yml
conda activate purohit
python -m pip install -e .
```

Some deployment-specific dependencies, such as HTCondor and `bilby_pipe`, are expected to be installed in the target LIGO/HTCondor environment when needed.

## Testing and continuous integration

GitHub Actions runs the automated test suite on every pull request and push. The workflow runs on `ubuntu-latest` with Python 3.10, 3.11, and 3.12. For each Python version, CI checks out the repository, installs the packages listed in `requirements-test.txt`, sets `PYTHONPATH=.`, and runs:

```bash
pytest -q
```

The tests currently focus on the `PERerun` workflow, including:

- discovery of bilby_pipe config files under event directories;
- approval-token config selection and fallback behavior;
- copying selected INI files into the project working directory;
- reading and updating `status.yaml` files;
- detecting completed jobs from `pe/final_result` outputs;
- handling empty pending-job queues and invalid submission counts;
- parsing Condor cluster IDs from `bilby_pipe --submit` output;
- recording submitted events in `submitted_jobs.txt`;
- persisting queried job statuses back to event status files.

The CI tests do not require a live HTCondor installation or the full target LIGO runtime environment. Test fixtures provide minimal stubs for optional runtime-only imports such as `htcondor2` and `waveformtools`; tests that need Condor status behavior monkeypatch it directly.

To run the same tests locally from the repository root:

```bash
python -m pip install -r requirements-test.txt
PYTHONPATH=. pytest -q
```

## Acknowledgements

The packaging work in this branch builds on the packaging effort proposed by @chungyinleo in #8, while preserving the existing `reanalyze/` source layout.
