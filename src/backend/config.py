import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


# 加载.env文件
if os.getenv("PMC_TRIAL_MODE") != "1" and os.getenv("PMC_REVIEW_MODE") != "1":
    load_dotenv()


DEFAULT_LLM_TIMEOUT_SECONDS = 6.0
DEFAULT_LLM_MAX_RETRIES = 1


def _llm_timeout_seconds() -> float:
    try:
        default = 25.0 if os.getenv("PMC_CHART_AI_MODE") == "1" else DEFAULT_LLM_TIMEOUT_SECONDS
        value = float(os.getenv("LLM_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return DEFAULT_LLM_TIMEOUT_SECONDS
    return min(max(value, 1.0), 30.0)


def _llm_max_retries() -> int:
    try:
        default = 0 if os.getenv("PMC_CHART_AI_MODE") == "1" else DEFAULT_LLM_MAX_RETRIES
        value = int(os.getenv("LLM_MAX_RETRIES", str(default)))
    except ValueError:
        return DEFAULT_LLM_MAX_RETRIES
    return min(max(value, 0), 2)


def get_llm_configuration_status() -> dict[str, object]:
    """返回不包含 Key 或地址内容的 LLM 就绪状态。"""
    if os.getenv("PMC_TRIAL_MODE") == "1" or (os.getenv("PMC_REVIEW_MODE") == "1" and os.getenv("PMC_CHART_AI_MODE") != "1"):
        return {"status": "warning", "provider": "disabled", "message": "受控评审进程禁止模型出站", "fallback_available": True}
    selected_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if selected_provider not in {"deepseek", "mimo"}:
        return {
            "status": "warning",
            "provider": "not_configured",
            "message": "LLM 未配置，将使用本地规则和确定性模板回退",
            "fallback_available": True,
        }
    prefix = "DEEPSEEK" if selected_provider == "deepseek" else "MIMO"
    configured = bool(os.getenv(f"{prefix}_API_KEY")) and bool(os.getenv(f"{prefix}_BASE_URL"))
    if os.getenv("PMC_CHART_AI_MODE") == "1":
        configured = configured and selected_provider == "deepseek" and _approved_deepseek_url()
    return {
        "status": "ok" if configured else "warning",
        "provider": selected_provider,
        "message": "LLM 配置已就绪" if configured else "LLM 配置不完整，将使用本地回退",
        "fallback_available": True,
    }


def _approved_deepseek_url():
    from urllib.parse import urlsplit
    try:
        url = urlsplit(os.getenv("DEEPSEEK_BASE_URL", ""))
        return (url.scheme == "https" and url.hostname == "api.deepseek.com" and
                url.port in (None, 443) and url.username is None and url.password is None and
                not url.query and not url.fragment)
    except ValueError:
        return False


def get_llm():
    if os.getenv("PMC_TRIAL_MODE") == "1" or (os.getenv("PMC_REVIEW_MODE") == "1" and os.getenv("PMC_CHART_AI_MODE") != "1"):
        raise RuntimeError("Controlled review model egress is disabled")
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if os.getenv("PMC_CHART_AI_MODE") == "1" and (provider != "deepseek" or not _approved_deepseek_url()):
        raise RuntimeError("Chart AI requires the approved DeepSeek endpoint")
    common_options = {
        "temperature": 0,
        "request_timeout": _llm_timeout_seconds(),
        "max_retries": _llm_max_retries(),
    }

    if provider == "deepseek":

        llm = ChatOpenAI(
            model="deepseek-chat",
            api_key=os.getenv(
                "DEEPSEEK_API_KEY"
            ),
            base_url=os.getenv(
                "DEEPSEEK_BASE_URL"
            ),
            **common_options,
        )

        return llm


    elif provider == "mimo":

        llm = ChatOpenAI(
            model="MiMo-V2.5",
            api_key=os.getenv(
                "MIMO_API_KEY"
            ),
            base_url=os.getenv(
                "MIMO_BASE_URL"
            ),
            **common_options,
        )

        return llm


    else:

        raise Exception(
            "没有配置LLM模型"
        )
