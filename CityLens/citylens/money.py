"""Business amounts are integer CNY cents; model/display amounts are explicit yuan."""
from decimal import Decimal


def yuan(cents):
    if type(cents) is not int or cents < 0:
        return None
    return format(Decimal(cents) / 100, '.2f')


def coupon_observation(coupon):
    return {**{key: coupon.get(key) for key in ('id', 'title', 'rules', 'orderable')},
            'currency': 'CNY', 'pay_yuan': yuan(coupon.get('payValue')),
            'face_yuan': yuan(coupon.get('actualValue'))}


def coupon_price_facts(shops):
    """Render amounts from tool facts without asking the model to convert units."""
    lines = []
    seen = set()
    for shop in shops:
        for coupon in shop.coupons:
            key = (shop.id, coupon.get('id'))
            if key in seen:
                continue
            seen.add(key)
            parts = []
            for label, field in (('售价', 'payValue'), ('面额', 'actualValue')):
                value = yuan(coupon.get(field))
                if value is not None:
                    parts.append(f'{label} ¥{value}')
            if parts:
                lines.append(f"{shop.name} · {coupon.get('title') or '优惠券'}：" + '，'.join(parts))
    return '\n'.join(lines)
