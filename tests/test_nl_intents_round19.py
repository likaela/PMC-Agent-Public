"""第19轮新增的针对性单元测试：意图分流、统计口径与拒答边界。

只测试纯函数/正则与解析结果，不访问数据库、不写库存、不依赖浏览器。
"""

from agents.business_language import refusal_kind
from agents.query_presentation import maximum_requested
from services import pmc_capability_service as pmc
from services.production_request_context import parse_quantity


# ---------------------------------------------------------------- 拒答边界
def test_fabrication_request_is_refused():
    assert refusal_kind("帮我编一个库存数据") == "fabrication"
    assert refusal_kind("随便编几条库存数据给我") == "fabrication"
    assert refusal_kind("帮我伪造一批入库记录") == "fabrication"


def test_unrelated_request_is_refused():
    assert refusal_kind("今天天气怎么样？") == "unrelated"
    assert refusal_kind("帮我写一首诗") == "unrelated"
    assert refusal_kind("asdkjh") == "unrelated"


def test_business_requests_are_not_refused():
    for message in ("KQ-Z112 生产 50 片够不够", "最急缺的是哪一种", "查一下贴片电阻的个数",
                    "库存为零的物料有哪些", "重复记录有多少条", "为什么有些物料不能核算"):
        assert refusal_kind(message) is None, message


def test_greeting_is_not_refused():
    assert refusal_kind("你好") is None


# ---------------------------------------------------------------- 产能问法
def test_capacity_question_variants():
    for message in ("KQ-Z112 最多能生产多少片", "KQ-Z112 当前库存够生产多少",
                    "KQ-Z112 现有库存能做多少片", "生产上限是多少"):
        assert maximum_requested(message) is True, message


def test_quantity_check_is_not_capacity_question():
    for message in ("KQ-Z112 生产 50 片够不够", "库存够不够生产 60 片", "KQ-Z112 生产 100 片，哪些物料缺"):
        assert maximum_requested(message) is False, message


# ---------------------------------------------------------------- 统计口径分流
def test_bom_row_count_pattern():
    for message in ("KQ-BHX3588M 有多少行物料？", "KQ-Z112 的 BOM 有多少条物料记录？",
                    "KQ-BHX3588M 一共有多少行 BOM"):
        assert pmc._BOM_ROWS.search(message), message
    # “多少种物料”是元器件种类统计，不能走行数口径
    assert not pmc._BOM_ROWS.search("KQ-Z112 的 BOM 有多少种物料？")


def test_inventory_record_count_pattern():
    for message in ("库存有多少条记录？", "库存总共有几条记录", "库存表记录条数是多少"):
        assert pmc._INV_RECORD_COUNT.search(message), message
    assert not pmc._INV_RECORD_COUNT.search("库存一共多少种物料？")


def test_zero_stock_pattern():
    assert pmc._ZERO_STOCK.search("库存为零的物料有哪些")
    assert pmc._ZERO_STOCK.search("整个库存里有没有零库存的物料")
    # 缺记录不等于零库存
    assert not pmc._ZERO_STOCK.search("哪些物料没有库存记录")


def test_duplicate_observation_pattern():
    assert pmc._DUPLICATE_OBS.search("哪些物料库存记录重复")
    assert pmc._DUPLICATE_OBS.search("重复记录有多少条")
    assert not pmc._DUPLICATE_OBS.search("库存有多少条记录")


# ---------------------------------------------------------------- 数量解析
def test_interrogative_pronoun_is_not_quantity():
    request = parse_quantity("最急缺的物料是哪一个")
    assert request.status == "missing" and request.quantity is None


def test_explicit_quantity_still_parsed():
    request = parse_quantity("KQ-Z112 生产 100 片够吗")
    assert request.status == "valid" and request.quantity == 100
