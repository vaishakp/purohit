#!/usr/bin/env bash
set -euo pipefail

# Server-side helper for creating N CE-STM pyRing test jobs and handing them
# to the normal Purohit manifest workflow.  This script deliberately does not
# start a separate pyRing monitor; use scripts/start_cluster_manager.sh and
# scripts/start_laptop_tunnel.sh for the existing Purohit UI.

export CODE="${CODE:-/scratch2/ligo.org/vaishak.prasad/Projects/Codes}"
export PYRING="${PYRING:-$CODE/pyRing}"
export PUROHIT="${PUROHIT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"

export TESTROOT="${TESTROOT:-$HOME/ce_stm_tunnel_test}"
export WORK="${WORK:-$TESTROOT/work}"
export PROJECT_DIR="${PROJECT_DIR:-$TESTROOT/purohit_project}"

export NJOBS="${NJOBS:-10}"
export RESET_TESTROOT="${RESET_TESTROOT:-1}"
export SUBMIT="${SUBMIT:-1}"
export AUTO_GIT_PULL="${AUTO_GIT_PULL:-1}"

export CONDA_SETUP="${CONDA_SETUP:-$HOME/soft/anaconda3/etc/profile.d/conda.sh}"
export CONDA_ENV="${CONDA_ENV:-ce}"

export DETECTORS="${DETECTORS:-CE20}"
export NOISE_CURVE="${NOISE_CURVE:-1p0MW_Aplus}"
export MODELS="${MODELS:-baseline_220}"

export NLIVE="${NLIVE:-64}"
export MAXMCMC="${MAXMCMC:-256}"
export POOLSIZE="${POOLSIZE:-16}"
export REQUEST_CPUS="${REQUEST_CPUS:-4}"
export REQUEST_MEMORY="${REQUEST_MEMORY:-8GB}"
export REQUEST_DISK="${REQUEST_DISK:-8GB}"
export MAX_RUNTIME="${MAX_RUNTIME:-3600}"

export NOISE_EVIDENCE_SAMPLES="${NOISE_EVIDENCE_SAMPLES:-256}"
export NOISE_EVIDENCE_SEED="${NOISE_EVIDENCE_SEED:-12345}"

echo "[pyring-init] host=$(hostname) user=$(whoami)"
echo "[pyring-init] PYRING=$PYRING"
echo "[pyring-init] PUROHIT=$PUROHIT"
echo "[pyring-init] TESTROOT=$TESTROOT"
echo "[pyring-init] PROJECT_DIR=$PROJECT_DIR"
echo "[pyring-init] NJOBS=$NJOBS SUBMIT=$SUBMIT"

if [[ ! -f "$PYRING/pyRing/__init__.py" || ! -d "$PYRING/studies/ce_stm" ]]; then
    echo "[pyring-init] ERROR: PYRING does not point to a pyRing checkout: $PYRING" >&2
    exit 2
fi
if [[ ! -d "$PUROHIT/reanalyze" ]]; then
    echo "[pyring-init] ERROR: PUROHIT does not point to a Purohit checkout: $PUROHIT" >&2
    exit 2
fi

if [[ "$AUTO_GIT_PULL" == "1" && -d "$PYRING/.git" ]]; then
    git -C "$PYRING" pull --ff-only
fi
if [[ "$AUTO_GIT_PULL" == "1" && -d "$PUROHIT/.git" ]]; then
    git -C "$PUROHIT" pull --ff-only
fi

if [[ "$RESET_TESTROOT" == "1" ]]; then
    echo "[pyring-init] removing old test root: $TESTROOT"
    rm -rf "$TESTROOT"
fi
mkdir -p "$WORK" "$PROJECT_DIR"

set +u
source "$CONDA_SETUP"
conda activate "$CONDA_ENV"
set -u

echo "[pyring-init] python=$(which python)"
echo "[pyring-init] pyRing executable=$(which pyRing || true)"

cd "$PYRING"
python -m py_compile pyRing/__init__.py pyRing/xg_truncated_noise_patch.py studies/ce_stm/config_writer.py
python studies/ce_stm/make_pyring_jobs.py --help | grep -q -- "--prior-policy"

CATALOG_CSV="$WORK/tunnel_${NJOBS}_events.csv"
EVENTS_JSONL="$WORK/events_${NJOBS}.jsonl"
python - <<PY
import csv
from pathlib import Path
n = int("$NJOBS")
path = Path("$CATALOG_CSV")
rows = []
for i in range(n):
    rows.append({
        "mass_1_source": 35 + 0.5*i,
        "mass_2_source": 30 + 0.3*i,
        "chi_1z": 0.0,
        "chi_2z": 0.0,
        "redshift": 0.08 + 0.002*i,
        "luminosity_distance_mpc": 370 + 10*i,
        "mf_source": 61.8 + 0.75*i,
        "af": 0.68,
    })
