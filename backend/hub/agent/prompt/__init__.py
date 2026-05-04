"""Plan 6 Task 5：PromptBuilder + 业务词典 + 同义词 + few-shots。"""
from hub.agent.prompt.builder import PromptBuilder
from hub.agent.prompt.business_dict import DEFAULT_DICT, render_dict
from hub.agent.prompt.few_shots import DEFAULT_FEW_SHOTS, FewShot, render_few_shots
from hub.agent.prompt.synonyms import DEFAULT_SYNONYMS, normalize, render_synonyms

__all__ = [
    "PromptBuilder",
    "DEFAULT_DICT", "render_dict",
    "DEFAULT_SYNONYMS", "render_synonyms", "normalize",
    "DEFAULT_FEW_SHOTS", "FewShot", "render_few_shots",
]
