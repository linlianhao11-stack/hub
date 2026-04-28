import pytest


@pytest.mark.asyncio
async def test_unique_match():
    from hub.match.resolver import MatchOutcome, MatchResolver

    candidates = [{"id": 1, "label": "A 商品", "subtitle": "SKU 1"}]
    res = MatchResolver().resolve(keyword="A", resource="商品", candidates=candidates, max_show=5)
    assert res.outcome == MatchOutcome.UNIQUE
    assert res.selected == candidates[0]


@pytest.mark.asyncio
async def test_multi_match():
    from hub.match.resolver import MatchOutcome, MatchResolver

    candidates = [
        {"id": 1, "label": "A1"}, {"id": 2, "label": "A2"}, {"id": 3, "label": "A3"},
    ]
    res = MatchResolver().resolve(keyword="A", resource="商品", candidates=candidates, max_show=5)
    assert res.outcome == MatchOutcome.MULTI
    assert len(res.choices) == 3


@pytest.mark.asyncio
async def test_no_match():
    from hub.match.resolver import MatchOutcome, MatchResolver
    res = MatchResolver().resolve(keyword="zzz", resource="商品", candidates=[], max_show=5)
    assert res.outcome == MatchOutcome.NONE


@pytest.mark.asyncio
async def test_multi_truncates_to_max_show():
    from hub.match.resolver import MatchOutcome, MatchResolver

    candidates = [{"id": i, "label": f"X{i}"} for i in range(20)]
    res = MatchResolver().resolve(keyword="X", resource="商品", candidates=candidates, max_show=5)
    assert res.outcome == MatchOutcome.MULTI
    assert len(res.choices) == 5
    assert res.truncated is True


@pytest.mark.asyncio
async def test_resolve_choice_by_number():
    """根据用户回复的编号定位 candidates 中具体项。"""
    from hub.match.resolver import MatchResolver

    candidates = [{"id": 10, "label": "X"}, {"id": 11, "label": "Y"}]
    chosen = MatchResolver().resolve_choice(candidates, choice_number=2)
    assert chosen == candidates[1]


@pytest.mark.asyncio
async def test_resolve_choice_out_of_range():
    from hub.match.resolver import MatchResolver
    candidates = [{"id": 1, "label": "X"}]
    chosen = MatchResolver().resolve_choice(candidates, choice_number=99)
    assert chosen is None
