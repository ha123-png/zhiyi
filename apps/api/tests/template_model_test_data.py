"""Synthetic invoice response matching the public, editable starter contract."""


def invoice_response(*, total_amount=106, document_number='NO-1', name='服务'):
    return {
        'header': {
            'seller_name': '销售方', 'buyer_name': '购买方', 'document_number': document_number,
            'document_date': '2026-07-30', 'amount_before_tax': 100, 'tax_amount': 6,
            'total_amount': total_amount,
        },
        'items': [{
            'name': name, 'specification': None, 'unit': '项', 'quantity': 1,
            'unit_price': 100, 'item_amount': 100, 'tax_rate': '6%', 'item_tax_amount': 6,
        }],
    }
