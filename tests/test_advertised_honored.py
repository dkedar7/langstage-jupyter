"""Advertised-but-not-honored regressions (gh #112, #121, #131, #139, #144, #145,
#148, #150).

Each test drives the documented surface (the launcher's ``main()``, ``LabConfig``, the
README recipe, the agent wrapper) the way a user reaches it, and asserts that what the
docs or ``--show-config`` advertise is what actually happens.
"""
import json
import math
import os
from pathlib import Path

import pytest

from langstage_jupyter.launcher import DEMO_AGENT_SPEC, main


def _isolate(monkeypatch, tmp_path):
    """No LANGSTAGE_*/DEEPAGENT_* env, no global config, cwd = tmp_path."""
    for key in list(os.environ.keys()):
        if key.startswith(("LANGSTAGE_", "DEEPAGENT_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("JUPYTER_TOKEN", raising=False)
    global_dir = tmp_path / "global"
    global_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(global_dir))
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(global_dir))
    monkeypatch.chdir(tmp_path)


# ── gh #112: the bundled default selected BY ITS SPEC gets the missing-key preflight ──


class TestDefaultAgentBySpec:
    SPEC = "langstage_jupyter.agent:agent"  # the spec .env.example recommends

    def test_predicate_recognizes_the_default_by_spec(self, monkeypatch, tmp_path):
        from langstage_jupyter.config import LabConfig, runs_bundled_default

        _isolate(monkeypatch, tmp_path)
        assert runs_bundled_default(LabConfig.resolve())
        for spec in (self.SPEC, f"  {self.SPEC}  ", "langstage_jupyter.agent:agent"):
            monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", spec)
            assert runs_bundled_default(LabConfig.resolve()), spec
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "./my_agent.py:graph")
        assert not runs_bundled_default(LabConfig.resolve())
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "langstage_jupyter.agent:other")
        assert not runs_bundled_default(LabConfig.resolve())
        monkeypatch.delenv("LANGSTAGE_AGENT_SPEC")
        monkeypatch.setenv("LANGSTAGE_AGENT_MODULE", "langstage_jupyter.agent")
        assert runs_bundled_default(LabConfig.resolve())
        monkeypatch.setenv("LANGSTAGE_AGENT_MODULE", "my_pkg.agent")
        assert not runs_bundled_default(LabConfig.resolve())

    def _no_real_turn(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "langstage_core.agui.verify",
            lambda *a, **k: pytest.fail("core.verify must not run when the key is missing"),
        )

    def test_verify_via_env_spec_names_the_key(self, monkeypatch, tmp_path, capsys):
        _isolate(monkeypatch, tmp_path)
        self._no_real_turn(monkeypatch)
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", self.SPEC)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        out = capsys.readouterr().out
        assert exc.value.code == 1
        assert "ANTHROPIC_API_KEY is not set" in out
        assert "TypeError" not in out

    def test_verify_via_cli_spec_names_the_key(self, monkeypatch, tmp_path, capsys):
        _isolate(monkeypatch, tmp_path)
        self._no_real_turn(monkeypatch)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "-a", self.SPEC, "--verify"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().out

    def test_ask_via_spec_names_the_key(self, monkeypatch, tmp_path, capsys):
        _isolate(monkeypatch, tmp_path)
        self._no_real_turn(monkeypatch)
        monkeypatch.setattr(
            "langstage_jupyter.agent_wrapper.AgentWrapper.load_agent_from_target",
            staticmethod(lambda *a: pytest.fail("the agent must not load when the key is missing")),
        )
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "-a", self.SPEC, "--ask", "hi"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err

    def test_health_via_spec_is_not_ready(self, monkeypatch, tmp_path):
        from langstage_jupyter import config, handlers

        _isolate(monkeypatch, tmp_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", self.SPEC)
        monkeypatch.setattr(config, "_cfg", config.LabConfig.resolve())
        monkeypatch.setattr(config, "MODEL_NAME", config._cfg.model_name)
        assert handlers._missing_default_agent_key() == "ANTHROPIC_API_KEY"


# ── gh #121: --show-config credits -a / --demo to the flag, not to an env var ──


class TestShowConfigAgentSource:
    def _json(self, argv, monkeypatch, tmp_path, capsys):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", *argv, "--show-config", "--json"])
        main()
        return json.loads(capsys.readouterr().out)["config"]["agent_spec"]

    def test_demo_is_credited_to_the_flag(self, monkeypatch, tmp_path, capsys):
        spec = self._json(["--demo"], monkeypatch, tmp_path, capsys)
        assert spec["value"] == DEMO_AGENT_SPEC
        assert spec["source"] == "cli:--demo"
        # --show-config reads the config; it must not leave a fake env var behind.
        assert "LANGSTAGE_AGENT_SPEC" not in os.environ

    def test_agent_flag_is_credited_to_the_flag(self, monkeypatch, tmp_path, capsys):
        spec = self._json(["-a", "./my_agent.py:graph"], monkeypatch, tmp_path, capsys)
        assert spec["value"] == "./my_agent.py:graph"
        assert spec["source"] == "cli:-a/--agent"

    def test_no_flag_env_is_still_env(self, monkeypatch, tmp_path, capsys):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "./x.py:graph")
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--show-config", "--json"])
        main()
        spec = json.loads(capsys.readouterr().out)["config"]["agent_spec"]
        assert spec["source"] == "env:LANGSTAGE_AGENT_SPEC"

    def test_human_table_shows_the_flag(self, monkeypatch, tmp_path, capsys):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--demo", "--show-config"])
        main()
        line = next(ln for ln in capsys.readouterr().out.splitlines() if "agent_spec" in ln)
        assert "[cli:--demo]" in line
        assert "env:LANGSTAGE_AGENT_SPEC]" not in line


