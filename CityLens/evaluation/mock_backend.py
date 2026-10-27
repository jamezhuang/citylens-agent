"""Finite in-memory business HTTP service; unexpected routes never reach a network."""
import asyncio
from copy import deepcopy
import time

import httpx


class MockBusiness:
    def __init__(self, fixtures, setup):
        self.fixtures = fixtures
        self.world = deepcopy(fixtures['worlds'][setup['fixture']])
        self.faults = setup.get('faults', [])
        self.calls = []
        self.attempts = {}
        self.started = asyncio.Event()
        self.cancelled_at = None
        self.deadline_at = None

    def operation(self, request):
        path = request.url.path
        if path == '/shop/search': return 'search_shops'
        if path == '/shop-type/list': return 'list_types'
        if path == '/shop/areas': return 'list_areas'
        if path == '/shop/batch': return 'get_shops'
        if path.startswith('/shop/'): return 'get_shop'
        if path.startswith('/voucher/list/'): return 'list_vouchers'
        if path.startswith('/voucher-order/ordinary/') or path.startswith('/voucher-order/seckill/'): return 'create_voucher_order'
        if path.startswith('/voucher-order/'): return 'query_voucher_order'
        if path.startswith('/blog/'): return 'search_blogs' if path == '/blog/search' else 'list_blogs'
        if path == '/user/me': return 'current_user'
        return 'unsupported_route'

    async def __call__(self, request):
        op = self.operation(request)
        self.attempts[op] = self.attempts.get(op, 0) + 1
        call = {'operation': op, 'kind': 'read' if request.method == 'GET' else 'write',
                'params': dict(request.url.params), 'started_monotonic': time.monotonic(),
                'started_after_cancel': self.cancelled_at is not None,
                'started_after_deadline': self.deadline_at is not None and time.monotonic() >= self.deadline_at}
        self.calls.append(call)
        try:
            for fault in self.faults:
                if fault['operation'] != op: continue
                if fault.get('shop_id') is not None and request.url.path != f"/shop/{fault['shop_id']}": continue
                attempts = fault.get('attempts', 'all')
                if isinstance(attempts, list) and self.attempts[op] not in attempts: continue
                effect = fault['effect']
                if effect == 'timeout': raise httpx.ReadTimeout('Synthetic business timeout', request=request)
                if effect == 'wait_for_cancellation':
                    self.started.set()
                    await asyncio.Event().wait()
                if effect == 'http_error':
                    return self.finish(call, httpx.Response(fault['http_status'], headers={'Retry-After': str(fault.get('retry_after_seconds', 0))}))
                if effect == 'business_rejected':
                    return self.finish(call, httpx.Response(200, json={'success': False, 'errorMsg': fault['error']}))
                if effect == 'return_world':
                    shop = self.fixtures['worlds'][fault['world']]['shops'][str(fault['shop_id'])]
                    return self.finish(call, self.ok(shop))
                if effect == 'forced_shop_ids':
                    rows = [self.world['shops'][str(id)] for id in fault['ids']]
                    return self.finish(call, self.ok(rows, total=len(rows)))
            response = self.route(request)
            for fault in self.faults:
                if fault['operation'] == op and fault['effect'] == 'commit_then_timeout' and self.attempts[op] in fault.get('attempts', [1]):
                    raise httpx.ReadTimeout('Synthetic response loss after mock commit', request=request)
            return self.finish(call, response)
        except BaseException as exc:
            call.update({'finished_monotonic': time.monotonic(), 'error': type(exc).__name__})
            raise

    @staticmethod
    def ok(data, **kwargs):
        return httpx.Response(200, json={'success': True, 'data': deepcopy(data), **kwargs})

    @staticmethod
    def finish(call, response):
        call.update({'finished_monotonic': time.monotonic(), 'http_status': response.status_code})
        try: call['business_success'] = response.json().get('success')
        except (ValueError, AttributeError): pass
        return response

    def route(self, request):
        p, q = request.url.path, request.url.params
        actor = request.headers.get('authorization', '').removeprefix('synthetic-')
        user_id = self.fixtures['actors'].get(actor, {}).get('user_id')
        if p == '/user/me':
            return self.ok({'id': user_id, 'nickName': actor}) if user_id else httpx.Response(401)
        if p == '/shop-type/list': return self.ok(self.world['types'])
        if p == '/shop/areas': return self.ok(sorted({shop['area'] for shop in self.world['shops'].values()}))
        if p == '/shop/search':
            rows = [shop for shop in self.world['shops'].values() if shop['typeId'] == int(q['typeId'])]
            for key, field, predicate in [('keyword', 'name', lambda a,b: b in a), ('area','area',lambda a,b:b in a),
                                           ('maxPrice','avgPrice',lambda a,b:a is not None and a <= float(b)),
                                           ('minScore','score',lambda a,b:a is not None and a >= float(b))]:
                if q.get(key): rows = [shop for shop in rows if predicate(shop.get(field), q[key])]
            if q.get('hasVoucher') == 'true':
                rows = [shop for shop in rows if any(v['shopId'] == shop['id'] and v['status'] == 1 for v in self.world['vouchers'].values())]
            rows.sort(key=(lambda shop: (shop['avgPrice'], -shop['score'], shop['id'])) if q.get('sort') == 'price'
                      else lambda shop: (-shop['score'], shop['avgPrice'], shop['id']))
            # A phase-injection fixture arranges the designated first candidate;
            # it does not pre-populate verification results or skip a real read.
            phase = next((f for f in self.faults if f['effect'] == 'partial_result_deadline'), None)
            if phase:
                rows.sort(key=lambda shop: shop['id'] not in phase['verified_ids'])
            start, size = (int(q.get('page', 1))-1)*int(q.get('size', 10)), int(q.get('size', 10))
            return self.ok(rows[start:start+size], total=len(rows))
        if p == '/shop/batch': return self.ok([self.world['shops'][id] for id in q.get('ids','').split(',') if id in self.world['shops']])
        if p.startswith('/shop/'): return self.ok(self.world['shops'].get(p.rsplit('/',1)[1]))
        if p.startswith('/voucher/list/'):
            return self.ok([v for v in self.world['vouchers'].values() if v['shopId'] == int(p.rsplit('/',1)[1])])
        if p in {'/blog/of/shop','/blog/search'}:
            return self.ok([b for b in self.world['blogs'] if b['shopId']==int(q['shopId']) and
                            (p != '/blog/search' or q.get('keyword','') in b['title']+b['content'])])
        if p.startswith('/voucher-order/ordinary/') and request.method == 'POST':
            if not user_id: return httpx.Response(401)
            voucher_id = int(p.rsplit('/',1)[1])
            old = next((o for o in self.world['orders'] if o['userId']==user_id and o['voucherId']==voucher_id),None)
            if old: return self.ok(old['id'])
            voucher = self.world['vouchers'][str(voucher_id)]
            order = {'id': 800000+len(self.world['orders']), 'userId': user_id, 'voucherId': voucher_id,
                     'status': 2 if voucher['payValue']==0 else 1}
            self.world['orders'].append(order)
            return self.ok(order['id'])
        if p in {'/voucher-order/mine','/voucher-order/mine/vouchers'}:
            if not user_id: return httpx.Response(401)
            rows = [o for o in self.world['orders'] if o['userId']==user_id and (not p.endswith('/vouchers') or o['status']==2)]
            return self.ok(rows,total=len(rows))
        if p.startswith('/voucher-order/of/voucher/'):
            return self.ok(next((o for o in self.world['orders'] if o['userId']==user_id and o['voucherId']==int(p.rsplit('/',1)[1])),None))
        if p.startswith('/voucher-order/') and request.method=='GET':
            return self.ok(next((o for o in self.world['orders'] if o['userId']==user_id and str(o['id'])==p.rsplit('/',1)[1]),None))
        return httpx.Response(501, json={'success':False,'errorMsg':'Mock route not implemented'})
