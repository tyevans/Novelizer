"""Exercises scripts/verify_install.sh's own status/exit-code logic.

The script's happy path performs a real, networked `uv tool install .` — too slow
and non-hermetic to run on every CI invocation, which is why nothing has ever
invoked the script automatically (see the coverage gap this file closes).

Instead of running the real installer, these tests put a fake `uv` executable
first on PATH. The fake stands in for `uv tool install`/`uv tool uninstall`
and, on "install", drops a fake `novelizer` binary into $UV_TOOL_BIN_DIR whose
--help/voices behavior is controlled by env vars per test. This lets us drive
the script through its actual bash control flow — including branches that
have never been exercised against their own FAIL paths:

  * `uv tool install` "succeeding" without producing a binary
  * `novelizer --help` exiting non-zero
  * `novelizer --help` missing an expected subcommand name
  * `novelizer voices` emitting a Python traceback

and confirm the fully-successful path still exits 0, so the script is now
covered by an automated, hermetic, sub-second test rather than only by manual
runs.
"""
import os
import stat
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "verify_install.sh")


def _write_executable(path, contents):
    with open(path, "w") as f:
        f.write(contents)
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_uv(fake_bin_dir, install_exit=0, create_binary=True):
    """A stand-in `uv` that fakes `tool install`/`tool uninstall` without
    touching the network or the real uv tool store.
    """
    binary_src = os.path.join(fake_bin_dir, "novelizer_impl")
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -u
        if [ "$1" = "tool" ] && [ "$2" = "install" ]; then
            if [ "{1 if create_binary else 0}" = "1" ]; then
                mkdir -p "$UV_TOOL_BIN_DIR"
                cp "{binary_src}" "$UV_TOOL_BIN_DIR/novelizer"
                chmod +x "$UV_TOOL_BIN_DIR/novelizer"
            fi
            exit {install_exit}
        elif [ "$1" = "tool" ] && [ "$2" = "uninstall" ]; then
            exit 0
        fi
        exit 1
        """
    )
    _write_executable(os.path.join(fake_bin_dir, "uv"), script)


def _fake_novelizer_binary(
    fake_bin_dir,
    help_exit=0,
    help_output="seed chapters read retcons voices autonomy",
    voices_exit=0,
    voices_output="ok",
):
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        if [ "${{1:-}}" = "--help" ]; then
            cat <<'EOF'
{help_output}
EOF
            exit {help_exit}
        elif [ "${{1:-}}" = "voices" ]; then
            cat <<'EOF'
{voices_output}
EOF
            exit {voices_exit}
        fi
        exit 0
        """
    )
    _write_executable(os.path.join(fake_bin_dir, "novelizer_impl"), script)


def _run_script(fake_bin_dir):
    env = dict(os.environ)
    env["PATH"] = fake_bin_dir + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["bash", SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_fully_successful_run_exits_zero(tmp_path):
    fake_bin_dir = str(tmp_path / "fakebin")
    os.makedirs(fake_bin_dir)
    _fake_uv(fake_bin_dir)
    _fake_novelizer_binary(fake_bin_dir)

    result = _run_script(fake_bin_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: install path verified" in result.stdout


def test_fails_when_install_produces_no_binary(tmp_path):
    fake_bin_dir = str(tmp_path / "fakebin")
    os.makedirs(fake_bin_dir)
    _fake_uv(fake_bin_dir, create_binary=False)
    _fake_novelizer_binary(fake_bin_dir)  # unused; no binary lands in bin dir

    result = _run_script(fake_bin_dir)

    assert result.returncode != 0
    assert "expected binary not found" in (result.stdout + result.stderr)


def test_fails_when_help_exits_nonzero(tmp_path):
    fake_bin_dir = str(tmp_path / "fakebin")
    os.makedirs(fake_bin_dir)
    _fake_uv(fake_bin_dir)
    _fake_novelizer_binary(fake_bin_dir, help_exit=1)

    result = _run_script(fake_bin_dir)

    assert result.returncode != 0
    assert "novelizer --help exited 1" in (result.stdout + result.stderr)


def test_fails_when_help_output_missing_expected_command(tmp_path):
    fake_bin_dir = str(tmp_path / "fakebin")
    os.makedirs(fake_bin_dir)
    _fake_uv(fake_bin_dir)
    # Drop "voices" and "autonomy" from the advertised command list.
    _fake_novelizer_binary(fake_bin_dir, help_output="seed chapters read retcons")

    result = _run_script(fake_bin_dir)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "missing expected command 'voices'" in combined
    assert "missing expected command 'autonomy'" in combined


def test_fails_when_voices_produces_traceback(tmp_path):
    fake_bin_dir = str(tmp_path / "fakebin")
    os.makedirs(fake_bin_dir)
    _fake_uv(fake_bin_dir)
    _fake_novelizer_binary(
        fake_bin_dir,
        voices_output=(
            "Traceback (most recent call last):\n"
            '  File "novelizer", line 1, in <module>\n'
            "RuntimeError: boom"
        ),
        voices_exit=1,
    )

    result = _run_script(fake_bin_dir)

    assert result.returncode != 0
    assert "unhandled traceback" in (result.stdout + result.stderr)


def test_passes_when_voices_exits_nonzero_without_traceback(tmp_path):
    """A handled CLI failure (non-zero exit, no traceback) is documented in the
    script as acceptable per plan, not a FAIL — confirm that branch too.
    """
    fake_bin_dir = str(tmp_path / "fakebin")
    os.makedirs(fake_bin_dir)
    _fake_uv(fake_bin_dir)
    _fake_novelizer_binary(
        fake_bin_dir,
        voices_output="Error: no story configured",
        voices_exit=2,
    )

    result = _run_script(fake_bin_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK (handled failure, exit 2, no traceback)" in result.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="script is bash/linux-oriented")
def test_uninstall_is_attempted_on_failure_paths(tmp_path):
    """Regression guard for the trap-based cleanup: even when the run fails
    after install, uninstall must still be invoked (installed=1 branch).
    """
    fake_bin_dir = str(tmp_path / "fakebin")
    os.makedirs(fake_bin_dir)
    _fake_uv(fake_bin_dir)
    _fake_novelizer_binary(fake_bin_dir, help_exit=1)

    result = _run_script(fake_bin_dir)

    assert "Cleaning up: uv tool uninstall novelizer" in result.stdout