# ── gh #131: labextension_version is read from the bundle JupyterLab actually loads ──


class TestLabextensionVersion:
    def _manifest(self, root: Path, version: str) -> Path:
        d = root / "langstage-jupyter"
        d.mkdir(parents=True)
        (d / "package.json").write_text(json.dumps({"name": "langstage-jupyter", "version": version}))
        return root

    def test_reads_the_installed_data_dir_bundle(self, monkeypatch, tmp_path):
        from langstage_jupyter import launcher

        first = self._manifest(tmp_path / "user", "9.9.9")
        second = self._manifest(tmp_path / "env", "1.1.1")
        monkeypatch.setattr(
            "jupyter_core.paths.jupyter_path", lambda *parts: [str(first), str(second)]
        )
        # JupyterLab takes the FIRST labextensions dir that has it; so do we.
        assert launcher._labextension_version() == "9.9.9"

    def test_no_bundle_anywhere_is_null_not_the_python_version(self, monkeypatch, tmp_path):
        from langstage_jupyter import launcher

        monkeypatch.setattr("jupyter_core.paths.jupyter_path", lambda *parts: [str(tmp_path)])
        monkeypatch.setattr(launcher, "_PACKAGE_LABEXTENSION_DIR", tmp_path / "absent")
        assert launcher._labextension_version() is None


# ── gh #139: the launcher honors LANGSTAGE_JUPYTER_TOKEN (canonical wins) ──


