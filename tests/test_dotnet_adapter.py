"""dotnet 适配器（C# 别名）测试（v0.26.0）。"""
from pathlib import Path
from anti_shortcut.config import GateConfig
from anti_shortcut.languages import DotNetAdapter, LANGUAGE_REGISTRY, detect_language, get_adapter


def test_dotnet_adapter_registered():
    assert "dotnet" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["dotnet"] is DotNetAdapter


def test_dotnet_is_csharp_alias():
    assert issubclass(DotNetAdapter, __import__("anti_shortcut.languages.csharp", fromlist=["CSharpAdapter"]).CSharpAdapter)
    assert DotNetAdapter().name == "dotnet"


def test_dotnet_explicit_language(tmp_path):
    cfg = GateConfig(language="dotnet", workspace=tmp_path)
    adapter = get_adapter(cfg, tmp_path)
    assert isinstance(adapter, DotNetAdapter)


def test_dotnet_detection_prefers_csharp(tmp_path):
    (tmp_path / "App.sln").write_text("Solution", encoding="utf-8")
    assert detect_language(tmp_path) == "csharp"  # 自动检测保持既有行为


def test_dotnet_file_classification():
    a = DotNetAdapter()
    assert a.is_source_file(Path("src/Fib.cs"))
    assert a.is_test_file(Path("FibTests.cs"))
