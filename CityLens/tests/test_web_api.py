import asyncio
from types import SimpleNamespace
import httpx
from citylens.api import app, require_user
from citylens.dianping import DianpingClient


def test_frontend_bff_forwards_cookie_without_exposing_token_and_rejects_csrf():
    async def run():
        seen=[]
        def backend(request):
            seen.append((request.method,request.url.path))
            assert request.headers['authorization']=='owner-token'
            return httpx.Response(200,json={'success':True,'data':[],'total':0})
        client=DianpingClient('http://java',transport=httpx.MockTransport(backend))
        old=getattr(app.state,'workflow',None)
        app.state.workflow=SimpleNamespace(client=client)
        app.dependency_overrides[require_user]=lambda:{'id':7}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://citylens',cookies={'citylens_dp_token':'owner-token'}) as c:
                result=await c.get('/api/catalog/shops?keyword=火锅')
                assert result.status_code==200 and 'owner-token' not in result.text
                assert seen==[('GET','/shop/catalog')]
                assert (await c.put('/api/community/blogs/1/like',headers={'Origin':'http://evil.example'})).status_code==403
                assert len(seen)==1
                assert (await c.get('/api/community/blogs?feed=admin')).status_code==422
                assert (await c.post('/api/community/blogs',json={'shopId':5,'title':'标题','content':'正文','images':'../../x'})).status_code==422
                assert (await c.post('/api/community/images',json={'data':'PHN2Zz4='})).status_code==422
                assert len(seen)==1
        finally:
            app.dependency_overrides.pop(require_user,None);app.state.workflow=old;await client.close()
    asyncio.run(run())


def test_unified_business_pages_require_login():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://citylens') as c:
            for path in ['/api/catalog/types','/api/catalog/shops','/api/community/blogs']:
                assert (await c.get(path)).status_code==401
    asyncio.run(run())


def test_publish_uses_only_validated_fields_and_current_login():
    async def run():
        seen=[]
        def backend(request):
            assert request.headers['authorization']=='owner'
            if request.method=='GET':
                return httpx.Response(200,json={'success':True,'data':{'id':5}})
            import json
            fields=json.loads(request.content)
            assert set(fields)=={'shopId','title','content','images'}
            assert fields['shopId']==5
            seen.append(fields)
            return httpx.Response(200,json={'success':True,'data':42})
        client=DianpingClient('http://java',transport=httpx.MockTransport(backend))
        old=getattr(app.state,'workflow',None);app.state.workflow=SimpleNamespace(client=client)
        app.dependency_overrides[require_user]=lambda:{'id':7}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://citylens',headers={'authorization':'owner'}) as c:
                r=await c.post('/api/community/blogs',json={'shopId':5,'title':'测试笔记','content':'测试正文','images':'/blogs/1/2/1234-abcd.png','userId':999})
                assert r.status_code==200 and r.json()['data']==42 and len(seen)==1
        finally:
            app.dependency_overrides.pop(require_user,None);app.state.workflow=old;await client.close()
    asyncio.run(run())
