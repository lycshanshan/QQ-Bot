from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class BiliError(Exception):
    def __init__(self,code):self.code=code


class BiliLive:
    def snapshot(self,room_id: str) -> dict:
        query=urllib.parse.urlencode({'room_id':room_id})
        request=urllib.request.Request('https://api.live.bilibili.com/room/v1/Room/get_info?'+query,
            headers={'User-Agent':'Mozilla/5.0 QQBot/0.1','Referer':'https://live.bilibili.com/'})
        try:
            with urllib.request.urlopen(request,timeout=15) as response:raw=response.read(2*1024*1024+1)
        except urllib.error.HTTPError as exc:raise BiliError(exc.code) from None
        if len(raw)>2*1024*1024:raise BiliError('response_too_large')
        result=json.loads(raw)
        if result.get('code')!=0:raise BiliError(result.get('code'))
        data=result['data']
        return {'room_id':str(data['room_id']),'uid':str(data['uid']),'online':data.get('live_status')==1,
                'session':str(data.get('live_time',''))+':'+str(data.get('up_session','')),
                'title':str(data.get('title',''))[:200]}

# Dynamic subscriptions are deliberately deferred: the approved target's first
# public API request returned HTTP 412. No dynamic endpoint is polled here.
