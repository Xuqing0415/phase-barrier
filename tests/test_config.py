"""配置模块测试。"""
from pathlib import Path

import pytest

from anti_shortcut.config import STAGES, GateConfig, load_config


def test_default_config():
    cfg = load_config()
    assert isinstance(cfg, GateConfig)
    assert cfg.min_test_functions == 2
    assert cfg.spec_sections == ["## \u9700\u6c42\u5206\u6790", "## \u8bbe\u8ba1\u65b9\u6848", "## \u63a5\u53e3\u5b9a\u4e49"]
    assert cfg.spec_file == "spec.md"
    assert cfg.gate_dir_name == ".agent_gate"
    assert any("pytest" in pat for pat in cfg.test_commands)
    assert cfg.workspace.is_absolute()


def test_dict_override_keeps_defaults():
    cfg = load_config({"min_test_functions": 5, "spec_file": "design.md"})
    assert cfg.min_test_functions == 5
    assert cfg.spec_file == "design.md"
    assert cfg.spec_sections  # \u9ed8\u8ba4\u503c\u4fdd\u7559


def test_yaml_file(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("min_test_functions: 3\nprotect_gate_dir: false\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.min_test_functions == 3
    assert cfg.protect_gate_dir is False


def test_missing_config_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config("no_such_file.yaml")


def test_stage_names():
    assert STAGES[0] == "\u9700\u6c42\u63a5\u6536"
    assert STAGES[6] == "\u4ea4\u4ed8"
    assert len(STAGES) == 7