with path.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
print(path)
PY

python studies/ce_stm/catalog_sampler.py \
  --input "$CATALOG_CSV" \
  --output "$EVENTS_JSONL" \
  --n-events "$NJOBS" \
  --seed 1234

PRIOR_POLICY="$WORK/ce_stm_production_free_sky_degenerate_fixed.json"
cat > "$PRIOR_POLICY" <<EOF
{
  "name": "ce-stm-production-free-sky-degenerate-fixed",
  "description": "Production CE-STM prior: sample sky/t0/remnant/modes; fix only free-amplitude Kerr degeneracies.",
  "fix": ["logdistance", "cosiota", "phi"],
  "noise_evidence": {
    "samples": $NOISE_EVIDENCE_SAMPLES,
    "seed": $NOISE_EVIDENCE_SEED
  },
  "Mf": {"min_factor": 0.5, "max_factor": 1.5, "floor": 1.0},
  "af": {"half_width": 0.30, "min": 0.0, "max": 0.99},
  "logdistance": {"fraction": 0.90},
  "cosiota": {"min": -1.0, "max": 1.0},
  "phi": {"min": 0.0, "max": 6.283185307179586},
  "amplitude": {"min": 0.0, "max": 100.0},
  "mode_phase": {"min": 0.0, "max": 6.283185307179586}
}
EOF

RUNS="$WORK/ce_stm_runs_tunnel_prior"
python studies/ce_stm/make_pyring_jobs.py \
  --events "$EVENTS_JSONL" \
  --outdir "$RUNS" \
  --models "$MODELS" \
  --detectors "$DETECTORS" \
  --noise-curve "$NOISE_CURVE" \
  --prior-policy "$PRIOR_POLICY" \
  --nlive "$NLIVE" \
  --maxmcmc "$MAXMCMC" \
  --poolsize "$POOLSIZE" \
  --nthreads "$REQUEST_CPUS" \
  --limit-events "$NJOBS"

CONFIG=$(find "$RUNS/configs" -name '*.ini' | sort | head -1)
echo "[pyring-init] prior check from $CONFIG"
grep -n "prior-policy\|noise-evidence\|fix-ra\|fix-dec\|fix-psi\|fix-t\|fix-logdistance\|fix-cosiota\|fix-phi\|Mf-min\|Mf-max\|af-min\|af-max" "$CONFIG" || true

python - <<PY
import pandas as pd
from pathlib import Path
manifest = Path("$RUNS/manifest.csv")
df = pd.read_csv(manifest)
df["job_id"] = df["event_id"].astype(str) + "__" + df["model"].astype(str)
out = manifest.with_name("manifest_purohit.csv")
df.to_csv(out, index=False)
print("[pyring-init] Purohit manifest:", out)
print(df[["job_id", "config", "output", "prior_policy"]].head(20).to_string(index=False))
PY

cat > "$WORK/setup_pyring_env.sh" <<EOF
#!/usr/bin/env bash
set -eo pipefail
set +u
source "$CONDA_SETUP"
conda activate "$CONDA_ENV"
set -u
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
echo "[job-env] host=\$(hostname)"
echo "[job-env] python=\$(which python)"
echo "[job-env] pyRing=\$(which pyRing)"
EOF
chmod +x "$WORK/setup_pyring_env.sh"

SUBMIT_FLAG=()
if [[ "$SUBMIT" == "1" ]]; then
    SUBMIT_FLAG=(--submit --n-jobs "$NJOBS")
fi

cd "$PUROHIT"
PYTHONPATH="$PUROHIT:$PYTHONPATH" python scripts/submit_manifest_jobs.py \
  --manifest "$RUNS/manifest_purohit.csv" \
  --project-dir "$PROJECT_DIR" \
  --event-column job_id \
  --command-template "pyRing --config-file {config}" \
  --application pyring \
  --request-cpus "$REQUEST_CPUS" \
  --request-memory "$REQUEST_MEMORY" \
  --request-disk "$REQUEST_DISK" \
  --max-runtime "$MAX_RUNTIME" \
  --env-setup "$WORK/setup_pyring_env.sh" \
  --disable-input-staging \
  "${SUBMIT_FLAG[@]}"

echo
echo "[pyring-init] done"
echo "PROJECT_DIR=$PROJECT_DIR"
echo "WORK=$WORK"
cat "$PROJECT_DIR/submitted_jobs.txt" 2>/dev/null || true
