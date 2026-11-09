"""Retained public Run11 inputs, hand-authored outputs; never a model replay."""
import copy
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from src.core.fund_analysis import (
    FundAnalysis, FundData, FundProfile, FundRelatedAsset, Holding, HoldingsSnapshot, Metric, NavPoint,
)
from src.services import fund_interpretation as module
from src.services.fund_analysis_service import render_fund_report
from tests.test_fund_decision_contract import impact_output, news_bundle, statement
from tests.test_fund_interpretation import analysis, configuration, fake_factory, valid_output


def run11_inputs():
    saved = json.loads((Path(__file__).parent / 'fixtures/fund_sources_run11.json').read_text())
    analyses = []
    for original in saved['analyses']:
        row = copy.deepcopy(original)
        data = row['data']
        profile = data['profile']
        profile['inception_date'] = date.fromisoformat(profile['inception_date']) if profile['inception_date'] else None
        data['profile'] = FundProfile(**profile)
        data['fetched_at'] = datetime.fromisoformat(data['fetched_at'])
        data['navs'] = [NavPoint(**{**p, 'nav_date': date.fromisoformat(p['nav_date'])}) for p in data['navs']]
        if data['holdings']:
            h = data['holdings']
            h['holdings'] = [Holding(**item) for item in h['holdings']]
            for key in ('report_period_end', 'published_at'):
                h[key] = date.fromisoformat(h[key]) if h[key] else None
            data['holdings'] = HoldingsSnapshot(**h)
        for asset in data['related_assets']:
            for key in ('source_date', 'published_at'):
                asset[key] = date.fromisoformat(asset[key]) if asset[key] else None
        data['related_assets'] = [FundRelatedAsset(**a) for a in data['related_assets']]
        for metric in row['metrics'].values():
            for key in ('start_date', 'end_date'):
                metric[key] = date.fromisoformat(metric[key]) if metric[key] else None
        analyses.append(FundAnalysis(FundData(**data), {k: Metric(**v) for k, v in row['metrics'].items()}, row['warnings']))
    return saved, analyses


def complete_impact():
    """Test news independently of an unsupported investment direction."""
    out = impact_output()
    decision = out['funds'][0]['decision']
    decision.update(direction='undetermined', basis='information_gap')
    decision['rationale']['text'] = (
        '{{F000001:return_1m}}历史股票持仓只提供研究对象，本次输入不足以判断企业经营变化。')
    return out


def strict_parse(out, news=None, analyses=None):
    analyses = analyses or [analysis(holdings=True)]
    facts, comparison = module._build_context(analyses, news)
    return module._parse_response(json.dumps(out, ensure_ascii=False), analyses, facts, comparison, require_analysis=True)


def test_live_path_does_not_silently_accept_legacy_empty_analysis():
    output = valid_output()
    for field in ('decision', 'news_impacts', 'news_assessment'):
        changed = copy.deepcopy(output)
        changed['funds'][0].pop(field)
        result = module.interpret_funds([analysis()], config=configuration(), analyzer_factory=fake_factory(changed))
        assert result.status == 'model_failed'
        assert result.diagnostics['code'] == 'analysis_incomplete'
        # Offline legacy parser remains deliberately compatible, not the runtime.
        assert module._parse_response(json.dumps(changed), [analysis()], *module._build_context([analysis()], None))[0]


@pytest.mark.parametrize('direction', ['watch', 'consider_buy', 'consider_reduce'])
def test_information_gap_cannot_be_presented_as_an_investment_case(direction):
    out = valid_output()
    out['funds'][0]['decision']['direction'] = direction
    result = strict_parse(out)
    assert not result[0] and result[2]['000001'].diagnostics['code'] == 'decision_basis'


def test_watch_needs_substantive_fund_evidence_and_distinct_conditions():
    out = valid_output()
    d = out['funds'][0]['decision']
    d.update(direction='watch', basis='investment_case')
    assert strict_parse(out)[2]['000001'].diagnostics['code'] == 'decision_evidence'
    out = complete_impact()
    d = out['funds'][0]['decision']
    d.update(direction='watch', basis='investment_case')
    d['conditions'] = copy.deepcopy(d['rationale'])
    assert strict_parse(out, news_bundle())[2]['000001'].diagnostics['code'] == 'decision_basis'


def test_news_empty_requires_evidence_bound_explanation():
    out = valid_output()
    assert strict_parse(out, news_bundle())[2]['000001'].diagnostics['code'] == 'news_assessment_evidence'
    out['funds'][0]['news_assessment'] = {
        'status': 'insufficient_evidence',
        'explanation': statement('经营计划线索缺少公告正文，无法判断实际变化及对基金的重要影响。', 'F000001:news:0'),
    }
    result = strict_parse(out, news_bundle())
    assert result[0]['000001'].news_assessment.status == 'insufficient_evidence'
    out['funds'][0]['news_assessment']['explanation']['evidence_ids'] = ['F000001:quality']
    assert not strict_parse(out, news_bundle())[0]


