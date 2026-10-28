"""Serial, isolated ASGI execution. Business traffic uses MockTransport exclusively."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace

import httpx

from .catalog import digest, load_catalog, validate_catalog
from .grading import grade_case, lookup, MISSING, summarize
from .mock_backend import MockBusiness


class RunBudget:
    def __init__(self, calls=20, tokens=60000, seconds=180):
        self.limit_calls, self.limit_tokens = calls, tokens
        self.deadline = time.monotonic()+seconds
        self.calls = self.input_tokens = self.output_tokens = self.unknown_usage_calls = 0
        self.attempts = []


class BudgetedProvider:
    def __init__(self, config, budget):
        from citylens.llm import LLMProvider
        self.inner, self.config, self.budget = LLMProvider(config), config, budget

    @property
    def enabled(self): return self.inner.enabled

    async def complete(self, system, messages, tools=None, max_tokens=500):
        budget = self.budget
        # UTF-8 bytes + output allowance conservatively reserves input text/schema
        # capacity. Actual vendor usage is accounted separately after each response.
        reserve = len(json.dumps([system,messages,tools],ensure_ascii=False).encode())+max_tokens+1024
        if budget.calls >= budget.limit_calls or time.monotonic() >= budget.deadline:
            raise RuntimeError('Evaluation model call/time budget exhausted')
        if budget.input_tokens+budget.output_tokens+reserve > budget.limit_tokens:
            raise RuntimeError('Evaluation token reserve exhausted')
        budget.calls += 1
        attempt = {'index':budget.calls, 'started':time.monotonic()}
        budget.attempts.append(attempt)
        try:
            result = await asyncio.wait_for(self.inner.complete(system,messages,tools,max_tokens),
                                            timeout=min(self.config.timeout,max(0.01,budget.deadline-time.monotonic())))
            usage = result.usage
            budget.input_tokens += usage.get('input_tokens',0)
            budget.output_tokens += usage.get('output_tokens',0)
            attempt.update({'ok':True,'usage':usage})
            return result
        except Exception as exc:
            budget.unknown_usage_calls += 1
            attempt.update({'ok':False,'error_type':type(exc).__name__})
            raise
        finally: attempt['duration_ms']=round((time.monotonic()-attempt.pop('started'))*1000)

    async def close(self): await self.inner.close()


def resolve(value, observations):
    if isinstance(value, dict):
        if '$ref' in value:
            reference=value['$ref']; result=lookup(observations[reference['step']],reference['path'])
            if result is MISSING: raise ValueError('Earlier step did not produce required reference')
            return result
        return {key:resolve(child,observations) for key,child in value.items()}
    if isinstance(value,list): return [resolve(child,observations) for child in value]
    return value


async def execute_case(case,suite,fixtures,mode,budget,config=None,on_step=None):
    from citylens import api
    from citylens import actions as action_module
    from citylens.actions import VoucherActionWorkflow
    from citylens.business import BusinessWorkflow
    from citylens.dianping import DianpingClient
    from citylens.llm import LLMConfig,LLMProvider
    from citylens.planner import Planner
    from citylens.workflow import DiscoveryWorkflow
    record={'record_version':1,'case_id':case['id'],'suite_version':suite['version'],'case_sha256':digest(case),
            'fixture_sha256':digest(fixtures),'mode':mode,'adapter_version':'asgi-mock-v1','repeat_index':0,
            'execution_status':'executed','steps':[],'reviews':{},'started_at':datetime.now(timezone.utc).isoformat()}
    backend=MockBusiness(fixtures,case['setup'])
    phase = next((f for f in case['setup'].get('faults', []) if f['effect'] == 'partial_result_deadline'), None)
    from citylens import workflow as workflow_module
    original_harness = workflow_module.AgentHarness
    injections = []
    if phase:
        class PhaseHarness(original_harness):
            def record(self, event, **details):
                super().record(event, **details)
                if event == 'candidate_verified' and details.get('shop_id') in phase['verified_ids'] and not injections:
                    now = time.monotonic()
                    self.context.max_duration = now - self.context.started + phase['remaining_ms'] / 1000
                    self.context.tool_time_estimates['get_shop'] = phase['next_round_estimate_ms'] / 1000
                    backend.deadline_at = now + phase['remaining_ms'] / 1000
                    injections.append({'after_event': event, 'shop_id': details['shop_id'],
                                       'remaining_ms': phase['remaining_ms'], 'next_round_estimate_ms': phase['next_round_estimate_ms']})
                    super().record('evaluation_phase_injected', **injections[-1])
        workflow_module.AgentHarness = PhaseHarness
    provider=BudgetedProvider(config,budget) if config else LLMProvider(LLMConfig())
    business_client=DianpingClient('https://synthetic.invalid',transport=httpx.MockTransport(backend))
    saved={key:getattr(api.app.state,key,MISSING) for key in ('workflow','actions','business','active_requests')}
    original_time=action_module.time
    virtual=[datetime.fromisoformat(case['setup']['clock']).timestamp()]
    action_module.time=SimpleNamespace(time=lambda:virtual[0])
    start=time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='citylens-eval-') as folder:
            workflow=DiscoveryWorkflow(business_client,Planner(provider=provider),'https://synthetic.invalid',str(Path(folder)/'state.sqlite3'))
            actions=VoucherActionWorkflow(business_client,workflow.runs)
            api.app.state.workflow,api.app.state.actions=workflow,actions
            api.app.state.business,api.app.state.active_requests=BusinessWorkflow(workflow,actions),{}
            observations={}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app,raise_app_exceptions=False),
                                         base_url='http://evaluation.invalid',trust_env=False,timeout=35) as browser:
                for step in case['steps']:
                    inputs=resolve(step['input'],observations)
                    index=len(backend.calls)
                    headers={'authorization':'synthetic-'+step['actor'],'origin':'http://evaluation.invalid'}
                    response=None
                    cancelled=False
                    if step['kind']=='control':
                        if inputs['operation']=='set_world':
                            from copy import deepcopy
                            backend.world=deepcopy(fixtures['worlds'][inputs['world']])
                        elif inputs['operation']=='advance_clock': virtual[0]+=inputs['seconds']
                        body={'control_applied':inputs['operation']}
                    else:
                        if step['kind']=='chat':
                            payload={k:v for k,v in inputs.items() if k not in {'new_session','cancel_when'}}
                            if inputs.get('cancel_when'):
                                task=asyncio.create_task(browser.post('/api/chat',json=payload,headers=headers))
                                await asyncio.wait_for(backend.started.wait(),timeout=10)
                                backend.cancelled_at=time.monotonic();task.cancel()
                                try: await task
                                except asyncio.CancelledError: cancelled=True
                            else: response=await browser.post('/api/chat',json=payload,headers=headers)
                        else:
                            op=inputs.pop('operation')
                            if op=='prepare_voucher': response=await browser.post('/api/actions/vouchers/prepare',json=inputs,headers=headers)
                            elif op=='confirm_action': response=await browser.post('/api/actions/'+inputs['action_id']+'/confirm',headers=headers)
                            elif op=='action_status': response=await browser.get('/api/actions/'+inputs['action_id'],headers=headers)
                            elif op=='session_messages': response=await browser.get('/api/sessions/'+inputs['session_id']+'/messages',headers=headers)
                            else: raise ValueError('Adapter operation not implemented')
                        if cancelled: body={'detail':'Request cancelled by driver'}
                        else:
                            try: body=response.json()
                            except ValueError: body={'detail':'Non-JSON ASGI response'}
                    calls=backend.calls[index:]
                    outcome=('cancelled' if cancelled else 'control' if response is None else
                             'upstream_error' if response.status_code>=500 else 'business_rejected' if response.status_code>=400 else
                             'success' if body.get('status')=='ok' else body.get('status','success'))
                    error_code=next((event.get('code') for event in body.get('events',[]) if event.get('event')=='agent_error'),None)
                    action_id=body.get('action_id') or inputs.get('action_id')
                    state={}
                    if action_id:
                        current=actions._load(action_id,fixtures['actors'][step['actor']]['user_id'])
                        if current: state['action']={'state':current['state'],'order_id':current['order_id']}
                    observation={'id':step['id'],'actor':step['actor'],'transport':'asgi' if response is not None else 'driver',
                                 'outcome':outcome,'error_code':error_code,
                                 'response':{'http_status':response.status_code if response is not None else None,'body':body},
                                 'calls':calls,'evidence':{'calls_complete':True},'state_after':state,
                                 'metrics':{'calls_started_after_cancel':sum(c['started_after_cancel'] for c in calls),
                                            'calls_started_after_deadline':sum(c['started_after_deadline'] for c in calls)}}
                    observations[step['id']]=observation;record['steps'].append(observation)
                    if on_step is not None:
                        await on_step(step, observation)
            record['mock_orders_created']=len(backend.world['orders'])
    except Exception as exc:
        record.update({'execution_status':'blocked','reason':'Adapter/step could not complete: '+type(exc).__name__})
    finally:
        action_module.time=original_time
        workflow_module.AgentHarness = original_harness
        for key,value in saved.items():
            if value is MISSING:
                delattr(api.app.state,key)
            else:
                setattr(api.app.state,key,value)
        await business_client.close();await provider.close()
    record['duration_ms']=round((time.monotonic()-start)*1000)
    if phase:
        record['phase_injections'] = injections
        if not injections:
            record.update({'execution_status':'blocked', 'reason':'Required verified-candidate phase was not reached'})
    return record


async def run(args):
    from .model_config import load_settings
    validate_catalog()
    suite,cases,fixtures=load_catalog()
    wanted=set(args.ids.split(','))
    selected=[case for case in cases if case['id'] in wanted]
    if len(selected)!=len(wanted) or len(selected)>args.max_cases: raise ValueError('Unknown case IDs or case-count limit exceeded')
    allowed_splits = {'development', 'reserved'} if args.allow_reserved else {'development'}
    if any(case['phase']!='core' or case['split'] not in allowed_splits for case in selected):
        raise ValueError('Reserved core cases require explicit --allow-reserved; future cases cannot execute')
    if args.mode=='mock_model' and not args.allow_model: raise ValueError('Explicit --allow-model is required')
    config=load_settings().provider_config() if args.mode=='mock_model' else None
    budget=RunBudget(args.max_model_calls,args.max_tokens,args.max_seconds)
    directory=Path(__file__).resolve().parents[1]/'reports/evaluation'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory.mkdir(parents=True,exist_ok=False)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip())
    records=[]
    for case in selected:
        print('Executing',case['id'],args.mode,flush=True)
        if time.monotonic()>=budget.deadline:
            record={'case_id':case['id'],'execution_status':'not_run','reason':'Run wall-clock limit reached'}
        else:
            record=await execute_case(case,suite,fixtures,args.mode,budget,config)
        record.update({'code_revision':revision,'dirty':dirty})
        records.append(record)
        (directory/'records.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    index={r['case_id']:r for r in records}
    results=[grade_case(case,index.get(case['id']),suite,fixtures) for case in cases]
    report={'kind':'diagnostic_execution','mode':args.mode,'selected_ids':sorted(wanted),'reserved_explicitly_enabled':args.allow_reserved,'summary':summarize(results),
            'results':results,'code_revision':revision,'dirty':dirty,'model':config.model if config else None,
            'model_budget':{'max_calls':budget.limit_calls,'max_tokens':budget.limit_tokens,'attempts':budget.calls,
                            'input_tokens':budget.input_tokens,'output_tokens':budget.output_tokens,
                            'unknown_usage_calls':budget.unknown_usage_calls,'calls':budget.attempts},
            'business_transport':'in_memory_mock_only','real_orders_submitted':False}
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(directory/'report.json'),'summary':report['summary'],
                      'model_attempts':budget.calls,'usage_known_tokens':budget.input_tokens+budget.output_tokens},ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description='Explicit evaluation only; business I/O is mocked')
    parser.add_argument('--mode',choices=['mock_rules','mock_model'],default='mock_rules')
    parser.add_argument('--ids',required=True)
    parser.add_argument('--max-cases',type=int,default=4)
    parser.add_argument('--allow-model',action='store_true')
    parser.add_argument('--allow-reserved',action='store_true',help='Explicit pre-merge evaluation of visible reserved core cases')
    parser.add_argument('--max-model-calls',type=int,default=20)
    parser.add_argument('--max-tokens',type=int,default=60000)
    parser.add_argument('--max-seconds',type=int,default=180)
    args=parser.parse_args()
    if min(args.max_cases,args.max_model_calls,args.max_tokens,args.max_seconds)<=0: raise ValueError('Budgets must be positive')
    asyncio.run(run(args))


if __name__=='__main__': main()
