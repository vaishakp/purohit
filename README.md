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

## Acknowledgements

The packaging work in this branch builds on the packaging effort proposed by @chungyinleo in #8, while preserving the existing `reanalyze/` source layout.
