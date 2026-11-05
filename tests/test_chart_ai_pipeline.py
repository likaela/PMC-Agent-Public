"""Real HTTP -> AI -> local validation -> Book Tool -> temp session regressions.

Only synthetic facts and injected models. No provider or enterprise writes.
"""
import copy
import json
from decimal import Decimal

import pytest
from test_production_quantity_safety import api as base_api, session, query, PRODUCTS
from services import chart_ai_pipeline as pipeline, chart_ai_service
import config


class DeepSeekStandIn:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.mutate = None
        self.chart_failure = None

    def with_structured_output(self, schema, method):
        owner = self
        class Request:
            def invoke(self, messages):
                payload = json.loads(messages[-1][1])
                owner.calls.append((schema.__name__, payload))
                if schema.__name__ == 'ChartReply':
                    if owner.chart_failure:
                        raise owner.chart_failure
                    return dict(verdict=payload['verdict'], finding_ids=payload['required_finding_ids'],
                        impact_id=payload['impact_id'], action_ids=[next(iter(payload['action_bank']))])
                if owner.failure:
                    raise owner.failure
                text = payload['message']
                intent = ('catalog' if '哪些产品' in text else 'maximum' if '最多' in text else
                    'bom' if 'BOM' in text else 'purchase' if '采购' in text else
                    'inventory' if '库存' in text and '生产' not in text else 'greeting' if text == '你好' else 'production')
                result = dict(intent=intent)
                if owner.mutate:
                    result = owner.mutate(result, payload)
                return result
        return Request()


@pytest.fixture
def ai_api(base_api, monkeypatch):
    monkeypatch.setenv('PMC_CHART_AI_MODE', '1')
    monkeypatch.setenv('PMC_LOCAL_REVIEW', '1')
    from langchain_core.tools import StructuredTool
    def bom_tool(facts):
        def bom(parent_material_code: str, revision: str):
            return facts.query_bom(parent_material_code, revision)
        return StructuredTool.from_function(bom, name='published_bom_query', description='Synthetic published BOM')
    for service in base_api.services.values():
        tool = bom_tool(service.book_service.facts)
        service.orchestrator.registry._resolver = lambda kind, tool=tool: tool if kind == 'bom' else pytest.fail('Unbound tool')
    model = DeepSeekStandIn()
    monkeypatch.setattr(config, 'get_llm', lambda: model)
    monkeypatch.setattr(config, 'get_llm_configuration_status', lambda: {'status':'ok', 'provider':'deepseek'})
    monkeypatch.setattr(chart_ai_service, 'get_llm', lambda: model)
    monkeypatch.setattr(chart_ai_service, 'get_llm_configuration_status', lambda: {'status':'ok'})
    base_api.model = model
    return base_api


@pytest.mark.parametrize('quantity', [1,10,50,100])
def test_ai_http_quantity_remains_exact_and_chart_and_history_match(ai_api, quantity):
    sid = session(ai_api)
    response = query(ai_api, sid, f'生产{quantity}片可以吗')
    data = response['structured_data']['data']
    assert data['requested_quantity'] == quantity
    assert Decimal(data['materials'][0]['required_qty']) == quantity * 2
    assert [schema for schema, _ in ai_api.model.calls] == ['TaskUnderstanding','ChartReply']
    assert response['structured_data']['chart_ai']['source'] == 'llm'
    trace = response['structured_data']['execution_trace']
    assert trace[0]['stage'] == 'understanding'
    assert any(e['stage']=='tool' and e['summary']=='enterprise_book_demand_analysis' for e in trace)
    assert trace[-1]['stage'] == 'chart_ai' and trace[-1]['status'] == 'ok'
    history = ai_api.client.get(f'/api/v1/agent/sessions/{sid}').json()
    stored = history['turns'][-1]['response']['structured_data']
    assert stored['chart_ai'] == response['structured_data']['chart_ai']
    assert stored['execution_trace'] == trace


@pytest.mark.parametrize('raw', ['0片','-1片','1/2片','1.5片','100公斤','1e3片'])
def test_invalid_raw_quantity_still_understands_first_but_never_calls_business(ai_api, raw):
    sid = session(ai_api)
    result = query(ai_api, sid, '生产'+raw+'并生成采购清单')
    assert result['status'] == 'invalid_query'
    assert not ai_api.calls
    assert [name for name,_ in ai_api.model.calls] == ['TaskUnderstanding']
    assert result['structured_data']['chart_ai']['status'] == 'not_applicable'


