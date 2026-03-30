import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import oanda_calculator_web as web_app


def test_embedded_form_action_includes_forwarded_prefix():
    client = web_app.app.test_client()
    resp = client.get(
        "/?embedded=1&shell=merged&title=Position+Size+Calculator",
        headers={"X-Forwarded-Prefix": "/apps/oanda-calculator-clone"},
    )
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert (
        'action="/apps/oanda-calculator-clone/?embedded=1&amp;shell=merged&amp;title=Position+Size+Calculator"'
        in html
    )
