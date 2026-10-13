"""Scoped capacity query over the existing, source-verified book comparison.

Does not change MRP, merge stock observations, or establish formal availability.
Unknown records stay unknown. A verified zero upper bound is conclusive even
when other required materials have no verified stock observation.
"""
from decimal import Decimal, ROUND_FLOOR, localcontext


class BookCapacityQueryService:
    def __init__(self, book):
        self.book = book

    def query(self, product, revision=None):
        result = self.book.analyze(product, revision, quantity=1)
        if not result.get("data") or result["status"] != "ok":
            return result
        data = result["data"]
        capacities = []
        for row in data["materials"]:
            row["verified_capacity"] = None
            per = Decimal(row["qty_per"])
            stock = row.get("recorded_stock_qty")
            if data.get("quantity_review") and row["comparison_status"] == "comparable" and per.is_finite() and per > 0 and stock is not None:
                stock = Decimal(stock)
                if stock.is_finite() and stock >= 0:
                    # Precision scales with source operands, not binary floats.
                    with localcontext() as ctx:
                        ctx.prec = max(28, len(stock.as_tuple().digits) + len(per.as_tuple().digits) + abs(stock.adjusted() - per.adjusted()) + 4)
                        capacity = int((stock / per).to_integral_value(rounding=ROUND_FLOOR))
                    row["verified_capacity"] = capacity
                    capacities.append((capacity, row))
            row["required_for_one"] = row["required_qty"]
            row["shortage_for_one"] = row["book_shortage_qty"]
            # This query has no user-specified production target.
            for field in ("required_qty", "shortage_qty", "book_shortage_qty", "book_difference", "suggested_purchase_qty"):
                row[field] = None
            row["quantity_status"] = "unknown"
        maximum = min((c for c, _ in capacities), default=None)
        full = bool(data["materials"]) and len(capacities) == len(data["materials"])
        determined = maximum is not None and (full or maximum == 0)
        data.update(requested_quantity=None, capacity_query=True,
            verified_material_count=len(capacities), verified_material_capacity=maximum,
            capacity_scope="complete" if full else "verified_subset",
            full_bom_capacity_determined=determined,
            theoretical_max_quantity=maximum if determined else None,
            inventory_sufficient_for_requested=None, quantity_check_status=None,
            book_shortages=[], recommendations=[],
            limiting_materials=[dict(material_code=r["material_code"], name=r["name"], qty_per=r["qty_per"],
                stock_qty=r["recorded_stock_qty"], capacity=c, shortage_for_one=r["shortage_for_one"],
                bom_line_sources=r["bom_line_sources"], stock_sources=r["stock_sources"])
                for c, r in capacities if c == maximum])
        result["query"].update(requested_quantity=None, operation="verified_book_capacity")
        result["completeness"].update(verified_capacity=maximum is not None, full_bom_capacity=determined,
            inventory_coverage=full, demand=False)
        if maximum is None:
            result["status"] = "data_unavailable"
        return result
