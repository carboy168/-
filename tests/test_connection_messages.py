from __future__ import annotations
import types
import unittest

from desktop.ui.connection_messages import connection_error_text,connection_success_text
from provider import (
    ProviderAuthenticationError,ProviderError,ProviderModelError,ProviderNetworkError,
    ProviderPermissionError,ProviderTimeoutError,
)


class ConnectionMessageTests(unittest.TestCase):
    def test_success_text_does_not_depend_on_response_body(self):
        response=types.SimpleNamespace(text="",provider="deepseek",model="deepseek-reasoner")
        text=connection_success_text("DeepSeek","deepseek-reasoner",response)
        self.assertEqual(text,"连接成功\n\nProvider: DeepSeek\nModel: deepseek-reasoner")

    def test_failure_texts_are_specific_and_do_not_echo_exception(self):
        secret="sk-never-show-this-value"
        cases=(
            (ProviderAuthenticationError(secret,status_code=401),"API Key无效 / 401"),
            (ProviderPermissionError(secret,status_code=403),"权限不足 / 403"),
            (ProviderModelError(secret,status_code=404),"模型不存在 / 404或模型错误"),
            (ProviderNetworkError(secret),"网络连接失败"),
            (ProviderTimeoutError(secret),"请求超时"),
            (ProviderError(secret,status_code=404),"模型不存在 / 404或模型错误"),
        )
        for exc,expected in cases:
            with self.subTest(expected=expected):
                text=connection_error_text(exc)
                self.assertIn(expected,text)
                self.assertNotIn(secret,text)


if __name__=="__main__":unittest.main()
