import asyncio

from app.tools.registry import tool


def _run_search(query: str) -> str:
    from ddgs import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=5):
            results.append(f"【{r['title']}】\n{r['body']}\n来源：{r['href']}")
    return "\n\n".join(results) if results else "未找到相关结果"


@tool
async def web_search(query: str) -> str:
    """搜索网页获取实时信息。query: 搜索关键词"""
    try:
        return await asyncio.to_thread(_run_search, query)
    except ImportError:
        return "错误：ddgs 未安装，请运行 pip install ddgs"
    except Exception as e:
        return f"搜索失败：{e}"
