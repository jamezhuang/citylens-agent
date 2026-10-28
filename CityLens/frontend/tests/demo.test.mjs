import { test } from 'node:test'
import assert from 'node:assert/strict'
import {demoApi,demoChat,demoPrompts,demoSnapshot,resetDemo} from '../src/demo.mjs'

test('complete demo retains session and requires confirmation before creating exactly one order',async()=>{
  resetDemo()
  let session_id
  for(const message of demoPrompts){
    const r=await demoChat({message,session_id})
    if(session_id)assert.equal(r.session_id,session_id)
    session_id=r.session_id
    if(message===demoPrompts[1]){
      assert.equal(r.plan.max_price,100)
      assert.ok(r.shops.length>0)
      assert.ok(r.shops.every(s=>s.avg_price<=100))
    }
    if(message===demoPrompts[2]){
      assert.ok(r.itineraries.length>0)
      assert.ok(r.itineraries.every(i=>i.estimated_total<=400&&i.stops.length===2))
    }
  }
  const draft=await demoApi('/api/catalog/prepare','POST',{shop_id:101,voucher_id:9001})
  assert.equal(draft.state,'prepared')
  assert.equal((await demoApi('/api/my/orders')).total,0)
  const first=await demoApi(`/api/actions/${draft.action_id}/confirm`,'POST')
  const second=await demoApi(`/api/actions/${draft.action_id}/confirm`,'POST')
  assert.equal(first.order_id,second.order_id)
  assert.equal((await demoApi('/api/my/vouchers')).total,1)
  assert.equal(demoSnapshot().writes,1)
  const another=await demoApi('/api/catalog/prepare','POST',{shop_id:101,voucher_id:9001})
  assert.equal((await demoApi(`/api/actions/${another.action_id}/confirm`,'POST')).order_id,first.order_id)
  assert.equal(demoSnapshot().writes,1)
  resetDemo()
  assert.equal((await demoApi('/api/my/orders')).total,0)
  assert.equal((await demoApi('/api/sessions')).sessions.length,0)
})

test('unsupported prompts explain the recording boundary and do not invent results',async()=>{
  resetDemo()
  const result=await demoChat({message:'帮我预订真实包厢'})
  assert.equal(result.shops.length,0)
  assert.match(result.answer,/固定案例/)
  await assert.rejects(demoApi('/api/catalog/prepare','POST',{shop_id:101,voucher_id:9002}))
  await assert.rejects(demoApi('/api/actions/missing/confirm','POST'))
  assert.equal(demoSnapshot().writes,0)
  const controller=new AbortController();controller.abort()
  await assert.rejects(demoChat({message:demoPrompts[0]},controller.signal),{name:'AbortError'})
})

test('catalog filtering, pagination and details work without network services',async()=>{
  const result=await demoApi('/api/catalog/shops?keyword=KTV&typeId=2&sort=price&page=1')
  assert.ok(result.total>0)
  assert.ok(result.data.every(s=>s.typeId===2))
  assert.equal((await demoApi('/api/catalog/shops?page=100')).data.length,0)
  const detail=await demoApi('/api/catalog/shops/101')
  assert.ok(detail.vouchers.find(v=>v.id===9001&&v.orderable))
  assert.equal(detail.vouchers.find(v=>v.id===9002).orderable,false)
})