def test_ai_is_called_before_the_real_local_business_calculation(ai_api):
    sid = session(ai_api)
    book = ai_api.services[PRODUCTS[0]].book_service
    original = book.analyze
    def analyze(*args, **kwargs):
        assert ai_api.model.calls[0][0] == 'TaskUnderstanding'
        return original(*args, **kwargs)
    book.analyze = analyze
    assert query(ai_api, sid, '生产10片')['status'] == 'ok'


def test_latest_demand_drives_purchase_after_ai_and_invalid_does_not_resurrect(ai_api):
    sid = session(ai_api)
    query(ai_api, sid, '生产100片生成采购清单')
    query(ai_api, sid, '生产50片呢')
    latest = query(ai_api, sid, '帮我生成采购建议')
    assert latest['structured_data']['data']['requested_quantity'] == 50
    query(ai_api, sid, '生产0片')
    blocked = query(ai_api, sid, '生成采购建议')
    assert blocked['status'] == 'needs_input'
    assert query(ai_api, sid, '生产1片')['structured_data']['data']['requested_quantity'] == 1


@pytest.mark.parametrize('failure', [TimeoutError('secret should never be shown'), ValueError('bad JSON')])
def test_model_failure_never_silently_executes_tools_or_changes_demand(ai_api, failure):
    sid = session(ai_api)
    before = copy.deepcopy(ai_api.repository.get_session(sid))
    ai_api.model.failure = failure
    failed = query(ai_api, sid, '生产10片')
    assert failed['status'] == 'model_unavailable'
    assert not ai_api.calls
    assert ai_api.repository.get_session(sid) == before
    assert 'secret' not in json.dumps(failed)


def test_hallucinated_identifier_reference_fails_before_tool(ai_api):
    sid = session(ai_api)
    ai_api.model.mutate = lambda result, payload: {**result, 'product_code':'KQ-NOT-IN-CATALOG'}
    assert query(ai_api,sid,'生产10片')['status'] == 'model_unavailable'
    assert not ai_api.calls


def test_chart_timeout_keeps_verified_numbers_and_records_actual_failure(ai_api):
    sid = session(ai_api)
    ai_api.model.chart_failure = TimeoutError('provider timeout')
    result = query(ai_api,sid,'生产20片')
    assert result['status'] == 'ok'
    assert result['structured_data']['data']['requested_quantity'] == 20
    assert result['structured_data']['chart_ai']['status'] == 'unavailable'
    assert result['structured_data']['execution_trace'][-1]['status'] == 'unavailable'


def test_resolution_ticket_reuses_one_model_understanding_then_real_query(ai_api):
    sid = session(ai_api)
    message = '生产10片'
    resolved = ai_api.client.post('/api/v1/agent/resolve',json={'message':message,'session_id':sid}).json()
    assert resolved['understanding_id']
    result = ai_api.client.post('/api/v1/agent/query',json={'message':message,'session_id':sid,
        'understanding_id':resolved['understanding_id']}).json()
    assert result['status'] == 'ok'
    assert [name for name,_ in ai_api.model.calls] == ['TaskUnderstanding','ChartReply']
    replay = ai_api.client.post('/api/v1/agent/query',json={'message':message,'session_id':sid,
        'understanding_id':resolved['understanding_id']}).json()
    assert replay['status'] == 'model_unavailable'


def test_ticket_is_bound_to_original_question(ai_api):
    sid = session(ai_api)
    response = ai_api.client.post('/api/v1/agent/resolve',json={'message':'生产10片','session_id':sid}).json()
    result = ai_api.client.post('/api/v1/agent/query',json={'message':'生产100片','session_id':sid,
        'understanding_id':response['understanding_id']}).json()
    assert result['status'] == 'model_unavailable'
    assert not ai_api.calls


def test_catalog_is_backend_generated_and_persisted_with_actual_ai_event(ai_api):
    sid = session(ai_api)
    result = query(ai_api,sid,'有哪些产品')
    assert result['structured_data']['data']['products']
    assert result['structured_data']['execution_trace'][0]['stage'] == 'understanding'
    assert len(ai_api.model.calls) == 1
    history = ai_api.client.get(f'/api/v1/agent/sessions/{sid}').json()
    assert history['turns'][-1]['response']['answer'] == result['answer']


def test_only_minimal_context_is_exported_not_history_or_provenance():
    payload, refs = pipeline.anonymous_task('TEST-PRODUCT-01生产10片',
        [{'product_code':'TEST-PRODUCT-01','product_name':'Synthetic','aliases':[]}],
        {'product_context':{'product_code':'TEST-PRODUCT-01'},'current_demand':{'status':'valid','quantity':100},
            'history':['DO-NOT-EXPORT'],'provenance':{'sha256':'DO-NOT-EXPORT'}})
    serialized=json.dumps(payload)
    assert 'DO-NOT-EXPORT' not in serialized
    assert 'TEST-PRODUCT-01' in refs and 'TEST-PRODUCT-01' in payload['message']