class TestLaunchToken:
    def _launch(self, monkeypatch, tmp_path, env):
        _isolate(monkeypatch, tmp_path)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        calls = {}

        def fake_run(cmd, env=None):
            calls["cmd"], calls["env"] = cmd, dict(env or {})

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", fake_run)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--no-browser", "--port", "8990"])
        with pytest.raises(SystemExit):
            main()
        return calls

    def test_canonical_token_is_used(self, monkeypatch, tmp_path):
        calls = self._launch(monkeypatch, tmp_path, {"LANGSTAGE_JUPYTER_TOKEN": "CANON_TOKEN_123456"})
        assert "--IdentityProvider.token=CANON_TOKEN_123456" in calls["cmd"]
        assert calls["env"]["LANGSTAGE_JUPYTER_TOKEN"] == "CANON_TOKEN_123456"

    def test_canonical_beats_jupyter_token(self, monkeypatch, tmp_path):
        calls = self._launch(
            monkeypatch, tmp_path,
            {"LANGSTAGE_JUPYTER_TOKEN": "CANON_TOKEN_123456", "JUPYTER_TOKEN": "BARE_TOKEN_123456"},
        )
        assert "--IdentityProvider.token=CANON_TOKEN_123456" in calls["cmd"]

    def test_legacy_token_is_used_with_a_notice(self, monkeypatch, tmp_path):
        import langstage_core.host.config as core_cfg

        # Core's one-time notice (its stderr line is muted under pytest; the
        # DeprecationWarning it raises alongside is not).
        monkeypatch.setattr(core_cfg, "_warned_legacy_env", set())
        with pytest.warns(DeprecationWarning, match="DEEPAGENT_JUPYTER_TOKEN"):
            calls = self._launch(
                monkeypatch, tmp_path, {"DEEPAGENT_JUPYTER_TOKEN": "LEG_TOKEN_123456"}
            )
        assert "--IdentityProvider.token=LEG_TOKEN_123456" in calls["cmd"]

    def test_bare_jupyter_token_still_works(self, monkeypatch, tmp_path):
        calls = self._launch(monkeypatch, tmp_path, {"JUPYTER_TOKEN": "BARE_TOKEN_123456"})
        assert "--IdentityProvider.token=BARE_TOKEN_123456" in calls["cmd"]

    def test_nothing_set_generates_one(self, monkeypatch, tmp_path):
        calls = self._launch(monkeypatch, tmp_path, {})
        tok = next(a for a in calls["cmd"] if a.startswith("--IdentityProvider.token="))
        assert tok.split("=", 1)[1] not in ("", "12345")


# ── gh #144: an unusable model_temperature degrades to the default with a note ──


class TestModelTemperature:
    def _resolve(self, monkeypatch, tmp_path, temp, model=None):
        from langstage_jupyter.config import LabConfig

        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("LANGSTAGE_MODEL_TEMPERATURE", temp)
        if model:
            monkeypatch.setenv("LANGSTAGE_MODEL_NAME", model)
        return LabConfig.resolve()

    @pytest.mark.parametrize("bad", ["-5", "-0.2", "nan", "inf", "-inf"])
    def test_provider_independent_bad_values(self, monkeypatch, tmp_path, capsys, bad):
        cfg = self._resolve(monkeypatch, tmp_path, bad, model="openai:gpt-4o")
        assert cfg.model_temperature == 0.0
        assert cfg.sources["model_temperature"] == "default"
        assert "model_temperature" in capsys.readouterr().err

    def test_above_anthropic_range(self, monkeypatch, tmp_path, capsys):
        cfg = self._resolve(monkeypatch, tmp_path, "1.5")  # default model is anthropic
        assert cfg.model_temperature == 0.0
        assert cfg.sources["model_temperature"] == "default"
        err = capsys.readouterr().err
        assert "model_temperature" in err and "anthropic" in err
        assert any(i["field"] == "model_temperature" for i in cfg.config_issues())

    def test_bare_claude_name_uses_anthropic_range(self, monkeypatch, tmp_path):
        cfg = self._resolve(monkeypatch, tmp_path, "1.5", model="claude-sonnet-4-5")
        assert cfg.model_temperature == 0.0

    def test_openai_accepts_up_to_two(self, monkeypatch, tmp_path):
        cfg = self._resolve(monkeypatch, tmp_path, "1.5", model="openai:gpt-4o")
        assert cfg.model_temperature == 1.5
        assert cfg.sources["model_temperature"] == "env:LANGSTAGE_MODEL_TEMPERATURE"
        cfg = self._resolve(monkeypatch, tmp_path, "2.5", model="openai:gpt-4o")
        assert cfg.model_temperature == 0.0

    def test_unknown_provider_has_no_upper_bound(self, monkeypatch, tmp_path):
        cfg = self._resolve(monkeypatch, tmp_path, "3.0", model="ollama:llama3")
        assert cfg.model_temperature == 3.0

    def test_valid_value_kept(self, monkeypatch, tmp_path):
        cfg = self._resolve(monkeypatch, tmp_path, "0.7")
        assert math.isclose(cfg.model_temperature, 0.7)
        assert cfg.sources["model_temperature"] == "env:LANGSTAGE_MODEL_TEMPERATURE"


# ── gh #148: the documented LANGSTAGE_WORKSPACE_ROOT is set before the agent loads ──


