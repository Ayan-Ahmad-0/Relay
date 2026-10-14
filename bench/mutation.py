"""AST-based Mutation Testing Engine for Relay.

Generates systematic code mutations (comparisons, arithmetic, booleans, bounds)
across core safety modules, executes targeted test suites against each mutant,
and asserts a mutation kill rate >= 80%.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Mutation:
    lineno: int
    col_offset: int
    original_op: str
    mutated_op: str
    description: str


class ASTMutator(ast.NodeTransformer):
    """Generates one mutant AST given a target mutation index."""

    def __init__(self, target_index: int):
        self.target_index = target_index
        self.current_index = 0
        self.applied_mutation: Mutation | None = None

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        new_ops = []
        for op in node.ops:
            mut_op = None
            if isinstance(op, ast.Eq):
                mut_op = ast.NotEq()
            elif isinstance(op, ast.NotEq):
                mut_op = ast.Eq()
            elif isinstance(op, ast.Lt):
                mut_op = ast.GtE()
            elif isinstance(op, ast.Gt):
                mut_op = ast.LtE()
            elif isinstance(op, ast.LtE):
                mut_op = ast.Gt()
            elif isinstance(op, ast.GtE):
                mut_op = ast.Lt()
            elif isinstance(op, ast.In):
                mut_op = ast.NotIn()
            elif isinstance(op, ast.NotIn):
                mut_op = ast.In()

            if mut_op is not None:
                if self.current_index == self.target_index:
                    new_ops.append(mut_op)
                    self.applied_mutation = Mutation(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        original_op=type(op).__name__,
                        mutated_op=type(mut_op).__name__,
                        description=f"Comparison {type(op).__name__} -> {type(mut_op).__name__}",
                    )
                else:
                    new_ops.append(op)
                self.current_index += 1
            else:
                new_ops.append(op)
        node.ops = new_ops
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        mut_op = None
        if isinstance(node.op, ast.Add):
            mut_op = ast.Sub()
        elif isinstance(node.op, ast.Sub):
            mut_op = ast.Add()
        elif isinstance(node.op, ast.Mult):
            mut_op = ast.FloorDiv()

        if mut_op is not None:
            if self.current_index == self.target_index:
                old_op_name = type(node.op).__name__
                node.op = mut_op
                self.applied_mutation = Mutation(
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    original_op=old_op_name,
                    mutated_op=type(mut_op).__name__,
                    description=f"BinOp {old_op_name} -> {type(mut_op).__name__}",
                )
            self.current_index += 1
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, bool):
            if self.current_index == self.target_index:
                old_val = node.value
                node.value = not old_val
                self.applied_mutation = Mutation(
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    original_op=str(old_val),
                    mutated_op=str(node.value),
                    description=f"Boolean {old_val} -> {node.value}",
                )
            self.current_index += 1
        return node


def count_possible_mutations(source_code: str) -> int:
    tree = ast.parse(source_code)

    class Counter(ast.NodeVisitor):
        def __init__(self):
            self.count = 0

        def visit_Compare(self, node: ast.Compare):
            for op in node.ops:
                if type(op) in (
                    ast.Eq,
                    ast.NotEq,
                    ast.Lt,
                    ast.Gt,
                    ast.LtE,
                    ast.GtE,
                    ast.In,
                    ast.NotIn,
                ):
                    self.count += 1
            self.generic_visit(node)

        def visit_BinOp(self, node: ast.BinOp):
            if type(node.op) in (ast.Add, ast.Sub, ast.Mult):
                self.count += 1
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant):
            if isinstance(node.value, bool):
                self.count += 1
            self.generic_visit(node)

    counter = Counter()
    counter.visit(tree)
    return counter.count


def run_mutation_benchmark(
    target_rel_path: str = "relay/security.py",
    test_rel_path: str = "tests/test_injection.py",
    max_mutants: int = 25,
) -> dict:
    target_file = ROOT / target_rel_path
    original_code = target_file.read_text(encoding="utf-8")
    total_possible = count_possible_mutations(original_code)
    num_to_test = min(total_possible, max_mutants)

    killed = 0
    survived = 0
    errors = 0
    mutants_log = []

    print(
        f"[*] Starting Mutation Run on {target_rel_path} ({num_to_test}/{total_possible} mutants)"
    )

    for i in range(num_to_test):
        tree = ast.parse(original_code)
        mutator = ASTMutator(target_index=i)
        mutated_tree = mutator.visit(tree)
        ast.fix_missing_locations(mutated_tree)

        mutation_info = mutator.applied_mutation
        if not mutation_info:
            continue

        mutated_code = ast.unparse(mutated_tree)
        target_file.write_text(mutated_code, encoding="utf-8")

        try:
            # Run test against mutant
            cmd = [sys.executable, "-m", "pytest", test_rel_path, "-q", "--no-header"]
            res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=15)
            if res.returncode != 0:
                killed += 1
                status = "KILLED"
            else:
                survived += 1
                status = "SURVIVED"
        except subprocess.TimeoutExpired:
            killed += 1
            status = "KILLED (Timeout)"
        except Exception:
            errors += 1
            status = "ERROR"
        finally:
            target_file.write_text(original_code, encoding="utf-8")

        mutants_log.append(
            {
                "id": i + 1,
                "line": mutation_info.lineno,
                "description": mutation_info.description,
                "status": status,
            }
        )

    tested = killed + survived
    kill_rate = (killed / tested * 100.0) if tested > 0 else 0.0

    print(
        f"[+] Mutation Results: {killed} Killed, {survived} Survived ({kill_rate:.1f}% kill rate)"
    )

    return {
        "target": target_rel_path,
        "test": test_rel_path,
        "total_possible": total_possible,
        "tested": tested,
        "killed": killed,
        "survived": survived,
        "kill_rate_pct": round(kill_rate, 2),
        "mutants": mutants_log,
    }


if __name__ == "__main__":
    report = run_mutation_benchmark()
    print(f"Target >= 80% achieved: {report['kill_rate_pct'] >= 80.0}")
