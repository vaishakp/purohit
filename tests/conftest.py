"""Test fixtures and import stubs for optional runtime dependencies."""

from __future__ import annotations

import sys
import types


# The package imports htcondor2 and waveformtools at module import time. These
# packages are expected on the target LIGO/HTCondor environment but are not
# needed for the unit tests below, so we provide minimal stubs for CI.
htcondor2 = types.ModuleType("htcondor2")


class HTCondorIOError(Exception):
    """Stub exception matching htcondor2.HTCondorIOError."""


class Schedd:
    """Stub schedd object; individual tests monkeypatch job status directly."""

    def query(self, *args, **kwargs):
        return []


htcondor2.HTCondorIOError = HTCondorIOError
htcondor2.Schedd = Schedd
sys.modules.setdefault("htcondor2", htcondor2)

waveformtools = types.ModuleType("waveformtools")
waveformtools_submodule = types.ModuleType("waveformtools.waveformtools")


def message(*args, **kwargs):
    """No-op replacement for waveformtools.waveformtools.message."""


waveformtools_submodule.message = message
waveformtools.waveformtools = waveformtools_submodule
sys.modules.setdefault("waveformtools", waveformtools)
sys.modules.setdefault("waveformtools.waveformtools", waveformtools_submodule)