@pytest.mark.parametrize('text', [
    '待核实线索已经证实为重大利好，应当继续关注。',
    '待核验线索表明没有重大新闻，可以继续关注。',
    '新闻线索已经确认回购完成，后续仍需观察。',
    '若待核实线索落实，本基金当前持有相关公司可能获利。',
    '若待核验线索落实，可能推动本基金净值上涨。',
])
def test_news_cannot_use_qualification_as_cover_for_unsafe_claims(text):
    out = complete_impact()
    out['funds'][0]['news_impacts'][0]['transmission']['text'] = text
    assert not strict_parse(out, news_bundle())[0]


def test_every_bound_headline_is_either_analyzed_or_explicitly_unresolved():
    news = news_bundle()
    news['000001']['items'][0]['title'] = '历史公司首次回购公告'
    second = copy.deepcopy(news['000001']['items'][0])
    second.update(title='历史公司产品订单公告', url='https://example.test/order', published_date='2026-06-29')
    news['000001']['items'].append(second)
    out = complete_impact()
    assert strict_parse(out, news)[2]['000001'].diagnostics['code'] == 'statement_schema'
    out['funds'][0]['news_assessment']['explanation'] = statement(
        '订单线索缺少原始合同与履行条款，无法判断其经营意义及基金关联影响。', 'F000001:news:1')
    result = strict_parse(out, news)
    assert result[0]['000001'].news_assessment.explanation


def test_empty_news_never_gets_invented_impact_or_no_news_claim():
    out = valid_output()
    assert strict_parse(out)[0]['000001'].news_assessment.status == 'no_eligible_news'
    out['funds'][0]['news_assessment'] = {'status': 'conditional_impacts', 'explanation': None}
    assert not strict_parse(out)[0]


def test_run11_navigation_never_enters_prompt_or_report_after_recheck():
    saved, analyses = run11_inputs()
    facts, comparison = module._build_context(analyses, saved['news_evidence'])
    prompt = module._make_prompt(analyses, facts, comparison)
    assert '股吧在哪' not in prompt and '投资者讨论入口' not in prompt
    assert not module._eligible_news(facts, '018123')
    assert len(module._eligible_news(facts, '020356')) == 2
    assert len(module._eligible_news(facts, '025500')) == 2
    assert len(module._eligible_news(facts, '024480')) == 1
    assert '贷款承诺' in prompt and '回购' in prompt


def mock_run11_output(analyses, facts):
    """Illustrate contract alternatives, not a suggested trade or actual model output."""
    output = valid_output(tuple(a.data.profile.code for a in analyses))
    for item in output['funds']:
        code = item['fund_code']; prefix = f'F{code}'
        eligible = module._eligible_news(facts, code)
        if code == '021190':
            item['decision']['rationale'] = statement('{{' + prefix + ':return_1m}}本次输入未提供汇率变化证据，无法把区间回落转换为操作判断。', prefix+':return_1m', prefix+':qdii')
            item['decision']['conditions'] = statement('需核验汇率与估值时点，明确其是否改变区间回落的研究解释。', prefix+':qdii')
        elif code == '018123':
            item['decision']['rationale'] = statement('{{' + prefix + ':max_drawdown_12m}}本次输入缺少经营变化核实证据，无法判断回撤后的投资价值。', prefix+':return_1m', prefix+':max_drawdown_12m')
        if code == '021190':
            item['risks'] = [statement('海外假期和汇率变化可能改变估值节奏，净值所属时点需单独核对。', prefix+':qdii')]
        if code == '018123':
            item['decision']['conditions'] = statement('需取得历史股票名单中企业的订单及盈利资料，检验区间回落是否伴随经营变化。', prefix+':holdings', prefix+':return_1m')
            item['decision']['invalidation'] = statement('若后续公告足以检验研究假设，且历史股票名单的适用时点得到核验，应重做判断。', prefix+':holdings')
            item['risks'] = [statement('历史股票名单可能已变化，旧报告不能直接说明后续经营风险。', prefix+':holdings')]
        if not eligible:
            continue
        ids = list(eligible)
        item['news_assessment'] = {'status': 'insufficient_evidence', 'explanation': statement(
            '关联线索缺少公告正文与实施细节，无法判断其是否构成对基金的重要变化。', *ids)}
        if code in {'020356', '024480'}:
            news_id = ids[0]; relation_id = facts[news_id].relationship_evidence_ids[0]
            transmission = ('若待核验的回购线索得到公告支持，可能改变企业现金安排与股本结构，尚需核对实施细节。'
                            if code == '020356' else
                            '若待核验的贷款承诺落实，可能扩充企业回购资金来源，仍需核验提款及实际使用条件。')
            item['news_impacts'] = [{'news_evidence_id': news_id, 'relationship_evidence_id': relation_id,
                                    'transmission': statement(transmission, news_id, relation_id)}]
            remaining = ids[1:]
            item['news_assessment'] = {'status': 'conditional_impacts', 'explanation': statement(
                '盈利观点线索缺少原始财报及核算依据，无法确认其经营意义或对基金的重要影响。', *remaining) if remaining else None}
        if code == '025500':
            item['decision']['rationale'] = statement('{{' + prefix + ':research_coverage}}历史股票名单与回购线索只提供研究对象，尚无法判断投资价值。', prefix+':research_coverage', prefix+':holdings', ids[0])
            item['decision']['conditions'] = statement('需核验回购注销线索的实施公告，并核对相关企业盈利与估值证据。', prefix+':holdings', ids[0])
            item['decision']['invalidation'] = statement('若注销线索与正式公告不符，或后续报告改变历史股票名单，需要重建研究判断。', prefix+':holdings', ids[0])
            item['risks'] = [statement('回购注销线索并非经营改善的证明，实际执行条件仍需核验。', ids[0])]
        if code == '024480':
            item['decision']['rationale'] = statement('{{' + prefix + ':return_3m}}贷款承诺线索尚不能说明实际回购规模，历史净值路径不能替代估值判断。', prefix+':return_3m', ids[0])
            item['decision']['conditions'] = statement('需核验贷款承诺线索的提款条件及实际回购公告，再核对相关企业经营与估值证据。', prefix+':holdings', ids[0])
            item['decision']['invalidation'] = statement('若融资线索未获正式公告支持，或回购安排发生变化，原研究假设需要重新检验。', ids[0])
            item['risks'] = [statement('贷款承诺线索尚未证明资金实际使用，事件时点不能替代净值日期。', ids[0])]
        if code == '020356':
            item['risks'] = [statement('回购线索不能替代企业经营证据，仍需检验现金安排的实际约束。', ids[0])]
            item['decision'] = {
                'direction': 'watch', 'basis': 'investment_case',
                'rationale': statement('{{' + prefix + ':return_1m}}历史股票名单与回购线索提供观察对象，但标题尚不足以确认实施。',
                                       prefix+':return_1m', prefix+':holdings', ids[0]),
                'conditions': statement('需核验回购线索对应的实施进度与资金用途，并核对历史股票名单的适用时点。', ids[0], prefix+':holdings'),
                'invalidation': statement('若回购线索未获公告支持，或后续报告改变历史股票名单，等待条件需重新评估。', ids[0], prefix+':holdings'),
            }
    return output


