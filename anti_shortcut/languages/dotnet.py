""".NET 语言适配器（v0.26.0）：C# 适配器的显式别名。

C# 适配器（``anti_shortcut.languages.csharp``）已完整覆盖 .NET 生态：
- 文件识别：``*.cs`` 实现 / ``*Tests.cs`` / ``*Test.cs`` / ``**/Tests/**`` 测试
- 语法检查：``dotnet build <项目根>``（自动向上查找 .csproj / .sln）
- 测试统计：``[Fact]`` / ``[Theory]`` / ``[Test]`` / ``[TestMethod]`` + ``Assert.*``
- 测试命令：``dotnet test`` / ``dotnet vstest`` / ``nunit3-console`` / msbuild VSTest
- 输出解析：VSTest 汇总 ``Passed! - Failed: 0, Passed: 5, Skipped: 0, Total: 5``

本适配器把同一实现以 ``dotnet`` 名称注册，供 ``language: dotnet`` 显式配置使用
（自动检测命中 .csproj / .sln 时仍返回 ``csharp``，行为完全一致）。
"""
from __future__ import annotations

from .csharp import CSharpAdapter

__all__ = ["DotNetAdapter"]


class DotNetAdapter(CSharpAdapter):
    """.NET：C# 适配器的显式别名（name = "dotnet"）。"""

    name = "dotnet"
