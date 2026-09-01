"""anti_shortcut init 配置脚手架测试（v0.26.0）。"""
import yaml

from anti_shortcut.config import GateConfig
from anti_shortcut.init import init_config, render_config
from anti_shortcut.languages import detect_language


def test_init_generates_valid_config(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    out, text = init_config(tmp_path)
    assert out == tmp_path / "config.yaml"
    data = yaml.safe_load(text)
    assert data["language"] == "python"
    GateConfig(**{k: v for k, v in data.items() if k in GateConfig.model_fields})


def test_init_detects_language(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    out, text = init_config(tmp_path)
    data = yaml.safe_load(text)
    assert data["language"] == "javascript"
    assert any("jest" in c for c in data["test_commands"])


def test_init_refuses_overwrite(tmp_path):
    (tmp_path / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    try:
        init_config(tmp_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("应拒绝覆盖已有配置文件")


def test_init_force_overwrites(tmp_path):
    (tmp_path / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    out, _ = init_config(tmp_path, force=True)
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["language"] == "python"


def test_init_custom_output(tmp_path):
    out, _ = init_config(tmp_path, output="gate/phase-barrier.yaml")
    assert out == tmp_path / "gate" / "phase-barrier.yaml"
    assert out.exists()


def test_init_optional_features(tmp_path):
    out, text = init_config(
        tmp_path,
        coverage_threshold=90.0,
        hmac_key="k3y",
        audit_url="https://siem.example/ingest",
        rules=["no_path_traversal", "no_shell_injection"],
    )
    data = yaml.safe_load(text)
    assert data["coverage_threshold"] == 90.0
    assert data["state_hmac_key"] == "k3y"
    assert data["audit_remote_url"].startswith("https://")
    assert data["rules"] == ["no_path_traversal", "no_shell_injection"]


def test_init_rejects_gate_dir(tmp_path):
    (tmp_path / ".agent_gate").mkdir()
    try:
        init_config(tmp_path, output=".agent_gate/config.yaml")
    except ValueError:
        pass
    else:
        raise AssertionError("应拒绝写入门禁目录")


def test_render_config_cpp(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("cmake\n", encoding="utf-8")
    out, text = init_config(tmp_path)
    data = yaml.safe_load(text)
    assert data["language"] == "cpp"
    assert any("ctest" in c for c in data["test_commands"])


def test_init_detect_language_helper_matches():
    # render_config 直接用已检测语言渲染
    text = render_config(language="go", output="config.yaml")
    data = yaml.safe_load(text)
    assert data["language"] == "go"
    assert any("go" in c for c in data["test_commands"])
