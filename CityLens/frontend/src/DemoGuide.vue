<script setup lang="ts">
import {ref,onUnmounted} from 'vue'
import {useRouter} from 'vue-router'
import {demoPrompts,demoSnapshot,subscribeDemo} from './demo.mjs'
const props=defineProps<{busy:boolean}>(), emit=defineEmits<{next:[text:string];prepare:[];reset:[]}>()
const router=useRouter(),state=ref(demoSnapshot()),expanded=ref(false)
const unsubscribe=subscribeDemo(value=>state.value=value)
onUnmounted(unsubscribe)
const labels=['找店','调整预算','规划行程','查询优惠','预览领券','确认领取','查看回执']
function next(){
  if(props.busy)return
  if(state.value.stage<4)emit('next',demoPrompts[state.value.stage])
  else if(state.value.stage<6)emit('prepare')
  else router.push('/orders')
}
</script>
<template>
<section class="demo-guide" aria-labelledby="demo-heading">
  <div class="demo-guide-head"><div><div class="eyebrow">CITYLENS / INTERACTIVE DEMO</div><h2 id="demo-heading">一顿火锅，一场聚会，一个完整的 Agent 案例。</h2><p>从找店到领券，亲手走完一遍。无需登录或安装。</p></div><span class="demo-online"><i></i>在线体验</span></div>
  <ol class="demo-steps" aria-label="演示进度"><li v-for="(label,index) in labels" :key="label" :class="{done:state.stage>index,current:state.stage===index}"><span>{{state.stage>index?'✓':index+1}}</span>{{label}}</li></ol>
  <div class="demo-next"><div><b>{{state.stage<4?demoPrompts[state.stage]:state.stage===4?'预览甲火锅的免费券':state.stage===5?'在弹窗中明确确认领取': '演示订单已生成，查看回执与券包'}}</b><small>{{state.stage===0?'点击开始，系统会发送第一条需求。':state.stage===5?'确认前不会生成订单；关闭弹窗后可重新打开。':state.stage>=6?'模拟订单 '+state.orderCount+' 笔 · 提交 '+state.writes+' 次 · 刷新即重置':'点击继续，保留当前会话并进入下一步。'}}</small></div><button class="primary" :disabled="busy" @click="next">{{busy?'正在展示…':state.stage===0?'开始完整案例 →':state.stage<4?'继续下一步 →':state.stage<6?'打开领券预览 →':'查看我的订单 →'}}</button></div>
  <div class="demo-guide-foot"><button class="text-button" :disabled="busy" @click="emit('reset')">重新开始</button><button class="text-button" @click="expanded=!expanded" :aria-expanded="expanded">{{expanded?'收起演示说明':'演示方式与边界'}} {{expanded?'−':'+'}}</button><span>案例回放 · 模拟交易</span></div>
  <p v-if="expanded" class="demo-explanation">这里复用 CityLens 网站，展示已通过验收的规则 Agent 实际执行记录。对话为固定案例回放，领券和订单在当前浏览器内模拟；不会调用实时大模型、保存个人信息或产生真实权益。你也可以从侧栏自由浏览店铺、笔记和券包。查看运行记录可了解原案例的工具调用。</p>
</section>
</template>