def test_real_inputs_mock_output_cover_nonempty_unknown_and_no_eligible_states():
    saved, analyses = run11_inputs()
    facts, _ = module._build_context(analyses, saved['news_evidence'])
    output = mock_run11_output(analyses, facts)
    calls = []
    result = module.interpret_funds(analyses, saved['news_evidence'], config=configuration(),
                                    analyzer_factory=fake_factory(output, calls=calls))
    assert result.status == 'ok', {k: v.diagnostics for k,v in result.fund_statuses.items()}
    assert len(calls) == 1 and result.funds['020356'].decision.direction == 'watch'
    assert result.funds['021190'].decision.direction == 'undetermined'
    assert result.funds['025500'].news_assessment.status == 'insufficient_evidence'
    text = render_fund_report(analyses, saved['failed_codes'], {}, datetime.fromisoformat(saved['generated_at']),
                              result, as_of=date.fromisoformat(saved['as_of']), news_evidence=saved['news_evidence'])
    assert '贷款承诺' in text and '提款及实际使用条件' in text
    assert '影响待核实' in text and '盈利观点线索缺少原始财报及核算依据' in text
    assert '关联线索缺少公告正文与实施细节' in text
    assert '未取得可分析关联新闻' in text and '不代表没有相关事件' in text
    assert '股吧在哪' not in text
    assert '无法判断' in text and '观望' in text
    assert '基金将上涨' not in text


@pytest.mark.parametrize('basis', [[], {}, None, True, 1])
def test_invalid_basis_isolated_to_its_fund(basis):
    output = valid_output(('000001', '000002'))
    output['funds'][0]['decision']['basis'] = basis
    result = strict_parse(output, analyses=[analysis(), analysis('000002')])
    assert set(result[0]) == {'000002'}
    assert result[2]['000001'].diagnostics['code'] == 'decision_basis'


def test_pure_page_verification_is_not_a_concrete_watch_condition():
    out = complete_impact()
    decision = out['funds'][0]['decision']
    decision.update(direction='watch', basis='investment_case')
    decision['conditions'] = statement('需核验原始页面与资料口径，确认后再继续观察。', 'F000001:holdings')
    decision['invalidation'] = statement('若原始页面信息发生变化，则需重新核验资料口径。', 'F000001:holdings')
    result = strict_parse(out, news_bundle())
    assert result[2]['000001'].diagnostics['code'] == 'decision_observation'


def test_watch_variable_cannot_borrow_an_unrelated_news_fact():
    out = complete_impact()
    decision = out['funds'][0]['decision']
    decision.update(direction='watch', basis='investment_case')
    decision['rationale']['text'] += '经营计划线索仍待核验。'
    decision['rationale']['evidence_ids'].append('F000001:news:0')
    decision['conditions'] = statement('需核验回购线索的实际实施进度，明确资金使用条件。',
                                        'F000001:news:0', 'F000001:holdings')
    result = strict_parse(out, news_bundle())  # Bound operating-plan headline lacks the repurchase variable.
    assert result[2]['000001'].diagnostics['code'] == 'decision_observation'
