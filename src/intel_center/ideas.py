from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import IdeaCandidate, IntelItem


def generate_ideas(items: list[IntelItem], *, max_ideas: int, min_ideas: int) -> list[IdeaCandidate]:
    ranked = sorted(
        [item for item in items if item.track != "macro_brief"],
        key=lambda entry: (entry.idea_potential_score, entry.importance_score),
        reverse=True,
    )
    ideas: list[IdeaCandidate] = []
    used_titles: list[str] = []
    for item in ranked:
        idea = _idea_from_item(item)
        if any(SequenceMatcher(None, idea.idea_title, title).ratio() >= 0.72 for title in used_titles):
            continue
        used_titles.append(idea.idea_title)
        ideas.append(idea)
        if len(ideas) >= max_ideas:
            break

    if len(ideas) >= min_ideas:
        return ideas

    for fallback in _fallback_ideas(items):
        if any(SequenceMatcher(None, fallback.idea_title, idea.idea_title).ratio() >= 0.72 for idea in ideas):
            continue
        ideas.append(fallback)
        if len(ideas) >= min_ideas:
            break
    return ideas


def _idea_from_item(item: IntelItem) -> IdeaCandidate:
    text = f"{item.title_en} {item.summary_en}".lower()
    method = _candidate_method(text, item.modality)
    dataset = _possible_dataset(text, item.modality)
    title = _idea_title(text, item)
    project_shape = _project_shape(text, item)
    why_now = (
        f"近期出现了与 {', '.join(item.matched_topics_zh[:2]) or item.track} 相关的新信号，"
        f"而且该条目同时具备 {item.study_type} 与 {item.modality} 线索，适合转成一个可快速验证的研究题目。"
    )
    evidence = [f"{item.title_en} ({item.source}) - {item.url}"]
    return IdeaCandidate(
        idea_title=title,
        why_now=why_now,
        seed_evidence=evidence,
        possible_dataset_or_signal=dataset,
        candidate_method=method,
        paper_or_project_shape=project_shape,
        score=item.idea_potential_score,
        source_item_ids=[item.id],
    )


def _idea_title(text: str, item: IntelItem) -> str:
    if "depression" in text and any(term in text for term in ("cardiovascular", "ecg", "heart")):
        return "用多模态纵向模型刻画抑郁与心血管共病风险轨迹"
    if any(term in text for term in ("llm", "large language model", "agent")):
        return "用 LLM / agent 做精神科与躯体共病的临床表型构建"
    if any(term in text for term in ("wearable", "smartphone", "digital phenotyping")):
        return "结合被动感测信号预测精神-身体共病恶化窗口"
    if "multimodal" in text or item.modality == "multimodal":
        return "构建 psychiatry + AI 的多模态预测基线与可解释性框架"
    return f"围绕 {', '.join(item.matched_topics_zh[:2]) or 'psychiatry + AI'} 设计一个可快速验证的研究题目"


def _candidate_method(text: str, modality: str) -> str:
    if any(term in text for term in ("survival", "trajectory", "longitudinal")):
        return "从时间到事件模型或轨迹聚类入手，再对照更强的时序深度学习基线。"
    if any(term in text for term in ("llm", "large language model", "agent")):
        return "先做 rule-based + LLM phenotyping baseline，再比较 structured + note 混合模型。"
    if modality == "wearable":
        return "使用多视图时间序列模型，并与简化的 mixed-effects / gradient boosting baseline 对照。"
    if modality == "multimodal":
        return "采用早期融合与晚期融合双基线，外加可解释性分析来判断不同模态贡献。"
    return "从可解释的强基线模型做起，再逐步加上表示学习或更复杂的融合策略。"


def _possible_dataset(text: str, modality: str) -> str:
    if any(term in text for term in ("ehr", "electronic health record", "claims", "hospital")):
        return "优先考虑 EHR / claims / registry 数据，再补充结构化诊断、药物与随访结果。"
    if modality == "wearable":
        return "可穿戴、手机被动感测、EMA 与症状量表的联合数据最有价值。"
    if any(term in text for term in ("llm", "note", "documentation")):
        return "精神科临床笔记、转诊摘要、出院小结与结构化诊断标签。"
    return "从公开摘要启发的数据模态入手，优先找纵向队列、EHR 或真实世界随访数据。"


def _project_shape(text: str, item: IntelItem) -> str:
    if any(term in text for term in ("trial", "intervention")):
        return "先做方法学或队列分析短文，再扩展成前瞻性干预研究。"
    if item.track == "current_program":
        return "比较适合做一个与你当前课题相连的 observational paper 或短期 pilot。"
    if item.track == "frontier_core":
        return "适合先做 scoping / benchmark 风格项目，再决定是否沉淀成长线课题。"
    return "先做方向扫描与 feasibility 分析，再判断是否值得变成正式项目。"


def _fallback_ideas(items: list[IntelItem]) -> list[IdeaCandidate]:
    titles = " ".join(item.title_en for item in items).lower()
    fallback: list[IdeaCandidate] = []
    if "cardiovascular" in titles or "ecg" in titles:
        fallback.append(
            IdeaCandidate(
                idea_title="比较精神症状表型与心血管纵向指标的联合预警价值",
                why_now="当前数据流和方法学都在向多模态风险预测集中，适合把共病问题做成更清晰的时序研究。",
                seed_evidence=[],
                possible_dataset_or_signal="纵向 EHR、ECG、药物记录、症状量表。",
                candidate_method="从 landmark model 或 Cox baseline 起步，再尝试深度时序模型。",
                paper_or_project_shape="偏 methods + clinical relevance 的研究短文。",
                score=0.0,
            )
        )
    fallback.append(
        IdeaCandidate(
            idea_title="建立 psychiatry + AI 前沿地图，识别未来 12 个月最可落地的交叉题目",
            why_now="前沿主题分散但收敛很快，用系统化雷达做方向筛选能降低选题噪声。",
            seed_evidence=[],
            possible_dataset_or_signal="公开论文摘要、机构动态、clinical AI 产品更新。",
            candidate_method="做主题聚类 + 规则标注的 scoping radar。",
            paper_or_project_shape="方向论文、综述前期工作或 grant ideation memo。",
            score=0.0,
        )
    )
    return fallback
