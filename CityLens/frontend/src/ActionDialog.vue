<script setup lang="ts">
import {ref} from 'vue'
import {api,money,statusName} from './api'
import {useRouter} from 'vue-router'
const router=useRouter()
const props=defineProps<{draft:any}>(),emit=defineEmits(['close','changed'])
const busy=ref(false),state=ref<any>(null),error=ref(''),submitted=ref(false)
async function confirm(){busy.value=true;error.value='';submitted.value=true;try{state.value=await api(`/api/actions/${props.draft.action_id}/confirm`,'POST');emit('changed')}catch(e){error.value=(e as Error).message+'；请查询状态，避免重复提交。'}finally{busy.value=false}}
async function refresh(){busy.value=true;try{state.value=await api(`/api/actions/${props.draft.action_id}`);emit('changed')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
</script>
<template><div class="overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="action-title"><button class="close" :disabled="busy" @click="emit('close')" aria-label="关闭">×</button><div class="eyebrow">REVIEW YOUR ORDER</div><h2 id="action-title">{{draft.operation||'确认操作'}}</h2><div class="summary" v-if="draft.voucher"><b>{{draft.shop?.name}}</b><p>{{draft.voucher?.title}}</p><div class="price">¥{{money(draft.voucher?.pay_cents)}}</div><p>券面 ¥{{money(draft.voucher?.face_cents)}}</p><p>{{draft.voucher?.rules||'请核对商家使用条件'}}</p></div><p v-if="draft.order_id">订单 {{draft.order_id}}</p><p class="muted small">{{draft.notice}} 付费订单支付后才进入券包；本项目仅支持模拟支付。</p><p class="error" role="alert" v-if="error">{{error}}</p><div v-if="state" class="summary"><p>{{state.state==='submitted'?'操作已提交':'当前操作状态：'+state.state}}</p><p v-if="state.order_id">订单 {{state.order_id}}</p><p v-if="state.order_status">{{statusName(state.order_status)}}</p><button class="text-button" @click="router.push('/orders');emit('close')">查看我的订单 →</button></div><div class="actions"><button class="outline" :disabled="busy" @click="emit('close')">关闭</button><button v-if="submitted" class="outline" :disabled="busy" @click="refresh">查询订单状态</button><button v-else class="primary" :disabled="busy" @click="confirm">{{draft.order_id?'明确确认取消订单':draft.voucher?.pay_cents===0?'明确确认领取':'明确确认下单'}}</button></div></section></div></template>
