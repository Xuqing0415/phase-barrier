# 语言适配器（多语言支持）

> 迁移自 README 精简版主页；语言配置与自动检测见 [配置指南](configuration.md)，第三方语言适配器插件见 [插件与生态](plugins.md)。[返回 README](https://github.com/Xuqing0415/phase-barrier#readme)

v0.3.0 起，语言相关逻辑（文件识别、语法检查、测试统计、测试命令识别）抽象为
**语言适配器（Language Adapter）**。核心包内置 Python、JavaScript/TypeScript、Java、Kotlin、Scala、Go、Rust、Swift、Ruby、
PHP、C#、C++、.NET 与 Dart 适配器，第三方可注册自定义适配器；未显式指定时按工作区标志文件自动检测。

### 快速启用

```python
from anti_shortcut import AntiShortcutSkill

# 显式指定语言（优先级最高），无需再手工配文件模式
skill = AntiShortcutSkill(
    workspace=".",
    config={"language": "javascript"},
    user_request="实现一个计算斐波那契数列的函数",
)
```

```yaml
# 或 YAML（完整示例见 examples/anti_shortcut_js_config.yaml）
language: javascript
min_test_functions: 2
test_commands:
  - '^\s*npm\s+test\b'
  - '^\s*npx\s+(jest|vitest|mocha|playwright)\b'
  - '^\s*npx\s+tsc\s+--noEmit\b'
```

不写 `language` 时自动检测标志文件：`package.json` -> `javascript`，`pom.xml` -> `java`，
`go.mod` -> `go`，`Cargo.toml` -> `rust`，`Gemfile` / `*.gemspec` -> `ruby`，
`*.csproj` / `*.sln` -> `csharp`，`CMakeLists.txt` / `Makefile` / `*.vcxproj` -> `cpp`，`composer.json` -> `php`，
`build.sbt` -> `scala`，`Package.swift` -> `swift`，`pubspec.yaml` -> `dart`，`requirements.txt` / `setup.py` / `pyproject.toml` -> `python`；未识别时默认 Python。
.NET 项目可显式 `language: dotnet` 启用 `DotNetAdapter`（与 `csharp` 共用实现，便于按生态区分）。
适配器默认文件模式与 YAML 中的 `test_file_patterns` / `source_file_patterns`
自动合并（配置只增不减）。完整字段说明见 [配置指南](configuration.md)。
项目配置示例：`examples/anti_shortcut_js_config.yaml`、`anti_shortcut_go_config.yaml`、
`anti_shortcut_rust_config.yaml`。

### 内置适配器

| 适配器 | 文件识别 | 语法检查 | 测试校验 |
|--------|----------|----------|----------|
| `PythonAdapter` | `test_*.py` / `tests/**` 为测试，`*.py` 为实现 | `compile()` | AST 解析：测试函数数 + `assert` / `pytest.raises` / `unittest.TestCase` 的 `self.assert*` 断言 |
| `JavaScriptAdapter` | `*.test.js` / `*.spec.ts` / `__tests__/` 为测试，`src/**` 与 `*.js|ts|jsx|tsx` 为实现 | `node --check` / `tsc --noEmit`（优先 `tsconfig.json` 项目检查；工具缺失返回明确错误） | 项目安装 acorn 时真实解析（`test` / `it` / `describe` 声明 + `expect` / `assert` 断言），否则启发式；可选 `jest --listTests --json` 动态发现；输出解析：Jest / Vitest（`Tests: N passed`）、Playwright（`N passed` / `N failed`）与 Cypress（`All specs passed!` / `N passing` / `N failing`） |
| `JavaAdapter` | `*Test.java` / `*Tests.java` / `src/test/**` 为测试，`src/**` 与 `*.java` 为实现 | 项目级 `mvn test-compile` / `gradle compileTestJava`（优先 `mvnw` / `gradlew`，带缓存）；无构建工具时回退 `javac -proc:none` | 启发式：`@Test` 注解数（JUnit / TestNG）+ JUnit/Hamcrest 断言关键字；输出解析：Surefire `Tests run: N, Failures: M, Errors: K`（Errors 并入失败）、TestNG `Total tests run: N, Failures: M, Skips|Skipped: K, Configuration Failures: C`、Gradle `N tests completed, M failed`（含 `Class > method() FAILED` 与参数化 `[N] method(args) FAILED` 行）、JUnit Platform Console `[ N tests successful / failed ]`；v0.44.0 起用 10 组 fixtures 矩阵回归 |
| `KotlinAdapter` | `*Test.kt` / `*Tests.kt` / `src/test/**`（kotlin）为测试，`src/main/kotlin/**` 与 `*.kt` 为实现 | `kotlinc`（缺失返回明确错误；跨文件/依赖缺失降级为“需完整项目编译验证”） | 启发式：`@Test` 注解数（JUnit5 / kotlin.test）+ `assert*` 断言关键字；输出解析复用 Java（Gradle / Surefire / JUnit Console） |
| `ScalaAdapter` | `*Test.scala` / `*Tests.scala` / `*Spec.scala` / `src/test/**` 为测试，`src/main/scala/**` 与 `*.scala` 为实现 | `scalac` 单文件编译（缺失返回明确错误；跨文件/依赖缺失降级为“需完整项目编译验证”） | 启发式：`@Test` 注解 + ScalaTest / MUnit `test("...")` / spec2 `"..." should` 统计 + `assert*` / matcher 断言；输出解析：ScalaTest（`Tests: succeeded N, failed M`），回退 Java（Surefire / Gradle / JUnit Console） |
| `SwiftAdapter` | `*Test.swift` / `*Tests.swift` / `Tests/**`（SwiftPM）为测试，`Sources/**` 与 `*.swift` 为实现（`Package.swift` 清单除外） | `swiftc -typecheck`（缺失返回明确错误；`@main` 自动以 `-parse-as-library` 重试；跨文件/依赖缺失降级为“需完整项目编译验证”） | 启发式：XCTest `func testXxx()` 方法 + swift-testing `@Test` 属性 + `XCTAssert*` / `#expect` / `#require` 断言；输出解析：XCTest（`Executed N tests, with M failures`）、swift-testing（`Test run with N tests passed|failed`）与 xcodebuild（`** TEST SUCCEEDED/FAILED **`） |
| `DartAdapter` | `*_test.dart` / `test/**` / `integration_test/**` 为测试，`lib/**` / `bin/**` / `web/**` 与 `*.dart` 为实现 | `dart format --output=none`（解析不落盘；Dart SDK 缺失返回明确错误） | 启发式：package:test `test()` / `testWidgets()` 声明数 + `expect` / `expectLater` 断言；输出解析：`dart test` / `flutter test` 进度汇总（`+N: All tests passed!` / `+N -M: Some tests failed.`，含 `~K` 跳过） |
| `GoAdapter` | `*_test.go` 为测试，`*.go` / `cmd|internal|pkg/**` 为实现 | `gofmt -e`（Go 工具链缺失返回明确错误） | 启发式：`func TestXxx(t *testing.T)` 函数数 + `t.Error` / `t.Fatal` / `assert` / `require` 断言 |
| `RustAdapter` | `tests/**` / `*_test.rs` / `src/**/tests.rs` 为测试，`src/**` 与 `*.rs` 为实现 | `cargo check`（有 `Cargo.toml`）/ `rustc` 单文件回退（工具缺失返回明确错误） | 启发式：`#[test]` / `#[tokio::test]` 属性数 + `assert!` / `assert_eq!` / `assert_ne!` |
| `CSharpAdapter` | `*Test.cs` / `*Tests.cs` / `**/Tests/**` 为测试，`*.cs` 为实现 | 项目级 `dotnet build`（查找 `.csproj` / `.sln` 项目根，带指纹缓存；无项目根或工具缺失返回明确错误） | 启发式：`[Fact]` / `[Theory]` / `[Test]` 特性数 + `Assert.*` 断言；输出解析：VSTest `Passed! - Failed: F, Passed: P` / NUnit |
| `CppAdapter` | `test_*.cpp` / `*_test.cpp` / `tests/**` 为测试，`*.cpp` / `*.cc` / `*.cxx` / `*.c` / `*.h` / `*.hpp` 为实现 | C++：`g++ -fsyntax-only`（`clang++` 回退）；C：`gcc -fsyntax-only`（`clang` / `cc` 回退）；编译器缺失返回明确错误 | 启发式：GoogleTest `TEST(` / `TEST_F(` 与 Catch2 `TEST_CASE(` / `SCENARIO(` 宏数 + `EXPECT_*` / `ASSERT_*` / `REQUIRE*` / `CHECK*` 断言；输出解析：`[  PASSED  ]` / `[  FAILED  ]` / ctest / Catch2（`All tests passed` / `FAILED:`） |
| `DotNetAdapter` | 同 `CSharpAdapter`（`name="dotnet"`） | 同 `CSharpAdapter` | 同 `CSharpAdapter`（显式 `language: dotnet` 启用） |
| `PhpAdapter` | `*Test.php` / `tests/**` / `spec/**` 为测试，`*.php` / `src/**` / `app/**` 为实现 | `php -l`（PHP CLI 缺失返回明确错误） | 启发式：PHPUnit `public function testXxx` 方法 + `#[Test]` 属性数 + `assert*()` / `expectException()` 断言；输出解析：`OK (N tests, M assertions)` / `Tests: N, Failures: M, Errors: K` / `FAILURES!` |

### 自定义适配器

只需 4 步即可接入一种新语言（10 分钟内可完成最小实现）：

**1. 实现 `LanguageAdapter`**（文件识别用默认模式即可，至少实现 `check_syntax`）：

```python
# my_adapters.py
from anti_shortcut.languages import LanguageAdapter

class MyLanguageAdapter(LanguageAdapter):
    name = "mylang"
    source_file_patterns = ["*.foo"]        # 实现文件模式
    test_file_patterns = ["*.test.foo"]     # 测试文件模式
    test_command_patterns = [r"^\s*foo\s+test\b"]  # 测试命令正则

    def check_syntax(self, path):
        return True, "ok"   # 返回 (是否通过, 错误信息)

    def analyze_tests(self, path):
        # 返回 {"test_functions": [...], "assertions_total": N}
        # 可参考 anti_shortcut/languages/javascript.py 的启发式实现
        ...
```

**2. 本地配置加载**（无需打包，直接指定导入路径）：

```yaml
language_adapter: "my_adapters.MyLanguageAdapter"
adapter_options:
  min_test_functions: 3    # 传给适配器的额外参数（由适配器自行解释）
```

**3. 打包发布为独立包**（便于复用与分享）：

```toml
[project]
name = "phase-barrier-mylang-adapter"
version = "0.1.0"
dependencies = ["phase-barrier>=0.3.0"]

[project.entry-points."phase_barrier.languages"]
mylang = "my_adapters:MyLanguageAdapter"
```

```bash
python -m build && twine check dist/* && twine upload dist/*
```

**4. 入口点注册后按名称引用**（安装插件包即可，无需再写导入路径）：

```yaml
language: mylang
```

**可运行示例**：`examples/custom_adapter/` 提供了一个虚构 `.foo` 语言的完整插件
（`foo_language.py` + `foo_config.yaml` + `pyproject.toml`），运行
`python examples/custom_adapter/demo.py` 可看到自定义适配器参与
文件识别、语法检查、测试校验与测试命令识别的完整拦截流程。

适配器选择优先级：显式 `language` > 自定义 `language_adapter` > 自动检测 > 默认 Python。