def test_second_product_session_never_inherits_first_products_quantity(ai_api):
    first=session(ai_api,PRODUCTS[0]); second=session(ai_api,PRODUCTS[1])
    query(ai_api,first,'生产100片')
    result=query(ai_api,second,'帮我生成采购建议')
    assert result['status']=='needs_input'


def test_normal_bom_does_not_call_chart_model(ai_api):
    sid=session(ai_api)
    result=query(ai_api,sid,'查询BOM')
    assert result['status']=='ok'
    assert [name for name,_ in ai_api.model.calls]==['TaskUnderstanding']
    assert result['structured_data']['chart_ai']['status']=='not_applicable'


@pytest.mark.parametrize("wrong_intent", ["maximum","bom","inventory"])
def test_model_cannot_replace_explicit_production_quantity_with_another_operation(ai_api, wrong_intent):
    sid=session(ai_api)
    ai_api.model.mutate=lambda result,payload: {"intent":wrong_intent}
    response=query(ai_api,sid,"生产10片")
    assert response["structured_data"]["data"]["requested_quantity"]==10
    assert Decimal(response["structured_data"]["data"]["materials"][0]["required_qty"])==20


def test_model_cannot_rewrite_a_fraction_as_a_valid_quantity(ai_api):
    sid=session(ai_api)
    ai_api.model.mutate=lambda result,payload: {"intent":"maximum", "needs_clarification":True}
    response=query(ai_api,sid,"生产1/2片")
    assert response["status"]=="invalid_query"
    assert not ai_api.calls


@pytest.mark.parametrize("private_text", [r"E:\Example Project\企业\bom.xlsx", "inventory-private.xlsx", "f"*64])
def test_task_projection_removes_private_paths_filenames_and_hashes(private_text):
    payload, refs = pipeline.anonymous_task("查看 "+private_text+"，查询库存", [], {})
    exported=json.dumps(payload,ensure_ascii=False)
    assert "Example Project" not in exported and "企业" not in exported
    assert "inventory-private" not in exported and "bom.xlsx" not in exported
    assert "f"*64 not in exported


def test_missing_new_production_quantity_is_not_replaced_by_model_maximum(ai_api):
    sid=session(ai_api)
    query(ai_api,sid,"生产100片")
    before=len(ai_api.calls)
    ai_api.model.mutate=lambda result,payload: {"intent":"maximum"}
    result=query(ai_api,sid,"我要生产"+PRODUCTS[0])
    assert result["status"]=="needs_input"
    assert len(ai_api.calls)==before


def test_catalog_trace_does_not_claim_a_registered_tool_invocation(ai_api):
    sid=session(ai_api)
    result=query(ai_api,sid,"有哪些产品")
    trace=result["structured_data"]["execution_trace"]
    assert any(event["stage"]=="catalog" for event in trace)
    assert not any(event["stage"]=="tool" for event in trace)
    assert next(event for event in trace if event["stage"]=="calculation")["status"]=="not_applicable"


@pytest.mark.parametrize("private_path", ["/home/user/My Docs/bom.xlsx","/data/private/stock.json"])
def test_task_projection_hides_posix_source_paths(private_path):
    payload,_=pipeline.anonymous_task("查看 "+private_path+"，查询库存",[],{})
    assert "home/user" not in json.dumps(payload) and "data/private" not in json.dumps(payload)


def test_ticket_expires_when_session_has_a_newer_turn(ai_api):
    sid=session(ai_api)
    result=ai_api.client.post("/api/v1/agent/resolve",json={"message":"生产10片","session_id":sid}).json()
    query(ai_api,sid,"生产1片")
    before=len(ai_api.calls)
    stale=ai_api.client.post("/api/v1/agent/query",json={"message":"生产10片","session_id":sid,"understanding_id":result["understanding_id"]}).json()
    assert stale["status"]=="model_unavailable"
    assert len(ai_api.calls)==before


@pytest.mark.parametrize("url",["http://api.deepseek.com","https://unapproved.example","https://api.deepseek.com@unapproved.example","https://api.deepseek.com?token=synthetic"])
def test_chart_ai_cannot_send_to_an_unapproved_model_endpoint(monkeypatch,url):
    monkeypatch.setenv("PMC_CHART_AI_MODE","1")
    monkeypatch.setenv("PMC_REVIEW_MODE","1")
    monkeypatch.setenv("PMC_TRIAL_MODE","0")
    monkeypatch.setenv("LLM_PROVIDER","deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY","synthetic-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL",url)
    monkeypatch.setattr(config,"ChatOpenAI",lambda **k:pytest.fail("Unapproved endpoint reached SDK"))
    assert config.get_llm_configuration_status()["status"]=="warning"
    with pytest.raises(RuntimeError):config.get_llm()


