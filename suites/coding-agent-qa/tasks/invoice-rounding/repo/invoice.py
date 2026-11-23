def total(lines):
    """lines: (name, unit_price_str, qty)"""
    return sum(float(price) * qty for _, price, qty in lines)
