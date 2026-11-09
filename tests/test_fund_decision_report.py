"""Report integration for conditional opinions; all model output is simulated."""
from datetime import date, datetime

import pytest

from tests.test_fund_report_brief import load_snapshot
from src.services.fund_analysis_service import render_fund_report
from src.services.fund_interpretation import (
    FundInsight, FundInterpretation, InterpretationScopeStatus, NewsImpact,
    ResearchDecision, Statement, _build_context,
)


def accepted_report(direction='watch', with_decision=True):
    saved, analyses, _ = load_snapshot()
    a = analyses[4]
    code = a.data.profile.code
    facts, comparison = _build_context([a], saved['news_evidence'])
    metrics = [f'F{code}:return_1m', f'F{code}:holdings']
    rationale = Statement('历史股票名单集中于科技公司，历史净值区间持续承压，应先核验原有研究判断。', metrics)
    decision = ResearchDecision(direction, rationale,
                                Statement('核对资金期限与可承受波动后，再确认相关研究依据是否仍然成立。', metrics),
                                Statement('若企业经营证据和后续净值表现改变，应重新评估原有研究判断。', metrics))
    insight = FundInsight(
        conclusion=[Statement('旧核心结论应保留在结构化结果而不重复展开。', metrics)],
        risks=[Statement('历史行业资料未证明风险已经消失，后续结果仍有不确定性。', metrics)],
        evidence=[Statement('旧完整占位符证据不应在简报重复展开。', metrics)],
        observations=[Statement('旧后续观察应保留在结构化结果。', metrics)],
        decision=decision if with_decision else None,
    )
    interpretation = FundInterpretation('ok', 'mock', funds={code: insight}, facts=facts,
                                        comparison_context=comparison,
                                        fund_statuses={code: InterpretationScopeStatus('ok', 'mock')})
    return saved, [a], interpretation


def render(saved, analyses, interpretation):
    return render_fund_report(analyses, {}, {}, datetime.fromisoformat(saved['generated_at']), interpretation,
                              as_of=date.fromisoformat(saved['as_of']), news_evidence=saved['news_evidence'])


@pytest.mark.parametrize('direction,label', [('consider_buy','买入候选'), ('consider_reduce','减仓候选'),
                                           ('watch','观望'), ('undetermined','无法判断')])
def test_conditional_decision_precedes_facts_and_does_not_echo_template(direction, label):
    saved, analyses, interpretation = accepted_report(direction)
    text = render(saved, analyses, interpretation)
    assert label in text
    assert text.index('操作倾向') < text.index('最新已披露单位净值')
    assert all(label in text for label in ['**为什么**', '**成立条件**', '**何时改变判断**'])
    assert '旧核心结论' not in text and '旧完整占位符' not in text
    assert 'F025500:holdings' not in text
    assert '历史持仓 2026-09-30' in text
    assert '你的持仓' not in text and '你的成本' not in text
    if direction == 'consider_reduce':
        assert '若已持有' in text


def test_legacy_narrative_does_not_get_fabricated_watch_decision():
    saved, analyses, interpretation = accepted_report(with_decision=False)
    text = render(saved, analyses, interpretation)
    assert '模型未提供' in text and '操作倾向' not in text
    assert '观望' not in text
    assert '旧核心结论' in text


def test_rejected_scope_never_displays_stale_decision_or_impact():
    saved, analyses, interpretation = accepted_report('consider_buy')
    code = analyses[0].data.profile.code
    interpretation.status = 'partial'
    interpretation.fund_statuses[code] = InterpretationScopeStatus('model_failed', '未通过校验')
    text = render(saved, analyses, interpretation)
    assert '买入候选' not in text and '为什么' not in text and '模型未提供' not in text
    assert '未通过校验' in text


def test_news_impact_has_historical_relation_date_source_and_uncertainty():
    saved, analyses, interpretation = accepted_report()
    code = analyses[0].data.profile.code
    news_id = next(key for key, fact in interpretation.facts.items()
                   if fact.kind == 'news_lead' and fact.relationship_evidence_ids)
    relation_id = interpretation.facts[news_id].relationship_evidence_ids[0]
    interpretation.funds[code].news_impacts = [NewsImpact(news_id, relation_id,
        Statement('若待核实的回购线索落实，可能影响相关企业资本安排，仍须核验公告内容。', [news_id, relation_id]))]
    text = render(saved, analyses, interpretation)
    assert '可能影响（标题线索未核实）' in text
    assert '2026-09-30 历史股票名单' in text
    assert '是否仍持有未核验' in text
    assert '影响幅度未知' in text
    assert '晚于所列净值日' in text
    assert '新闻线索 2026-11-07' in text
    assert '重大利好' not in text and '基金将上涨' not in text
