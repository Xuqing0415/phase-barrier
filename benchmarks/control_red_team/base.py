"""Reuse the existing red-team result and waiver semantics, with explicit probes."""
from __future__ import annotations

import hashlib
import secrets
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from anti_shortcut.proxy import GateProxy
from anti_shortcut.sidecar import GateSidecar
from benchmarks.red_team.techniques.base import EscapeAttempt, EscapeResult, EscapeTechnique, Outcome

SPEC = '''# Addition
## 需求分析
Implement add(a, b), returning the sum of two integers.
## 设计方案
Use integer addition, with no external side effects.
## 接口定义
def add(a: int, b: int) -> int
'''
TESTS = '''from add import add
def test_positive():
    assert add(1, 2) == 3
def test_zero():
    assert add(0, 0) == 0
def test_negative():
    assert add(-2, 1) == -1
'''
IMPL = 'def add(a, b):\n    return a + b\n'


@contextmanager
def denied(*exceptions, match=None):
    """Only a specific expected rejection counts; infrastructure errors propagate."""
    try:
        yield
    except exceptions as exc:
        assert match is None or match in str(exc), f'Wrong rejection: {exc}'
    else:
        raise AssertionError('Forbidden operation succeeded')


class Environment:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        (self.root / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        self.sidecar = GateSidecar(self.root, config={'state_hmac_key': 'red-team-only-key'})
        self.skill = self.sidecar.skill
        self.control = self.skill.control
        self.permissions = self.control.permissions
        self.admin = self.permissions.bootstrap()
        self.proxy = GateProxy(self.skill)
        self.evidence = {}

    def issue(self, role='worker', actor='agent'):
        return self.permissions.issue(role, actor)

    def expire(self, token, kind='session'):
        with self.control.store.transaction() as db:
            key = hashlib.sha256(token.encode()).hexdigest()
            row = self.control.store.get(db, kind, key)
            assert row is not None
            row['expires_at'] = 0
            self.control.store.put(db, kind, key, row)

    def to_stage(self, target):
        def ensure(result):
            if not result['success']:
                raise RuntimeError(f'Cannot prepare stage {target}: {result}')
        if self.skill.current_stage == 0 and target >= 1:
            ensure(self.skill.advance_stage(1))
        if self.skill.current_stage == 1 and target >= 2:
            self.proxy.write_file('spec.md', SPEC)
            ensure(self.skill.advance_stage(2))
        if self.skill.current_stage == 2 and target >= 3:
            self.proxy.write_file('test_add.py', TESTS)
            ensure(self.skill.advance_stage(3))
        if self.skill.current_stage == 3 and target >= 4:
            self.proxy.write_file('add.py', IMPL)
            ensure(self.skill.advance_stage(4))
        if self.skill.current_stage == 4 and target >= 5:
            # Real test process: no synthetic green-test evidence.
            command = f'"{sys.executable}" -m pytest -q -o addopts= test_add.py'
            result = self.proxy.execute_command(command)
            if result['exit_code'] != 0:
                raise RuntimeError(f'Real fixture tests failed: {result}')
            # Public test-reporting API receives the real process output, including when
            # an absolute interpreter path is not recognized by the command classifier.
            env_result = self.sidecar.record_test_run(result['exit_code'], result['output'])
            if not env_result['ok']:
                raise RuntimeError('Cannot record real test results')
            if target == 5:
                self.proxy.write_file('add.py', IMPL + '\n# invalidate tests to enter repair stage\n')
            ensure(self.skill.advance_stage(5))
        if self.skill.current_stage != target:
            raise RuntimeError(f'Fixture stopped at stage {self.skill.current_stage}, expected {target}')


@dataclass
class ControlAttempt(EscapeAttempt):
    probe: Callable | None = None

    def run(self, root):
        env = None
        try:
            env = Environment(Path(root) / f'{self.technique}-{self.case_id}-{secrets.token_hex(5)}')
            with env.permissions.session(env.admin):
                self.probe(env)
            outcome, message = Outcome.BLOCKED, 'Boundary invariant held under the adversarial probe'
        except AssertionError as exc:
            outcome, message = Outcome.ESCAPED, str(exc)
        except Exception as exc:
            outcome, message = Outcome.INCONCLUSIVE, f'{type(exc).__name__}: {exc}'
        finally:
            if env:
                env.sidecar.close()
        return EscapeResult(technique=self.technique, case_id=self.case_id,
                            target_defense=4, expectation='blocked', outcome=outcome,
                            goal_stage=6, stage_reached=env.skill.current_stage if env else 0,
                            message=message, blocked_by='control-plane' if outcome == Outcome.BLOCKED else None,
                            evidence=env.evidence if env else {})


class ControlTechnique(EscapeTechnique):
    target_defense = 4
    cases = {}

    def generate(self, requirement):
        return self.generate_all(requirement)[0]

    def generate_all(self, requirement):
        return [ControlAttempt(technique=self.name, target_defense=4, requirement=requirement,
                               goal_stage=6, case_id=key, probe=probe)
                for key, probe in self.cases.items()]
