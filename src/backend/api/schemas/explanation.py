"""PMC 结果解释层的受控公开契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExplanationLlmOutput(BaseModel):
    """DeepSeek 只允许返回解释文本，不能回写任何业务字段。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answer: str = Field(min_length=1, max_length=2000)


class ExplanationResponse(BaseModel):
    """供 Chat 展示的解释结果，所有事实仍来自既有结构化报告。"""

    topic: Literal["blocked", "ready", "impact", "general"]
    answer: str
    source: Literal["llm", "template_fallback"]
