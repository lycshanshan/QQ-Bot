import asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from bot.media.render import MarkdownRenderer
async def main():
    paths=await MarkdownRenderer().render(r'''# 中文、公式与表格渲染验收

这是机器人复杂回答的图片样式。**重点内容**、行内代码 `print("hello")` 与中文均应清晰可读。

## 一个公式

行内公式 $E=mc^2$，以及居中公式：

$$
\int_0^1 x^2\,dx=\frac{1}{3}
$$

| 项目 | 数值 | 说明 |
| --- | --- | --- |
| 早安 | 04:30—12:00 | 包含结束点 |
| 晚安 | 20:00—03:00 | 凌晨归前一天 |

```python
for i in range(3):
    print("中文也能显示", i)
```

> 渲染页不加载聊天内容指向的外部资源。
''',ROOT/'artifacts/feasibility/render-smoke')
    print(json.dumps([str(p) for p in paths]))
asyncio.run(main())
