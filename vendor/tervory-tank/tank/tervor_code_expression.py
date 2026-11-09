"""Versioned finite integer/Boolean expression semantics. Never Python execution.

This trusted interpreter is used by the controller and copied into the existing
sandbox. Candidate input is bounded text/data, never imported or eval/exec'd.
"""
from __future__ import annotations

import ast
import re

CONTRACT = 'bounded_int_expression_v1'
MAX_SOURCE = 512
MAX_NODES = 96
MAX_DEPTH = 16
MAX_INT = 2**31 - 1


class ContractError(ValueError):
    pass


def scalar(value: object) -> int | bool:
    if type(value) is bool:
        return value
    if type(value) is int and abs(value) <= MAX_INT:
        return value
    raise ContractError('scalar_out_of_contract')


def interpret(source: str, inputs: dict) -> int | bool:
    """Validate the whole syntax tree, then interpret bounded primitive values.

    Arithmetic uses integers, Boolean operators use Booleans, comparisons require
    matching scalar types. Calls, attributes, containers, strings, assignments,
    lambdas, imports and all other Python capabilities are outside this language.
    """
    if type(source) is not str or not source.isascii() or not 0 < len(source) <= MAX_SOURCE:
        raise ContractError('source_out_of_contract')
    if (type(inputs) is not dict or not 1 <= len(inputs) <= 2
            or any(type(k) is not str or not re.fullmatch('[a-z][a-z0-9_]{0,15}', k) for k in inputs)):
        raise ContractError('inputs_out_of_contract')
    values = {k: scalar(v) for k, v in inputs.items()}
    try:
        root = ast.parse(source, mode='eval')
    except (SyntaxError, ValueError, RecursionError):
        raise ContractError('invalid_expression') from None
    allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp,
        ast.BoolOp, ast.Compare, ast.IfExp, ast.Add, ast.Sub, ast.Mult, ast.FloorDiv,
        ast.Mod, ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or, ast.Lt, ast.LtE,
        ast.Gt, ast.GtE, ast.Eq, ast.NotEq)
    stack, count = [(root, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH or type(node) not in allowed:
            raise ContractError('syntax_out_of_contract')
        if isinstance(node, ast.Constant):
            scalar(node.value)
        if isinstance(node, ast.Name) and node.id not in values:
            raise ContractError('unknown_name')
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))

    def typed(value, kind):
        if type(value) is not kind:
            raise ContractError('operand_type')
        return value

    def visit(node):
        if isinstance(node, ast.Constant):
            return scalar(node.value)
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op, ast.Not):
                return not typed(value, bool)
            value = typed(value, int)
            return scalar(-value if isinstance(node.op, ast.USub) else value)
        if isinstance(node, ast.BinOp):
            left, right = typed(visit(node.left), int), typed(visit(node.right), int)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif right == 0:
                raise ContractError('division_by_zero')
            elif isinstance(node.op, ast.FloorDiv):
                result = left // right
            else:
                result = left % right
            return scalar(result)
        if isinstance(node, ast.BoolOp):
            for child in node.values:
                value = typed(visit(child), bool)
                if isinstance(node.op, ast.And) and not value:
                    return False
                if isinstance(node.op, ast.Or) and value:
                    return True
            return value
        if isinstance(node, ast.IfExp):
            return visit(node.body if typed(visit(node.test), bool) else node.orelse)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            for op, child in zip(node.ops, node.comparators, strict=True):
                right = typed(visit(child), type(left))
                if isinstance(op, ast.Eq):
                    matches = left == right
                elif isinstance(op, ast.NotEq):
                    matches = left != right
                else:
                    typed(left, int)
                    if isinstance(op, ast.Lt):
                        matches = left < right
                    elif isinstance(op, ast.LtE):
                        matches = left <= right
                    elif isinstance(op, ast.Gt):
                        matches = left > right
                    else:
                        matches = left >= right
                if not matches:
                    return False
                left = right
            return True
        raise ContractError('unsupported_node')

    return scalar(visit(root.body))
