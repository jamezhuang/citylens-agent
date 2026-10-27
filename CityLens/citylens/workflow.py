"""找店工作流：理解需求 → 调业务工具 → 硬条件筛选 → 排序 → 解释。"""

from __future__ import annotations

from datetime import datetime
import asyncio
from itertools import product
import math
import re
from typing import Any, Callable
from urllib.parse import quote

from .dianping import DianpingClient, DianpingError
from .errors import classify_error
from .harness import AgentHarness, HarnessError
from .run_context import RunContext
from .tools import discovery_tools
from .planner import Planner
from .planner import UNVERIFIED_PREFERENCES
from .runtime import AgentRuntime
from .runs import RunStore
from .checkpoints import CheckpointStore
from .memory import UserMemoryStore, apply_memory
from .business_state import BusinessStateStore
from .storage import StateDatabase
from .schedule import default_duration, display_time, minute_of_day, open_for_visit
from .schemas import CandidateDecision, ChatRequest, ChatResponse, ItineraryCard, RelaxationOption, SearchPlan, ShopCard
from .session import SessionAccessError, SessionStore, constraints, is_followup, merge_followup
from .tool_args import BlogSearchArgs, SearchShopsArgs, ShopIdArgs, ShopIdsArgs


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _active_coupons(coupons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = []
    for coupon in coupons:
        if coupon.get("type") and _number(coupon.get("stock")) == 0:
            continue
        begin = coupon.get("beginTime")
        end = coupon.get("endTime")
        try:
            begin_dt = datetime.fromisoformat(str(begin)) if begin else None
            end_dt = datetime.fromisoformat(str(end)) if end else None
            if begin_dt and begin_dt > datetime.now(begin_dt.tzinfo):
                continue
            if end_dt and end_dt < datetime.now(end_dt.tzinfo):
                continue
        except ValueError:
            continue
        active.append(coupon)
    return active


def _shop_blogs(shop: dict[str, Any], blogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """示例库的关联 ID 存在错配；引用前要求笔记正文或标题出现店名主体。"""
    shop_id = int(shop["id"])
    name = re.split(r"[（(]", str(shop.get("name") or ""), maxsplit=1)[0].strip()
    if len(name) < 3:
        return []
    return [blog for blog in blogs if isinstance(blog, dict)
            and (blog.get("shopId") is None or str(blog.get("shopId")) == str(shop_id))
            and name.lower() in (str(blog.get("title") or "") + str(blog.get("content") or "")).lower()]


def _coupon_face_saving(coupons: list[dict[str, Any]]) -> float | None:
    """只显示券面差额，不能推断叠加、门槛或最终实付。"""
    cents = [int(actual - paid) for coupon in coupons
             if (actual := _number(coupon.get("actualValue"))) is not None
             and (paid := _number(coupon.get("payValue"))) is not None
             and actual > paid >= 0]
    return round(max(cents) / 100, 2) if cents else None


def _preference_note(plan: SearchPlan, blog_map: dict[int, list[dict[str, Any]]]) -> str:
    if not plan.unverified_preferences:
        return ""
    text = " ".join(str(blog.get("title") or "") + " " + str(blog.get("content") or "")
                    for blogs in blog_map.values() for blog in blogs)
    mentioned = [name for name in plan.unverified_preferences
                 if any(alias in text for alias in UNVERIFIED_PREFERENCES[name])]
    if mentioned:
        return (" 探店笔记中提到" + "、".join(mentioned) +
                "，这是作者的主观描述，可打开笔记核对；未把它当作硬筛选条件。")
    return " 当前点评数据无法验证" + "、".join(plan.unverified_preferences) + "，以上推荐未按这些条件筛选。"


def _to_card(shop: dict[str, Any], web_url: str, coupons: list[dict[str, Any]],
             blogs: list[dict[str, Any]] | None = None,
             coupon_lookup_status: str = 'not_queried') -> ShopCard:
    price = _number(shop.get("avgPrice"))
    score = _number(shop.get("score"))
    distance = _number(shop.get("distance"))
    parts = []
    if score is not None:
        parts.append(f"评分 {score / 10:g}")
    if price is not None:
        parts.append(f"人均约 ¥{price:g}")
    if distance is not None:
        parts.append(f"距离约 {distance:g} 米")
    if coupons:
        parts.append(f"有 {len(coupons)} 张券可查看")
    face_saving = _coupon_face_saving(coupons)
    if face_saving is not None:
        parts.append(f"单张券面差额最高 ¥{face_saving:g}，适用条件待核")
    if blogs:
        parts.append(f"有 {len(blogs)} 篇探店笔记")
    images = str(shop.get("images") or "").split(",")
    shop_id = int(shop["id"])
    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    detail_fields = set(shop.get("_detail_fields") or [])
    detail_source = str(shop.get("_detail_source") or f"/shop/{shop_id}")
    evidence = [{"field": field, "value": shop[field],
                 "source": detail_source if field in detail_fields else "/shop/search",
                 "observed_at": observed_at}
                for field in ("avgPrice", "score", "area", "x", "y", "openHours") if shop.get(field) is not None]
    if coupons:
        evidence.append({"field": "coupons", "value": [item.get("id") for item in coupons],
                         "source": f"/voucher/list/{shop_id}", "observed_at": observed_at})
    for blog in (blogs or [])[:2]:
        blog_id = blog.get("id")
        if blog_id is not None:
            evidence.append({"field": "blog", "value": str(blog.get("content") or "")[:120],
                             "title": blog.get("title"), "published_at": blog.get("createTime"),
                             "url": f"{web_url.rstrip('/')}/blog-detail.html?id={quote(str(blog_id))}",
                             "source": f"/blog/of/shop?shopId={shop_id}", "observed_at": observed_at})
    return ShopCard(
        id=shop_id,
        name=str(shop.get("name") or ""),
        area=shop.get("area"),
        address=shop.get("address"),
        avg_price=int(price) if price is not None else None,
        score=round(score / 10, 1) if score is not None else None,
        distance_m=round(distance, 1) if distance is not None else None,
        image=images[0] if images and images[0].startswith(("http://", "https://")) else None,
        url=f"{web_url.rstrip('/')}/shop-detail.html?id={quote(str(shop_id))}",
        reason=" · ".join(parts) or "查看店铺详情",
        coupons=coupons,
        coupon_lookup_status='available' if coupons else coupon_lookup_status,
        coupon_face_saving_yuan=face_saving,
        open_hours=shop.get("openHours"),
        evidence=evidence,
    )


def _straight_distance(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    coordinates = [_number(shop.get(field)) for shop in (left, right) for field in ("x", "y")]
    if any(value is None for value in coordinates):
        return None
    lon1, lat1, lon2, lat2 = map(math.radians, coordinates)
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def _apply_detail(shop: dict[str, Any], detail: dict[str, Any] | None,
                  source: str | None = None) -> dict[str, Any]:
    try:
        same_shop = isinstance(detail, dict) and int(detail.get("id") or 0) == int(shop["id"])
    except (TypeError, ValueError):
        same_shop = False
    if not same_shop:
        return shop
    merged = dict(shop)
    fields = ("name", "typeId", "area", "address", "avgPrice", "score", "x", "y", "images", "openHours")
    merged["_detail_fields"] = [field for field in fields if detail.get(field) is not None]
    if source:
        merged["_detail_source"] = source
    if source == "/shop/batch":
        # 批量详情是两站组合的当前事实；缺字段不能回退到可能过时的搜索摘要。
        merged.update({field: detail.get(field) for field in fields})
    else:
        merged.update({field: detail[field] for field in fields if detail.get(field) is not None})
    return merged


def _within_user_radius(shop, request, plan):
    if not (plan.nearby or plan.sort == 'distance'):
        return shop
    if request.longitude is None or request.latitude is None:
        return None
    distance = _straight_distance({'x': request.longitude, 'y': request.latitude}, shop)
    if distance is None or distance > 5000:
        return None
    return {**shop, 'distance': distance}


def _matches_hard_fields(shop: dict[str, Any], plan: SearchPlan, type_id: int) -> bool:
    return not _hard_reasons(shop, plan, type_id)


# 以业务字段检查已识别条件，并返回排除原因；不能弥补规划阶段漏识别的需求。
def _hard_reasons(shop: dict[str, Any], plan: SearchPlan, type_id: int) -> list[str]:
    reasons = []
    price = _number(shop.get("avgPrice"))
    score = _number(shop.get("score"))
    if shop.get("typeId") is not None and int(shop["typeId"]) != type_id:
        reasons.append("类别与请求不符")
    if ((plan.relative_price == "higher" and plan.price_reference is not None and (price is None or price <= plan.price_reference))
            or (plan.min_price is not None and (price is None or price < plan.min_price))):
        reasons.append("人均价缺失或未高于上轮推荐")
    if plan.relative_price == "lower" and plan.price_reference is not None and (price is None or price >= plan.price_reference):
        reasons.append("人均价缺失或未低于上轮推荐")
    if plan.max_price is not None and (price is None or price > plan.max_price):
        reasons.append("人均价缺失或超出上限")
    if plan.min_score is not None and (score is None or score < plan.min_score * 10):
        reasons.append("评分缺失或低于下限")
    if plan.area and plan.area not in str(shop.get("area") or "") + str(shop.get("address") or ""):
        reasons.append("不在指定商圈")
    return reasons


# 组织需求计划、候选读取和确定性筛选；模型补查只影响证据读取，不替代预算与条件校验。
class DiscoveryWorkflow:
    @staticmethod
    async def _read_tool(harness: AgentHarness, name: str, **kwargs: Any) -> Any:
        """One bounded retry for declared reads, sharing task time/call budgets."""
        for attempt in range(2):
            try:
                return await harness.call(name, **kwargs)
            except Exception as exc:
                error = classify_error(exc)
                harness.record('agent_error', tool=name, **error.to_wire())
                if attempt or not error.retryable or not harness.specs[name].read_only:
                    raise
                # Backoff is cancellable and consumes the original task deadline.
                await harness.context.execute(lambda: asyncio.sleep(0.1), harness._remaining())
                harness._remaining()
                harness.record('tool_retry', tool=name, attempt=1, code=error.code)

    def __init__(self, client: DianpingClient, planner: Planner, web_url: str,
                 storage_path: str | None = None):
        self.client = client
        self.planner = planner
        self.web_url = web_url
        database = StateDatabase(storage_path) if storage_path else None
        ttl = 30 * 86400 if database else 86400
        from .conversations import ConversationStore
        self.sessions = SessionStore(ttl_seconds=ttl, database=database)
        self.conversations = ConversationStore(self.sessions)
        self.runs = RunStore(ttl_seconds=ttl, database=database)
        self.memory = UserMemoryStore(database)
        self.checkpoints = CheckpointStore(database)
        self.business_states = BusinessStateStore(database)

    async def _single_relaxations(self, harness: AgentHarness, search_args: dict[str, Any],
                                  plan: SearchPlan, type_id: int) -> tuple[list[RelaxationOption], list[CandidateDecision]]:
        """只从同类别、同关键词的真实首批候选推导单条件放宽，不猜测全库最优。"""
        if not any((plan.max_price is not None, plan.min_score is not None, plan.area, plan.wants_coupon)):
            return [], []
        broad = await self._read_tool(harness, "search_shops", **{
            **search_args, "size": 20, "page": 1, "max_price": None,
            "min_score": None, "area": None}, has_voucher=False)
        options: list[RelaxationOption] = []
        decisions: list[CandidateDecision] = []
        one_change: dict[str, list[dict[str, Any]]] = {}
        for shop in broad["items"][:20]:
            if int(shop["id"]) in plan.excluded_shop_ids:
                continue
            reasons = _hard_reasons(shop, plan, type_id)
            if not reasons and plan.wants_coupon:
                reasons = ["当前有券筛选未返回该店；券状态需再次核对"]
            if reasons:
                decisions.append(CandidateDecision(shop_id=int(shop["id"]), name=str(shop.get("name") or "店铺"),
                                                   outcome="excluded", reasons=reasons))
            if len(reasons) == 1:
                one_change.setdefault(reasons[0], []).append(shop)
        priced = [_number(shop.get("avgPrice")) for shop in one_change.get("人均价缺失或超出上限", [])]
        priced = [value for value in priced if value is not None]
        if priced and plan.max_price is not None:
            new_price = math.ceil(min(priced))
            if new_price > plan.max_price:
                options.append(RelaxationOption(label=f"人均上限调到 ¥{new_price}",
                    message=f"人均上限改成{new_price}元，其他条件不变",
                    reason="依据同类别、同关键词的当前候选；仅放宽人均上限"))
        scored = [_number(shop.get("score")) for shop in one_change.get("评分缺失或低于下限", [])]
        scored = [value for value in scored if value is not None]
        if scored and plan.min_score is not None:
            new_score = max(scored) / 10
            if new_score < plan.min_score:
                options.append(RelaxationOption(label=f"最低评分调到 {new_score:g}",
                    message=f"最低评分改成{new_score:g}分，其他条件不变",
                    reason="依据同类别、同关键词的当前候选；仅放宽评分"))
        if one_change.get("不在指定商圈"):
            options.append(RelaxationOption(label="扩大商圈范围", message="不限商圈，其他条件不变",
                                            reason="其他条件符合的店铺位于当前商圈之外"))
        if one_change.get("当前有券筛选未返回该店；券状态需再次核对"):
            options.append(RelaxationOption(label="暂不要求有券", message="不要优惠券，其他条件不变",
                                            reason="存在其他条件符合的店铺，但当前有券筛选无结果"))
        harness.record("single_relaxation", options=len(options), inspected=len(broad["items"][:20]))
        return options[:3], decisions[:8]

    async def run(self, request: ChatRequest, owner_user_id: int | None = None,
                  on_event: Callable[[dict[str, Any]], None] | None = None,
                  resume_checkpoint: dict | None = None, extra_excluded: list[int] | None = None,
                  required_shops: list[int] | None = None, run_context: RunContext | None = None,
                  parent_span_id: str | None = None) -> ChatResponse:
        # 恢复必须基于对应账号、会话和版本，避免旧检查点覆盖最新对话计划。
        state = await self.sessions.load(request.session_id, owner_user_id)
        if resume_checkpoint and (state.id != resume_checkpoint["session_id"]
                                  or state.revision != resume_checkpoint["revision"] + 1):
            raise SessionAccessError("会话已修改，不能恢复旧计划")
        def emit(event):
            if event["event"] in ("tool_end", "candidate_validation", "itinerary_validation", "plan_resolved"):
                self.checkpoints.phase(event["run_id"], event["event"], plan)
            if on_event:
                on_event(event)
        harness = AgentHarness(discovery_tools({
            "list_types": self.client.list_types,
            "list_areas": self.client.list_areas,
            "search_shops": self.client.search_shops,
            "get_shop": self.client.get_shop,
            "get_shops": self.client.get_shops,
            "list_vouchers": self.client.list_vouchers,
            "list_blogs": self.client.list_blogs,
            "search_blogs": self.client.search_blogs,
        }), max_calls=20, on_event=emit, context=run_context, parent_span_id=parent_span_id)
        harness.record("run_start", session_id=state.id)
        if resume_checkpoint:
            plan, mode = SearchPlan.model_validate_json(resume_checkpoint["plan_json"]), "rules"
            harness.record("task_resumed", source_run_id=resume_checkpoint["run_id"],
                           phase=resume_checkpoint["phase"])
        else:
            history, _ = await self.conversations.read(state.id, owner_user_id) if owner_user_id is not None else ([], None)
            recent_users = [item["text"] for item in history if item["role"] == "user"][-6:]
            last_task = await self.runs.latest_for_session(state.id, owner_user_id, display=True)
            boundary = next((i for i in range(len(history)-1, -1, -1)
                             if history[i].get("run_id") == (last_task or {}).get("run_id")
                             and history[i]["role"] == "assistant"), -1) if last_task else -1
            # 以最近一次业务运行划分待处理消息，让澄清中补充的新条件优先参与规划。
            pending_users = [item["text"] for item in history[boundary+1:] if item["role"] == "user"][-6:]
            plan, mode = await self.planner.plan(request.message, harness=harness, context={
                "plan": state.plan.model_dump(exclude={"excluded_shop_ids", "required_shop_ids", "fixed_shop_ids", "fixed_first_shop_id"}) if state.plan else None,
                "last_shop_prices": state.last_shop_prices[:10], "recent_user_messages": recent_users, "pending_user_messages": pending_users})
        if not resume_checkpoint and request.session_id and is_followup(request.message, state, plan):
            plan = merge_followup(request.message, state.plan, plan, state.last_shop_ids,
                                  state.last_shop_prices)
        if required_shops is not None:
            plan.required_shop_ids = list(dict.fromkeys(required_shops))
        memory_applied = []
        if extra_excluded:
            plan.excluded_shop_ids = list(dict.fromkeys(plan.excluded_shop_ids + extra_excluded))
            harness.record("personal_history_filter", excluded_shop_ids=extra_excluded)
        # 跨会话偏好只用于还没有计划的新任务补缺省值，当前明确输入仍优先。
        if state.plan is None and owner_user_id is not None:
            profile = await self.memory.get(owner_user_id)
            if profile:
                memory_applied = apply_memory(plan, profile, request.message)
                if memory_applied:
                    harness.record("memory_applied", fields=memory_applied)
        harness.record("plan", stops=len(plan.stops), mode=mode)
        changes = []
        if state.plan is not None:
            for field in ("stops", "category", "keyword", "area", "max_price", "min_price", "min_score", "party_size",
                          "total_budget", "visit_time", "wants_coupon", "wants_notes", "sort"):
                before, after = state.plan.model_dump()[field], plan.model_dump()[field]
                if before != after:
                    changes.append({"field": field, "before": before, "after": after})
            if changes:
                harness.record("plan_revised", changes=changes)
        self.checkpoints.save(harness.run_id, owner_user_id, state.id, plan, state.revision)
        try:
            result = await self._execute(request, plan, mode, harness)
        except asyncio.CancelledError:
            self.checkpoints.phase(harness.run_id, "interrupted")
            raise
        except Exception:
            self.checkpoints.phase(harness.run_id, "failed")
            raise
        result.session_id = state.id
        result.run_id = harness.run_id
        result.memory_applied = memory_applied
        result.confirmed_constraints = constraints(plan)
        result.plan_changes = changes
        result.session_summary = "；".join(result.confirmed_constraints) or "尚未确认搜索条件"
        saved = await self.sessions.save(state, plan,
                                         [shop.id for shop in result.shops] if result.status in {'ok', 'partial'} else state.last_shop_ids,
                                         [shop.avg_price for shop in result.shops] if result.status in {'ok', 'partial'} else state.last_shop_prices)
        if not saved:
            harness.record("session_conflict", revision=state.revision)
            result = ChatResponse(answer="会话条件已被较新的请求修改或清空，本次方案已作废。请按当前条件重新提问。",
                                  session_id=state.id, run_id=harness.run_id, status="session_conflict",
                                  mode=mode, trace=harness.trace)
        harness.record("run_end", status=result.status)
        result.agent_outcomes = list(harness.context.outcomes)
        result.events = list(harness.events)
        await self.runs.save(result, owner_user_id)
        if result.status in {'upstream_error', 'partial'} and saved:
            self.checkpoints.save(harness.run_id, owner_user_id, state.id, plan, state.revision + 1)
            self.checkpoints.phase(harness.run_id, "retryable")
        else:
            self.checkpoints.phase(harness.run_id, "completed")
        if resume_checkpoint:
            self.checkpoints.phase(resume_checkpoint["run_id"], "resumed")
        return result

    # 已有结果直接回放；检查点恢复按保存计划重新查当前数据，不做逐工具断点续跑。
    async def resume(self, run_id: str, user_id: int, token: str | None = None) -> ChatResponse:
        record = await self.runs.get(run_id, user_id)
        if record and record.get("response") and record["status"] not in {'upstream_error', 'partial'}:
            return ChatResponse.model_validate(record["response"])
        checkpoint = self.checkpoints.get(run_id, user_id)
        if not checkpoint or checkpoint["phase"] in ("completed", "resumed"):
            raise SessionAccessError("任务不存在或不可恢复")
        required = None
        asset_harness = None
        saved_plan = SearchPlan.model_validate_json(checkpoint['plan_json'])
        if saved_plan.required_shop_ids:
            if not token:
                raise SessionAccessError('持券任务恢复需要验证当前登录身份')
            asset_harness = AgentHarness({'my_vouchers': lambda: self.client.my_orders(token, held_only=True)}, max_calls=1, context=RunContext(max_calls=20))
            asset_harness.record('run_start', source_run_id=run_id)
            page = await asset_harness.call('my_vouchers')
            active = {item['shop_id'] for item in page['items'] if item.get('shop_id')}
            required = [shop_id for shop_id in saved_plan.required_shop_ids if shop_id in active]
            if not required:
                raise SessionAccessError('原持有券已不在最近待核销记录中，请重新生成持券方案')
        result = await self.run(ChatRequest(message="恢复计划", session_id=checkpoint["session_id"]),
                                user_id, resume_checkpoint=checkpoint, required_shops=required,
                                run_context=asset_harness.context if asset_harness else None,
                                parent_span_id=asset_harness.run_id if asset_harness else None)
        if asset_harness:
            asset_harness.record('business_read_end', child_run_id=result.run_id)
            asset_harness.record('run_end', status=result.status)
            result.events = sorted(asset_harness.events + result.events, key=lambda event: event.get('sequence',0))
            result.trace = asset_harness.trace + result.trace
            await self.runs.save(result, user_id)
        return result

    async def _execute(self, request: ChatRequest, plan: SearchPlan, mode: str,
                       harness: AgentHarness) -> ChatResponse:
        # KTV 预约表达先校验必填条件；补齐后也只是筛店和查券，没有实际包厢预约接口。
        if plan.goal == "booking" and plan.category == "KTV":
            missing = []
            if not plan.party_size: missing.append("人数")
            if not plan.visit_day or plan.visit_day == "周末": missing.append("具体日期")
            if not plan.visit_time: missing.append("具体时间")
            if not (plan.max_price or plan.total_budget or plan.budget_unrestricted): missing.append("预算及其口径（总预算/人均）")
            if not (plan.area or plan.area_unrestricted or (request.longitude is not None and request.latitude is not None)):
                missing.append("地点或商圈（可明确不限）")
            if missing:
                harness.record("requirements_missing", fields=missing, goal="booking")
                return ChatResponse(answer="KTV 预订需求尚缺：" + "、".join(missing) + "。当前仅能帮助筛选和查看券，尚未接入真实包厢预约。",
                                    missing_fields=missing, status="needs_clarification", plan=plan,
                                    mode=mode, trace=harness.trace)
            if plan.total_budget and plan.party_size:
                plan.max_price = min(plan.max_price or plan.total_budget, max(1, plan.total_budget // plan.party_size))
        if len(plan.stops) > 3:
            return ChatResponse(answer="目前最多支持三站方案，请先保留最重要的三站。", plan=plan,
                                status="unsupported_request", mode=mode, trace=harness.trace)
        if plan.relative_price and (plan.price_reference is None or len(plan.stops) >= 2):
            return ChatResponse(answer="请说明要和哪一家店的人均价比较；多站方案也请指定要调整哪一站。",
                                plan=plan, status="needs_clarification", mode=mode, trace=harness.trace)
        has_coordinates = request.longitude is not None and request.latitude is not None
        if not has_coordinates and ((plan.nearby and not plan.area) or plan.sort == "distance"):
            return ChatResponse(answer="请提供所在商圈，或在页面允许获取位置，我才能按附近筛选。", plan=plan,
                                status="needs_location", mode=mode, trace=harness.trace)
        if not plan.category:
            return ChatResponse(answer="你想找哪一类店？例如美食、KTV、健身运动或亲子游乐。也可以补充预算和商圈。",
                                plan=plan, status="no_results", mode=mode, trace=harness.trace)
        try:
            if len(plan.stops) >= 2:
                return await self._run_itinerary(request, plan, mode, harness)
            if len(plan.stops) == 1 and plan.stops[0].max_price is not None:
                plan.max_price = min(plan.max_price or plan.stops[0].max_price, plan.stops[0].max_price)
            types = await self._read_tool(harness, "list_types")
            match = next((item for item in types if str(item.get("name", "")).lower() == plan.category.lower()), None)
            if not match:
                available = "、".join(str(item.get("name")) for item in types[:8])
                return ChatResponse(answer=f"点评目前没有“{plan.category}”这个分类。可选分类：{available}。", plan=plan,
                                    status="no_results", mode=mode, trace=harness.trace)
            search_args = dict(
                type_id=int(match["id"]), page=1, size=20 if plan.relative_price else 3,
                keyword=plan.keyword, area=plan.area, max_price=plan.max_price,
                min_score=int(round(plan.min_score * 10)) if plan.min_score is not None else None,
                longitude=request.longitude if plan.nearby and has_coordinates else None,
                latitude=request.latitude if plan.nearby and has_coordinates else None,
                sort=plan.sort,
            )
            search = await self._read_tool(harness, "search_shops", **search_args, has_voucher=plan.wants_coupon)
            candidates = [shop for shop in search["items"] if int(shop["id"]) not in plan.excluded_shop_ids]
            if plan.relative_price:
                candidates = [shop for shop in candidates if not _hard_reasons(shop, plan, int(match["id"]))]
            # 有券候选为空时可展示其他条件符合的店，但回复必须明确尚未确认有券。
            no_coupon_match = plan.wants_coupon and not candidates
            if no_coupon_match:
                search = await self._read_tool(harness, "search_shops", **search_args)
                candidates = [shop for shop in search["items"] if int(shop["id"]) not in plan.excluded_shop_ids]
            if plan.relative_price:
                candidates = [shop for shop in candidates if not _hard_reasons(shop, plan, int(match["id"]))]
            if not candidates and plan.relative_price and not plan.required_shop_ids:
                direction = "更贵" if plan.relative_price == "higher" else "更便宜"
                bound = f"仍保留人均上限 ¥{plan.max_price}。" if plan.max_price else ""
                return ChatResponse(answer=f"已沿用上一轮的类别和条件，寻找比上轮推荐{direction}的店（比较基准人均 ¥{plan.price_reference}）。"
                                          f"{bound}当前检查的同类别、同关键词最多 20 家候选中没有符合条件的店，不能编造新的推荐。"
                                          "你可以调整商圈、换一种餐饮类型，或明确修改预算。",
                                    plan=plan, status="no_results", mode=mode, trace=harness.trace)
            if not candidates and not plan.required_shop_ids:
                relaxations, decisions = await self._single_relaxations(harness, search_args, plan, int(match["id"]))
                suggestions = "；".join(option.label for option in relaxations)
                answer = "按当前条件没有找到店铺。"
                answer += (f"可选调整（尚未按新条件重新查询）：{suggestions}。" if suggestions
                           else "你可以修改当前条件或更换类别后重新查询。")
                return ChatResponse(answer=answer,
                                    plan=plan, status="no_results", mode=mode, trace=harness.trace,
                                    relaxations=relaxations, candidate_decisions=decisions)
            if plan.required_shop_ids:
                held = await self._read_tool(harness, 'get_shops', ids=plan.required_shop_ids[:20])
                candidates = [_apply_detail(shop, shop, '/shop/batch') for shop in held
                              if int(shop['id']) in plan.required_shop_ids and int(shop['id']) not in plan.excluded_shop_ids
                              and shop.get('typeId') == int(match['id'])
                              and (not plan.keyword or plan.keyword in str(shop.get('name', '')))]
                candidates = [current for shop in candidates if (current := _within_user_radius(shop,request,plan)) is not None]
                candidates.sort(key=lambda shop: (_number(shop.get('avgPrice')) or float('inf')) if plan.sort == 'price'
                                else -(_number(shop.get('score')) or 0))
            shown = candidates if plan.required_shop_ids else candidates[:3]
            runtime = AgentRuntime(self.planner.provider, harness)
            def verified_detail(shop, detail):
                if (isinstance(detail, dict) and detail.get('id') == shop['id']
                        and _number(detail.get('avgPrice')) is not None and _number(detail.get('score')) is not None):
                    current = _apply_detail(shop, detail, '/shop/batch')
                    if (not _hard_reasons(current, plan, int(match['id'])) and
                            (not plan.visit_time or open_for_visit(current.get('openHours'), minute_of_day(plan.visit_time),
                                                                 default_duration(plan.category or '')) is True)):
                        harness.record('candidate_verified', shop_id=shop['id'])
            details = {} if plan.required_shop_ids else await runtime.inspect(shown, require_open_hours=bool(plan.visit_time),
                                                                              on_detail=verified_detail)
            verification_incomplete = False
            if not plan.required_shop_ids:
                verified = []
                for shop in shown:
                    detail = details.get(int(shop['id']))
                    if (not isinstance(detail, dict) or detail.get('id') != shop['id']
                            or _number(detail.get('avgPrice')) is None or _number(detail.get('score')) is None):
                        harness.record('candidate_verification_failed', shop_id=shop['id'])
                        continue
                    if any(detail.get(field) != shop.get(field) for field in ('avgPrice', 'score')):
                        harness.record('candidate_detail_changed', shop_id=shop['id'],
                                       search_price=shop.get('avgPrice'), detail_price=detail.get('avgPrice'))
                    verified.append(shop)
                verification_incomplete = len(verified) < len(shown)
                shown = verified
                if not shown:
                    return ChatResponse(answer='候选店铺详情未能可靠核验，本次无法给出推荐；这不代表没有符合条件的店铺，请稍后重试。',
                                        plan=plan, status='upstream_error', mode=mode, trace=harness.trace)
            shown = [_apply_detail(shop, details.get(int(shop["id"]))) for shop in shown]
            if plan.required_shop_ids:
                shown = [shop for shop in shown if int(shop['id']) in plan.required_shop_ids]
            before_validation = len(shown)
            decisions: list[CandidateDecision] = []
            accepted = []
            for shop in shown:
                # 候选详情用于复核；指定时段必须可确认营业，不能仅凭搜索命中就展示。
                reasons = _hard_reasons(shop, plan, int(match["id"]))
                if not reasons and plan.visit_time and open_for_visit(
                    shop.get("openHours"), minute_of_day(plan.visit_time), default_duration(plan.category or "")) is not True:
                    reasons = ["指定时段无法确认营业"]
                if reasons:
                    decisions.append(CandidateDecision(shop_id=int(shop["id"]), name=str(shop.get("name") or "店铺"),
                                                       outcome="excluded", reasons=reasons))
                else:
                    accepted.append(shop)
            shown = accepted[:3]
            harness.record("candidate_validation", phase="single", before=before_validation, after=len(shown))
            if not shown and not plan.required_shop_ids and int(search.get("total") or 0) > 3:
                next_page = await self._read_tool(harness, "search_shops", **{**search_args, "page": 2},
                                               has_voucher=plan.wants_coupon and not no_coupon_match)
                alternates = [shop for shop in next_page["items"]
                              if int(shop["id"]) not in plan.excluded_shop_ids][:3]
                alternate_details = await runtime.inspect(alternates, require_open_hours=bool(plan.visit_time))
                alternates = [shop for shop in alternates if isinstance(alternate_details.get(int(shop['id'])), dict)
                              and alternate_details[int(shop['id'])].get('id') == shop['id']
                              and _number(alternate_details[int(shop['id'])].get('avgPrice')) is not None
                              and _number(alternate_details[int(shop['id'])].get('score')) is not None]
                alternates = [_apply_detail(shop, alternate_details.get(int(shop["id"]))) for shop in alternates]
                shown = []
                for shop in alternates:
                    reasons = _hard_reasons(shop, plan, int(match["id"]))
                    if not reasons and plan.visit_time and open_for_visit(
                        shop.get("openHours"), minute_of_day(plan.visit_time), default_duration(plan.category or "")) is not True:
                        reasons = ["指定时段无法确认营业"]
                    if reasons:
                        decisions.append(CandidateDecision(shop_id=int(shop["id"]), name=str(shop.get("name") or "店铺"),
                                                           outcome="excluded", reasons=reasons))
                    else:
                        shown.append(shop)
                harness.record("candidate_validation", phase="single_page_2",
                               before=len(alternates), after=len(shown))
            if not shown:
                if plan.required_shop_ids:
                    return ChatResponse(answer='指定店铺经核验不符合当前条件，本轮没有可推荐的指定店铺。',
                                        plan=plan, status='no_results', mode=mode, trace=harness.trace,
                                        candidate_decisions=decisions[:8])
                reason = ("指定时段无法核验候选店铺营业情况，本次无法可靠推荐。"
                          if plan.visit_time else "上游候选店铺数据与筛选条件不符，本轮已排除；不能据此判断是否有符合条件的店铺，请稍后重试。")
                return ChatResponse(answer=reason,
                                    plan=plan, status="upstream_error", mode=mode, trace=harness.trace,
                                    candidate_decisions=decisions[:8])
            if verification_incomplete:
                cards = [_to_card(shop, self.web_url, []) for shop in shown]
                codes = {event.get('code') for event in harness.events if event['event'] == 'inspection_failed'}
                explanation = ('剩余时间不足或查询超时，已停止后续核验。'
                               if codes & {'deadline_exceeded', 'insufficient_time', 'tool_timeout'}
                               else '部分详情未能读取，已停止不可靠的补查。')
                harness.record('partial_result', verified_ids=[card.id for card in cards], reason='verification_incomplete')
                return ChatResponse(answer='本轮只完成了部分候选的核验，先返回已核验的店铺：'
                                           + '；'.join(card.name for card in cards)
                                           + '。' + explanation + '其余候选及补充信息查询尚未完成，不能据此判断全部候选或全库情况。',
                                    shops=cards, plan=plan, status='partial', mode=mode, trace=harness.trace)
            coupon_ids = [int(shop["id"]) for shop in shown] if plan.wants_coupon and not no_coupon_match else []
            # 主观偏好只补关联笔记作参考，不把“安静/约会”等变成已核实硬条件。
            note_ids = [int(shop["id"]) for shop in shown] if plan.wants_notes or plan.unverified_preferences else []
            evidence_query = UNVERIFIED_PREFERENCES[plan.unverified_preferences[0]][0] if plan.unverified_preferences else None
            raw_coupons, blog_map, enrich_failed = await runtime.enrich(
                shown, coupon_ids, note_ids, evidence_query=evidence_query)
            blog_map = {int(shop["id"]): _shop_blogs(shop, blog_map.get(int(shop["id"]), [])) for shop in shown}
            harness.record("evidence_validation", accepted=sum(map(len, blog_map.values())),
                           candidate_shops=len(shown))
            if plan.unverified_preferences and any(blog_map.values()):
                shown.sort(key=lambda shop: (not bool(blog_map.get(int(shop["id"]))),
                                             -(_number(shop.get("score")) or 0)))
                harness.record("evidence_rerank", reason="preference_note_available")
            offers = [(shop, _active_coupons(raw_coupons.get(int(shop["id"]), []))) for shop in shown]
            coupon_lookup_failed = enrich_failed and bool(coupon_ids)
            notes_error = enrich_failed and bool(note_ids)
            cards = [_to_card(shop, self.web_url, coupons, blog_map.get(int(shop["id"])),
                              'empty' if int(shop['id']) in raw_coupons else
                              'failed' if int(shop['id']) in coupon_ids else 'not_queried')
                     for shop, coupons in offers[:3]]
            summary = "根据点评当前店铺数据，找到这些选择：" + "；".join(f"{card.name}（{card.reason}）" for card in cards) + "。点击卡片查看店铺详情。"
            if coupon_lookup_failed:
                summary += " 部分优惠详情暂时无法读取，请到店铺页确认。"
            elif no_coupon_match:
                summary += " 按当前条件暂未查到有可用优惠券的店铺。"
            elif plan.wants_coupon and not any(card.coupons for card in cards):
                summary += " 优惠券状态可能刚刚变化，请到店铺页确认。"
            summary += _preference_note(plan, blog_map)
            if notes_error:
                summary += " 部分探店笔记暂时无法读取。"
            elif plan.wants_notes and not any(any(item.get("field") == "blog" for item in card.evidence) for card in cards):
                summary += " 这些店暂未查到探店笔记。"
            decisions.extend(CandidateDecision(shop_id=card.id, name=card.name, outcome="selected",
                                               reasons=["符合店铺硬条件", "当前有券筛选无结果，未确认有券" if no_coupon_match
                                                        else "按当前排序进入展示结果"])
                             for card in cards)
            return ChatResponse(answer=summary, shops=cards, plan=plan, status="ok", mode=mode,
                                trace=harness.trace, candidate_decisions=decisions[:10])
        except (DianpingError, HarnessError, TimeoutError) as exc:
            error = classify_error(exc)
            messages = {
                'business_rejected': '点评接口未接受本次查询，查询未完成，请核对条件后重试。',
                'rate_limited': '点评服务请求过于频繁，本次未继续重试，请稍后再试。',
                'tool_timeout': '读取点评数据超时，本次无法完成可靠查询，请稍后重试。',
                'auth_expired': '点评登录已失效，请重新登录。',
            }
            harness.record('agent_error', **error.to_wire())
            return ChatResponse(answer=messages.get(error.code, '暂时无法完成点评数据查询，请稍后重试。'), plan=plan,
                                status='business_rejected' if error.code == 'business_rejected' else 'upstream_error',
                                mode=mode, trace=harness.trace)

    async def _run_itinerary(self, request: ChatRequest, plan: SearchPlan, mode: str,
                             harness: AgentHarness) -> ChatResponse:
        # 总预算计算依赖人数，缺人数先追问；无总预算时才可按默认 1 人估算。
        if plan.total_budget is not None and plan.party_size is None:
            return ChatResponse(answer="请补充同行人数，我才能按总预算计算多站方案；例如“2 人，总预算 400 元”。",
                                plan=plan, status="needs_clarification", mode=mode, trace=harness.trace)
        party_size = plan.party_size or 1
        types = await self._read_tool(harness, "list_types")
        type_ids = {str(item["name"]).lower(): int(item["id"]) for item in types}
        for stop in plan.stops:
            if stop.category.lower() not in type_ids:
                return ChatResponse(answer=f"点评暂时没有“{stop.category}”分类，无法完成多站规划。",
                                    plan=plan, status="no_results", mode=mode, trace=harness.trace)
        if not plan.area:
            areas = await self._read_tool(harness, "list_areas")
            plan.area = next((area for area in sorted(areas, key=len, reverse=True) if area in request.message), None)
            harness.record("plan_resolved", area=plan.area)

        held_candidates = await self._read_tool(harness, 'get_shops', ids=plan.required_shop_ids[:20]) if plan.required_shop_ids else []
        candidate_groups: list[list[dict[str, Any]]] = []
        for index, stop in enumerate(plan.stops):
            price_limit = min(value for value in (plan.max_price, stop.max_price) if value is not None) if (
                plan.max_price is not None or stop.max_price is not None) else None
            search = await self._read_tool(harness, 
                "search_shops", type_id=type_ids[stop.category.lower()], page=1, size=20,
                keyword=stop.keyword, area=plan.area, max_price=price_limit,
                min_score=int(round(plan.min_score * 10)) if plan.min_score is not None else None,
                longitude=request.longitude if (plan.nearby or plan.sort == "distance") else None,
                latitude=request.latitude if (plan.nearby or plan.sort == "distance") else None,
                sort=plan.sort,
            )
            candidates = [shop for shop in search["items"] if int(shop["id"]) not in plan.excluded_shop_ids]
            extras = [shop for shop in held_candidates if int(shop['id']) not in plan.excluded_shop_ids
                      and shop.get('typeId') == type_ids[stop.category.lower()]
                      and (not stop.keyword or stop.keyword in str(shop.get('name', '')))]
            candidates = list({int(shop['id']): shop for shop in candidates + extras}.values())
            pinned = (plan.fixed_shop_ids[index] if index < len(plan.fixed_shop_ids) else
                      plan.fixed_first_shop_id if index == 0 else None)
            if pinned is not None and not any(int(shop["id"]) == pinned for shop in candidates):
                detail = await self._read_tool(harness, "get_shop", shop_id=pinned)
                if (detail and int(detail.get("id", 0)) == pinned and pinned not in plan.excluded_shop_ids
                        and (not stop.keyword or stop.keyword in str(detail.get("name", "")))):
                    candidates.append(detail)
            candidates = [current for shop in candidates if (current := _within_user_radius(shop,request,plan)) is not None]
            if index == 0 and plan.fixed_first_shop_id is not None:
                candidates = [shop for shop in candidates if int(shop["id"]) == plan.fixed_first_shop_id]
            if index < len(plan.fixed_shop_ids) and plan.fixed_shop_ids[index] is not None:
                candidates = [shop for shop in candidates if int(shop["id"]) == plan.fixed_shop_ids[index]]
            if index == 1 and plan.second_price_ceiling is not None:
                candidates = [shop for shop in candidates if (_number(shop.get("avgPrice")) is not None
                              and _number(shop.get("avgPrice")) < plan.second_price_ceiling)]
            if index in plan.stop_price_ceilings:
                candidates = [shop for shop in candidates if (_number(shop.get("avgPrice")) is not None
                              and _number(shop.get("avgPrice")) < plan.stop_price_ceilings[index])]
            if not candidates:
                detail = ("更便宜的替代店铺" if index in plan.stop_price_ceilings or
                          index == 1 and plan.second_price_ceiling is not None
                          else "符合条件的店铺")
                return ChatResponse(answer=f"“{stop.category}”这一站没有{detail}。可以放宽商圈、预算或评分。",
                                    plan=plan, status="no_results", mode=mode, trace=harness.trace)
            candidate_groups.append(candidates)

        runtime = AgentRuntime(self.planner.provider, harness)
        candidate_ids = list(dict.fromkeys(int(shop["id"]) for group in candidate_groups for shop in group))
        batch = []
        # 按批量接口的 40 个 ID 上限刷新详情，组合使用当前字段而非旧搜索快照。
        for offset in range(0, len(candidate_ids), 40):
            chunk = await self._read_tool(harness, "get_shops", ids=candidate_ids[offset:offset + 40])
            if not isinstance(chunk, list):
                raise HarnessError("店铺批量详情格式不正确")
            batch.extend(chunk)
        details = {int(shop["id"]): shop for shop in batch
                   if isinstance(shop, dict) and shop.get("id") is not None
                   and int(shop["id"]) in candidate_ids}
        harness.record("candidate_detail_batch", requested=len(candidate_ids), returned=len(details))
        decisions: list[CandidateDecision] = []
        candidate_summary: list[dict[str, int]] = []
        for index, group in enumerate(candidate_groups):
            category_id = type_ids[plan.stops[index].category.lower()]
            valid = []
            for shop in group:
                current = details.get(int(shop["id"]))
                if current is None:
                    reasons = ["批量详情缺失，无法复核"]
                else:
                    checked = _apply_detail(shop, current, source="/shop/batch")
                    reasons = _hard_reasons(checked, plan, category_id)
                    if plan.stops[index].max_price is not None and (
                            _number(checked.get("avgPrice")) is None or
                            _number(checked.get("avgPrice")) > plan.stops[index].max_price):
                        reasons.append("超过本站人均价格上限")
                    if not checked.get("name") or checked.get("avgPrice") is None:
                        reasons.append("缺少名称或人均价")
                    if index == 1 and plan.second_price_ceiling is not None and (
                        _number(checked.get("avgPrice")) is None or
                        _number(checked.get("avgPrice")) >= plan.second_price_ceiling):
                        reasons.append("第二站价格未低于指定上限")
                    if index in plan.stop_price_ceilings and (
                        _number(checked.get("avgPrice")) is None or
                        _number(checked.get("avgPrice")) >= plan.stop_price_ceilings[index]):
                        reasons.append(f"第 {index + 1} 站价格未低于指定上限")
                if reasons:
                    decisions.append(CandidateDecision(shop_id=int(shop["id"]), name=str(shop.get("name") or "店铺"),
                                                       outcome="excluded", reasons=reasons))
                else:
                    valid.append(checked)
            harness.record("candidate_validation", phase=f"stop_{index + 1}",
                           before=len(group), after=len(valid))
            candidate_summary.append({"stop": index + 1, "searched": len(group),
                                      "verified": len(valid), "excluded": len(group) - len(valid)})
            if not valid:
                return ChatResponse(answer=f"“{plan.stops[index].category}”的候选详情不符合当前条件。请调整预算或评分。",
                                    plan=plan, status="no_results", mode=mode, trace=harness.trace,
                                    candidate_decisions=decisions[:10])
            candidate_groups[index] = valid

        combinations = []
        time_rejected = 0
        held_rejected = 0
        cheapest_feasible_total: int | None = None
        # 逐组合校验持券关联、不重复门店、预算和时段；只是有限候选集，不保证全库最优。
        for shops in product(*candidate_groups):
            if plan.required_shop_ids and not any(int(shop['id']) in plan.required_shop_ids for shop in shops):
                held_rejected += 1
                continue
            if len({int(shop["id"]) for shop in shops}) != len(shops):
                continue
            prices = [_number(shop.get("avgPrice")) for shop in shops]
            if any(price is None for price in prices):
                continue
            # 总价按人数乘各站人均参考价之和；未校验券使用门槛，不预扣优惠。
            estimated_total = round(sum(prices) * party_size)
            leg_distances = [_straight_distance(left, right) for left, right in zip(shops, shops[1:])]
            distance = round(sum(leg_distances), 1) if all(item is not None for item in leg_distances) else None
            visit_times = []
            if plan.visit_time:
                start = minute_of_day(plan.visit_time)
                for index, shop in enumerate(shops):
                    duration = default_duration(plan.stops[index].category)
                    if open_for_visit(shop.get("openHours"), start, duration) is not True:
                        time_rejected += 1
                        break
                    visit_times.append(display_time(start))
                    # 停留时间与 30 分钟转场是估算假设，不是导航时长或预约承诺。
                    start += duration + 30
                if len(visit_times) != len(shops):
                    continue
            cheapest_feasible_total = (estimated_total if cheapest_feasible_total is None else
                                       min(cheapest_feasible_total, estimated_total))
            if plan.total_budget is not None and estimated_total > plan.total_budget:
                continue
            combined_score = sum((_number(shop.get("score")) or 0) for shop in shops)
            combinations.append((shops, estimated_total, distance, combined_score, visit_times, leg_distances))
        harness.record("itinerary_validation", combinations=len(combinations), time_rejected=time_rejected, held_rejected=held_rejected,
                       cheapest_feasible_total=cheapest_feasible_total)
        if not combinations:
            if (plan.total_budget is not None and cheapest_feasible_total is not None
                    and cheapest_feasible_total > plan.total_budget):
                difference = cheapest_feasible_total - plan.total_budget
                return ChatResponse(
                    answer=(f"{len(plan.stops)} 站都有候选，但按当前人数和店铺人均价，符合其余条件的最便宜组合约 ¥{cheapest_feasible_total}，"
                            f"比总预算高 ¥{difference}。你可以确认提高预算后重新规划。"),
                    plan=plan, status="no_results", mode=mode, trace=harness.trace,
                    relaxations=[RelaxationOption(
                        label=f"将总预算提高到 ¥{cheapest_feasible_total}",
                        message=f"总预算{cheapest_feasible_total}元，其他条件不变",
                        reason=f"根据当前点评候选计算，至少还需 ¥{difference}")],
                    candidate_decisions=decisions[:10])
            reason = ("当前候选无法组成至少一站关联持有券的方案，请调整类别、商圈或换店条件。" if held_rejected and cheapest_feasible_total is None and not time_rejected
                      else "各站候选在指定时段无法确认营业；请调整到店时间或更换商圈。" if time_rejected
                      else "各站都找到候选，但按人均价估算后没有符合总预算的组合。可以提高总预算或更换类别。"
                      if plan.total_budget is not None else "候选缺少可计算的价格或没有可组合的店铺。")
            return ChatResponse(answer=reason, plan=plan, status="no_results", mode=mode,
                                trace=harness.trace, candidate_decisions=decisions[:10])

        prefer_near = any(word in request.message for word in ("近", "距离", "少走")) or plan.sort == "distance"
        if prefer_near:
            combinations.sort(key=lambda item: (item[2] is None, item[2] if item[2] is not None else float("inf"),
                                                item[1], -item[3]))
        elif plan.sort == "price":
            combinations.sort(key=lambda item: (item[1], -item[3], item[2] if item[2] is not None else float("inf")))
        else:
            combinations.sort(key=lambda item: (-item[3], item[1], item[2] if item[2] is not None else float("inf")))
        selected = combinations[:3]
        unique_shops = list({int(shop["id"]): shop for combination in selected
                             for shop in combination[0]}.values())
        # Keep enrichment inside the harness tool budget; every selected card still has batch details.
        unique_shops = unique_shops[:6]
        coupon_ids = [int(shop["id"]) for shop in unique_shops] if plan.wants_coupon else []
        note_ids = [int(shop["id"]) for shop in selected[0][0]] if plan.wants_notes or plan.unverified_preferences else []
        evidence_query = UNVERIFIED_PREFERENCES[plan.unverified_preferences[0]][0] if plan.unverified_preferences else None
        raw_coupons, blog_map, notes_error = await runtime.enrich(
            unique_shops, coupon_ids, note_ids, evidence_query=evidence_query)
        blog_map = {int(shop["id"]): _shop_blogs(shop, blog_map.get(int(shop["id"]), []))
                    for shop in unique_shops}
        harness.record("evidence_validation", accepted=sum(map(len, blog_map.values())),
                       candidate_shops=len(unique_shops))
        coupon_map = {shop_id: _active_coupons(items) for shop_id, items in raw_coupons.items()}

        itineraries = []
        for shops, estimated_total, distance, _score, visit_times, leg_distances in selected:
            cards = [_to_card(shop, self.web_url, coupon_map.get(int(shop["id"]), []),
                              blog_map.get(int(shop["id"])),
                              'empty' if int(shop['id']) in raw_coupons else
                              'failed' if int(shop['id']) in coupon_ids else 'not_queried') for shop in shops]
            reason = f"按 {party_size} 人 × {len(shops)} 站人均价估算约 ¥{estimated_total}"
            if distance is not None:
                reason += f"；相邻站直线距离合计约 {distance:g} 米"
            if visit_times:
                reason += "；建议 " + "、".join(f"{time} 到第 {index + 1} 站"
                                               for index, time in enumerate(visit_times))
            itineraries.append(ItineraryCard(stops=cards, estimated_total=estimated_total,
                                             party_size=party_size, straight_distance_m=distance, reason=reason,
                                             leg_distances_m=leg_distances, visit_times=visit_times))
        first = itineraries[0]
        ranking_basis = "直线距离" if prefer_near else "预计总价" if plan.sort == "price" else "各站评分合计"
        for index, itinerary in enumerate(itineraries):
            itinerary.reason += f"；按{ranking_basis}排序第 {index + 1}"
        decisions.extend(CandidateDecision(shop_id=shop.id, name=shop.name,
                                           outcome="selected" if index == 0 else "alternative",
                                           reasons=["通过当前硬条件复核", f"进入方案 {index + 1}，按{ranking_basis}排序"])
                         for index, itinerary in enumerate(itineraries) for shop in itinerary.stops)
        displayed_ids = {shop.id for itinerary in itineraries for shop in itinerary.stops}
        for index, group in enumerate(candidate_groups):
            candidate_summary[index]["displayed"] = sum(int(shop["id"]) in displayed_ids for shop in group)
            decisions.extend(CandidateDecision(shop_id=int(shop["id"]), name=str(shop["name"]),
                                               outcome="excluded", reasons=["通过硬条件复核；未进入前三组方案"])
                             for shop in group if int(shop["id"]) not in displayed_ids)
        answer = (f"找到 {len(itineraries)} 组{len(plan.stops)}站方案。首选："
                  + " → ".join(shop.name for shop in first.stops) + f"；{first.reason}。")
        if plan.party_size is None:
            answer += " 你尚未提供人数，目前按 1 人估算。"
        answer += " 方案仅从每站搜索第一页最多 20 家候选中计算。"
        if plan.visit_time:
            answer += (f" 时段按首站停留约 {default_duration(plan.stops[0].category)} 分钟、"
                       "转场预留 30 分钟估算；营业时间来自店铺登记，出发前请再次确认。")
        if plan.wants_coupon:
            answer += " 优惠券仅展示券面差额，使用门槛和叠加规则尚未结构化校验，未从预计总价中扣除。"
            if not any(shop.coupons for shop in first.stops):
                answer += " 首选方案目前没有查到可用优惠券。"
        answer += _preference_note(plan, blog_map)
        if notes_error:
            answer += " 部分探店笔记暂时无法读取。"
        elif plan.wants_notes and not any(any(item.get("field") == "blog" for item in shop.evidence)
                                          for shop in first.stops):
            answer += " 首选方案暂未查到探店笔记。"
        return ChatResponse(answer=answer, shops=first.stops, itineraries=itineraries, plan=plan,
                            status="ok", mode=mode, trace=harness.trace,
                            candidate_decisions=decisions[:30], candidate_summary=candidate_summary)
