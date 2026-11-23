from __future__ import annotations

import pytest

from adguard_training.prompts import system_prompt as training_system_prompt
from adguard_training.prompts import user_prompt as training_user_prompt

pytestmark = pytest.mark.skipif(
    __import__("importlib").util.find_spec("adguard_pipeline") is None,
    reason="adguard_pipeline not importable (serving package not installed in this env)",
)


def test_serving_prompt_matches_training_prompt():
    """The model must see byte-identical prompts at train and inference time."""
    from adguard_pipeline import model_prompt

    assert training_system_prompt() == model_prompt.system_prompt()
    assert training_user_prompt("测试文案") == model_prompt.user_prompt("测试文案")
