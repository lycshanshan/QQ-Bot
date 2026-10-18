from __future__ import annotations

import asyncio
import base64
import html
import math
from pathlib import Path
import re
import uuid

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from playwright.async_api import async_playwright

from bot.config import ROOT

ASSETS=ROOT/'artifacts/runtime/renderer/katex'


def markdown_html(text: str) -> str:
    def math_markup(content,options):
        return f'<span data-math="{html.escape(content,quote=True)}" data-display="{str(options["display_mode"]).lower()}"></span>'
    parser=MarkdownIt('commonmark',{'html':False,'breaks':True}).enable('table')
    parser.use(dollarmath_plugin,allow_labels=False,renderer=math_markup)
    parser.add_render_rule('image',lambda self,tokens,idx,options,env:html.escape('[图片：'+tokens[idx].content+']'))
    return parser.render(text)


def browser_path() -> str:
    candidates=[Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                Path('C:/Program Files/Google/Chrome/Application/chrome.exe')]
    for candidate in candidates:
        if candidate.is_file():return str(candidate)
    raise RuntimeError('No local Chromium browser available')


def stylesheet() -> str:
    css=(ASSETS/'katex.min.css').read_text(encoding='utf-8')
    def embed(match):
        filename=match[1]
        path=ASSETS/'fonts'/filename
        if path.parent!=ASSETS/'fonts' or not path.is_file():raise ValueError('Unknown font resource')
        mime='font/woff2' if path.suffix=='.woff2' else 'font/woff' if path.suffix=='.woff' else 'font/ttf'
        return 'url(data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()+')'
    return re.sub(r'url\(fonts/([^\)]+)\)',embed,css)


class MarkdownRenderer:
    def __init__(self):self.lock=asyncio.Lock()

    async def render(self,text: str,output: Path) -> list[Path]:
        if len(text)>24000:raise ValueError('Markdown output too long for image rendering')
        async with self.lock:
            async with asyncio.timeout(40):
                output.mkdir(parents=True,exist_ok=True)
                body=markdown_html(text)
                style='''*{box-sizing:border-box}body{margin:0;background:#fff;color:#172033;font:20px/1.75 "Microsoft YaHei","Noto Sans CJK SC",sans-serif}article{padding:40px 48px;width:1000px;overflow:hidden}h1,h2,h3{line-height:1.4;color:#142644;margin:24px 0 14px}h1{font-size:34px}h2{font-size:28px}h3{font-size:23px}p{margin:12px 0}pre{padding:20px;background:#f1f5f9;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:17px;line-height:1.6}code{font-family:Consolas,monospace}p code{padding:2px 5px;background:#f1f5f9}table{border-collapse:collapse;width:100%;table-layout:fixed;margin:20px 0;font-size:18px}th,td{border:1px solid #cbd5e1;padding:10px 14px;overflow-wrap:anywhere}th{background:#eaf0f8;text-align:left}blockquote{border-left:4px solid #6f8caf;margin:18px 0;padding:2px 20px;background:#f7f9fc}li{margin:8px 0}a{color:#245a99}hr{border:0;border-top:1px solid #dbe2eb;margin:26px 0}.math.block{text-align:center;margin:24px 0}.katex-display{overflow:hidden}'''
                document=('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
                          'content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; font-src data:; img-src data:">'
                          '<style>'+style+stylesheet()+'</style><article>'+body+'</article>')
                async with async_playwright() as playwright:
                    browser=await playwright.chromium.launch(executable_path=browser_path(),headless=True)
                    try:
                        context=await browser.new_context(viewport={'width':1000,'height':1400},device_scale_factor=1)
                        await context.route('**/*',lambda route:route.abort())
                        page=await context.new_page()
                        await page.set_content(document,wait_until='load')
                        await page.add_script_tag(content=(ASSETS/'katex.min.js').read_text(encoding='utf-8'))
                        await page.evaluate('''() => { for (const element of document.querySelectorAll('[data-math]')) { katex.render(element.dataset.math,element,{displayMode:element.dataset.display==='true',throwOnError:false,trust:false,strict:'ignore'}); } }''')
                        await page.evaluate('document.fonts.ready')
                        height=math.ceil(await page.evaluate("document.querySelector('article').getBoundingClientRect().height"))
                        if height>11200:raise ValueError('Rendered content exceeds 8 pages')
                        boundaries=await page.evaluate('''() => Array.from(document.querySelectorAll('article > *,tr,li')).map(e=>Math.floor(e.getBoundingClientRect().top)).filter(y=>y>0).sort((a,b)=>a-b)''')
                        paths=[]
                        y=0
                        while y<height:
                            end=min(y+1400,height)
                            if end<height:
                                candidates=[boundary for boundary in boundaries if y+600<=boundary<=end]
                                if candidates:end=max(candidates)
                            target=output/(str(len(paths)+1)+'.png')
                            await page.screenshot(path=target,clip={'x':0,'y':y,'width':1000,'height':end-y})
                            paths.append(target)
                            y=end
                        if len(paths)>8:raise ValueError('Rendered content exceeds 8 pages')
                        return paths
                    finally:
                        await browser.close()
