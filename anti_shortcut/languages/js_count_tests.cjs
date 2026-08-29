#!/usr/bin/env node
/* phase-barrier: 用 acorn（项目依赖）真实解析 JS 测试文件，统计测试声明与断言。
 * 用法：node js_count_tests.cjs <file> [<file> ...]
 * 输出：JSON {"acorn": true|false, "files": [{path, declarations, test_cases,
 *        suites, assertions}]}
 * 说明：优先从 cwd（项目根）解析 acorn；项目未安装 acorn 时返回 {"acorn": false}，
 *       由适配器回退启发式校验。TS/TSX 文件 acorn 无法解析时返回 error，同样回退。
 */
"use strict";

const fs = require("fs");
const path = require("path");

let acorn = null;
try {
  // 优先使用项目（cwd）node_modules 里的 acorn，与项目解析版本保持一致
  acorn = require(require.resolve("acorn", { paths: [process.cwd()] }));
} catch (_) {
  try {
    acorn = require("acorn");
  } catch (_) {
    acorn = null;
  }
}

const MODIFIERS = new Set(["skip", "only", "todo", "each", "concurrent", "failing", "skipIf", "retry"]);

function calleeName(node) {
  // 只返回测试声明名：test / it / describe 及带修饰符的链式调用
  if (!node || typeof node.type !== "string") return null;
  if (node.type === "Identifier") {
    return node.name === "test" || node.name === "it" || node.name === "describe" ? node.name : null;
  }
  if (node.type === "MemberExpression") {
    const obj = calleeName(node.object);
    if (obj) {
      const prop = node.property && node.property.type === "Identifier" ? node.property.name : "";
      if (MODIFIERS.has(prop)) return obj + "." + prop;
    }
    return null;
  }
  return null;
}

function isAssertionCall(node) {
  if (node.type !== "CallExpression") return false;
  const callee = node.callee;
  if (callee.type === "Identifier") {
    return callee.name === "expect" || callee.name === "assert";
  }
  if (callee.type === "MemberExpression") {
    // expect(x).toBe(...) / expect(x).rejects.toThrow(...) / assert.equal(...)
    let obj = callee.object;
    while (obj && obj.type === "MemberExpression") obj = obj.object;
    if (obj && obj.type === "CallExpression" && obj.callee.type === "Identifier" && obj.callee.name === "expect") {
      return true;
    }
    if (obj && obj.type === "Identifier" && obj.name === "assert") return true;
  }
  return false;
}

function walk(node, stats) {
  if (!node || typeof node.type !== "string") return;
  if (node.type === "CallExpression") {
    const name = calleeName(node.callee);
    if (name) {
      stats.declarations += 1;
      if (name.startsWith("describe")) stats.suites += 1;
      else stats.test_cases += 1;
    }
    if (isAssertionCall(node)) stats.assertions += 1;
  }
  for (const key in node) {
    if (key === "start" || key === "end" || key === "loc" || key === "range") continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const item of value) walk(item, stats);
    } else if (value && typeof value.type === "string") {
      walk(value, stats);
    }
  }
}

function analyzeFile(file) {
  const stats = { declarations: 0, test_cases: 0, suites: 0, assertions: 0 };
  try {
    const source = fs.readFileSync(file, "utf8");
    const ast = acorn.parse(source, {
      ecmaVersion: "latest",
      sourceType: "module",
      allowHashBang: true,
      allowReturnOutsideFunction: true,
    });
    walk(ast, stats);
  } catch (err) {
    return { path: file, error: String((err && err.message) || err) };
  }
  return {
    path: file,
    declarations: stats.declarations,
    test_cases: stats.test_cases,
    suites: stats.suites,
    assertions: stats.assertions,
  };
}

const files = process.argv.slice(2);
if (!acorn) {
  console.log(JSON.stringify({ acorn: false, files: [] }));
  process.exit(0);
}
console.log(JSON.stringify({ acorn: true, files: files.map(analyzeFile) }));