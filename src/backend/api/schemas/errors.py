"""面向演示客户端的受控错误契约。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PublicErrorResponse(BaseModel):
    """不携带异常文本、堆栈或内部工作流字段的公开错误。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    user_message: str