_READS_ROOT_AT_IMPORT = """
import os
WORKSPACE = os.environ['LANGSTAGE_WORKSPACE_ROOT']
graph = object()
"""


class TestWorkspaceRootBeforeLoad:
    def test_wrapper_publishes_serving_root_before_import(self, monkeypatch, tmp_path):
        from langstage_jupyter import agent_wrapper, config

        _isolate(monkeypatch, tmp_path)
        serving = tmp_path / "serving"
        serving.mkdir()
        (tmp_path / "strict_agent.py").write_text(_READS_ROOT_AT_IMPORT)
        monkeypatch.setattr(config, "WORKSPACE_ROOT", None)  # unpinned
        monkeypatch.setattr(config, "AGENT_SPEC", f"{tmp_path / 'strict_agent.py'}:graph")
        monkeypatch.setattr(agent_wrapper, "_SERVING_ROOT", str(serving))
        w = agent_wrapper.AgentWrapper()
        assert w.agent is not None, w.load_error
        assert os.environ["LANGSTAGE_WORKSPACE_ROOT"] == str(serving.resolve())
        # The first chat from the same root must not rebuild the agent.
        assert w._applied_root == agent_wrapper.AgentWrapper._resolve_root(str(serving))

    def test_verify_publishes_the_launch_dir(self, monkeypatch, tmp_path, capsys):
        _isolate(monkeypatch, tmp_path)
        (tmp_path / "strict_agent.py").write_text(
            _READS_ROOT_AT_IMPORT.replace("graph = object()", "")
            + "\nfrom langstage_core.demo.stub import graph\n"
        )
        monkeypatch.setattr(
            "sys.argv", ["langstage-jupyter", "-a", "./strict_agent.py:graph", "--verify"]
        )
        with pytest.raises(SystemExit) as exc:
            main()
        out = capsys.readouterr().out
        assert exc.value.code == 0, out
        assert "LANGSTAGE_WORKSPACE_ROOT" not in out


# ── gh #150: notebook tools and the pinned workspace agree ──


class TestPinnedWorkspaceServing:
    def _launch(self, monkeypatch, tmp_path, argv, pin):
        _isolate(monkeypatch, tmp_path)
        if pin is not None:
            monkeypatch.setenv("LANGSTAGE_WORKSPACE_ROOT", str(pin))
        calls = {}

        def fake_run(cmd, env=None):
            calls["cmd"] = cmd

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr("langstage_jupyter.launcher.subprocess.run", fake_run)
        monkeypatch.setattr("sys.argv", ["langstage-jupyter", "--no-browser", *argv])
        with pytest.raises(SystemExit):
            main()
        return calls["cmd"]

    def test_launcher_serves_the_pinned_workspace(self, monkeypatch, tmp_path, capsys):
        project = tmp_path / "project"
        project.mkdir()
        cmd = self._launch(monkeypatch, tmp_path, [], project)
        assert f"--ServerApp.root_dir={project.resolve()}" in cmd
        assert "pinned workspace" in capsys.readouterr().out

    def test_unpinned_serves_the_launch_dir(self, monkeypatch, tmp_path):
        cmd = self._launch(monkeypatch, tmp_path, [], None)
        assert not any(a.startswith("--ServerApp.root_dir") for a in cmd)

    def test_explicit_root_dir_wins_with_a_warning(self, monkeypatch, tmp_path, capsys):
        project = tmp_path / "project"
        project.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        cmd = self._launch(monkeypatch, tmp_path, [f"--notebook-dir={other}"], project)
        assert not any(a.startswith("--ServerApp.root_dir") for a in cmd)
        out = capsys.readouterr().out
        assert "notebook tools" in out and str(project.resolve()) in out

    def test_extension_warns_when_roots_disagree(self, monkeypatch, tmp_path, capsys):
        from langstage_jupyter import agent_wrapper

        serving = tmp_path / "serving"
        pinned = tmp_path / "pinned"
        serving.mkdir()
        pinned.mkdir()
        agent_wrapper.warn_if_roots_disagree(str(serving), str(pinned))
        out = capsys.readouterr().out
        assert "notebook tools" in out and str(pinned.resolve()) in out
        agent_wrapper.warn_if_roots_disagree(str(pinned), str(pinned))
        assert capsys.readouterr().out == ""