def test_trial_stays_no_egress_even_when_chart_flag_is_set(monkeypatch):
    monkeypatch.setenv("PMC_TRIAL_MODE","1");monkeypatch.setenv("PMC_CHART_AI_MODE","1")
    monkeypatch.setattr(config,"ChatOpenAI",lambda **k:pytest.fail("Trial reached SDK"))
    with pytest.raises(RuntimeError):config.get_llm()


def test_model_receives_original_business_text_before_any_entity_or_quantity_rules(ai_api):
    sid=session(ai_api)
    message="生产10片，核对物料需求"
    query(ai_api,sid,message)
    payload=ai_api.model.calls[0][1]
    assert payload["message"]==message
    assert "references" not in payload
    assert payload["catalog"][0]["product_code"]==PRODUCTS[0]


def test_explicit_model_parameters_are_recorded_but_never_replace_local_quantity(ai_api):
    sid=session(ai_api)
    ai_api.model.mutate=lambda result,payload: {"intent":"production", "product_code":PRODUCTS[0],"quantity":999,"unit":"片"}
    result=query(ai_api,sid,"生产10片")
    assert result["structured_data"]["data"]["requested_quantity"]==10
    assert result["structured_data"]["execution_trace"][0]["details"]["quantity"]==999


def test_user_entered_unknown_product_is_a_local_not_found_not_an_ai_fact(ai_api):
    sid=session(ai_api)
    ai_api.model.mutate=lambda result,payload:{"intent":"bom","product_code":"KQ-UNKNOWN"}
    result=query(ai_api,sid,"查询KQ-UNKNOWN的BOM")
    assert result["status"]=="item_not_found"
    assert not ai_api.calls


def test_clarification_is_saved_with_its_real_understanding_trace(ai_api):
    sid=session(ai_api)
    ai_api.model.mutate=lambda result,payload:{"intent":"other","needs_clarification":True}
    result=query(ai_api,sid,"那个怎么样")
    assert result["status"]=="needs_input"
    history=ai_api.client.get("/api/v1/agent/sessions/"+sid).json()
    assert history["turns"][-1]["response"]["structured_data"]["execution_trace"]==result["structured_data"]["execution_trace"]
    assert not ai_api.calls


def test_rule_stage_shows_verified_quantity_after_rejecting_model_candidate_override(ai_api):
    sid=session(ai_api)
    ai_api.model.mutate=lambda result,payload:{"intent":"production","quantity":999}
    result=query(ai_api,sid,"生产10片")
    rule=next(event for event in result["structured_data"]["execution_trace"] if event["stage"]=="rules")
    assert rule["details"]["quantity"]==10



def test_greeting_has_real_model_understanding_without_business_tools_or_defensive_copy(ai_api):
    sid=session(ai_api)
    response=query(ai_api,sid,"你好")
    assert response["status"]=="ok"
    assert response["structured_data"]["understanding_source"]=="llm"
    assert not ai_api.calls
    assert all(text not in response["answer"] for text in ("不会","不猜","不保证","权威数据","不承担"))


def test_quantity_rules_only_run_after_model_understanding(ai_api,monkeypatch):
    from services import production_request_context as rules
    original=rules.parse_quantity
    checked=[]
    def parse_after_model(message):
        assert ai_api.model.calls, "Local quantity parsing ran before DeepSeek understanding"
        checked.append(message)
        return original(message)
    monkeypatch.setattr(rules,"parse_quantity",parse_after_model)
    sid=session(ai_api)
    assert query(ai_api,sid,"生产10片")["status"]=="ok"
    assert checked


def test_purchase_trace_records_the_actual_compatibility_service_not_a_fictional_tool(ai_api):
    sid=session(ai_api)
    result=query(ai_api,sid,"生产100片并生成采购清单")
    events=result["structured_data"]["execution_trace"]
    assert any(event["stage"]=="service" and event["summary"]=="PmcCapabilityQueries.run" for event in events)
    assert result["structured_data"]["data"]["requested_quantity"]==100


def test_ai_production_intent_routes_natural_wording_without_a_literal_production_keyword(ai_api):
    sid=session(ai_api)
    message="这单10片的料够不够"
    assert "生产" not in message and "库存" not in message
    result=query(ai_api,sid,message)
    assert result["status"]=="ok"
    assert result["structured_data"]["data"]["requested_quantity"]==10
    assert Decimal(result["structured_data"]["data"]["materials"][0]["required_qty"])==20
    assert ai_api.calls
    assert ai_api.model.calls[0][1]["message"]==message
