"""The tool is only summarizing if the runtime actually hands it the pieces.

Wiring is exactly the kind of thing that looks done and silently is not: the
tool degrades to the bare hit list when backend/settings are missing, which is
indistinguishable from "summarization is off" unless something asserts it.
"""
import inspect

import novelizer.runtime as runtime_mod


def test_phase_a_toolkit_passes_backend_settings_and_callbacks():
    src = inspect.getsource(runtime_mod.Runtime._phase_a_toolkit)
    assert "backend=backend" in src
    assert "settings_provider=lambda: self.settings" in src
    assert "callbacks=self._llm_callbacks" in src
