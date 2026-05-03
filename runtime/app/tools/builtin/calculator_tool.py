import ast
import operator
from app.tools.registry import tool

_OPS = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.Pow:      operator.pow,
    ast.Mod:      operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub:     operator.neg,
    ast.UAdd:     operator.pos,
}


def _eval(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        op = _OPS.get(type(node.op))
        if not op:
            raise ValueError("不支持的运算符")
        return op(_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _OPS.get(type(node.op))
        if not op:
            raise ValueError("不支持的运算符")
        return op(_eval(node.operand))
    raise ValueError(f"不支持的表达式类型: {type(node).__name__}")


@tool
def calculator(expression: str) -> str:
    """计算数学表达式，支持 + - * / ** % // 运算。expression: 如 '2 + 3 * 4' 或 '(100 - 32) / 1.8'"""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree.body)
        return str(int(result)) if result == int(result) else str(round(result, 10))
    except ZeroDivisionError:
        return "错误：除零"
    except Exception as e:
        return f"计算错误：{e}"
