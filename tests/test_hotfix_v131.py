from __future__ import annotations
import logging, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.errors import DependencyError

from pdf_support import PDF_CRYPTO_MESSAGE, PDF_PASSWORD_MESSAGE, open_pdf_reader
from provider import (
    ChatRequest, OpenAIResponsesProvider, ProviderAuthenticationError, ProviderCapabilityError,
    ProviderConfig, ProviderEndpointError, ProviderModelError, ProviderRateLimitError,
    ProviderRequestError, ProviderTimeoutError, QwenProvider,
)

ROOT=Path(__file__).resolve().parents[1]


class FakeResponses:
    def __init__(self,error=None):self.error=error;self.calls=[]
    def create(self,**kwargs):
        self.calls.append(kwargs)
        if self.error:raise self.error
        return types.SimpleNamespace(output_text='{"summary":"ok","findings":[]}',usage={},status="completed",id="safe")


class SDKHarness:
    def __init__(self,error=None):self.responses=FakeResponses(error)
    def module(self):
        owner=self
        class Client:
            def __init__(self,**kwargs):self.responses=owner.responses
        return types.SimpleNamespace(OpenAI=Client)


class UpstreamError(Exception):
    def __init__(self,status,message,error_type="",code=""):
        super().__init__(message);self.status_code=status
        self.body={"error":{"message":message,"type":error_type,"code":code}}


class HotfixV131Tests(unittest.TestCase):
    def _provider_call(self,provider,content,error=None):
        sdk=SDKHarness(error)
        with patch.dict(sys.modules,{"openai":sdk.module()}):
            result=provider.chat(ChatRequest(provider.config.model,[{"role":"user","content":content}]))
        return result,sdk

    def test_official_openai_supports_responses_direct_file(self):
        provider=OpenAIResponsesProvider(ProviderConfig("openai","gpt-test","secret",""))
        self.assertTrue(provider.supports("direct_file_input"));self.assertTrue(provider.supports("file_reference"))
        _,sdk=self._provider_call(provider,[{"type":"input_file","filename":"a.pdf","file_data":"data:application/pdf;base64,QQ=="}])
        self.assertEqual(sdk.responses.calls[0]["input"][0]["content"][0]["type"],"input_file")

    def test_custom_openai_compatible_allows_text_but_blocks_direct_file_before_call(self):
        provider=OpenAIResponsesProvider(ProviderConfig("openai","compatible-model","secret","https://api.ofox.ai/v1"))
        self.assertTrue(provider.supports("text_input"));self.assertFalse(provider.supports("direct_file_input"))
        response,sdk=self._provider_call(provider,"hello");self.assertTrue(response.text);self.assertEqual(len(sdk.responses.calls),1)
        sdk=SDKHarness()
        with patch.dict(sys.modules,{"openai":sdk.module()}),self.assertRaisesRegex(ProviderCapabilityError,"不支持直接 PDF/文件审查"):
            provider.chat(ChatRequest(provider.config.model,[{"role":"user","content":[{"type":"input_file","file_data":"data:application/pdf;base64,SECRET"}]}]))
        self.assertEqual(sdk.responses.calls,[])

    def test_custom_compatible_base_does_not_inherit_known_provider_image_capability(self):
        official=QwenProvider(ProviderConfig("qwen","qwen-test","secret","https://dashscope.aliyuncs.com/compatible-mode/v1"))
        custom=QwenProvider(ProviderConfig("qwen","qwen-test","secret","https://proxy.example.invalid/v1"))
        self.assertTrue(official.supports("image_input"));self.assertFalse(custom.supports("image_input"));self.assertTrue(custom.supports("text_input"))

    def test_upstream_400_logs_safe_structured_summary(self):
        secret="sk-never-log-this-secret";payload="A"*300
        error=UpstreamError(400,f"invalid file_data data:application/pdf;base64,{payload} authorization=Bearer {secret}","invalid_request_error","bad_file")
        provider=OpenAIResponsesProvider(ProviderConfig("openai","gpt-test",secret,""))
        with self.assertLogs("provider",logging.ERROR) as logs,self.assertRaises(ProviderRequestError) as caught:
            self._provider_call(provider,"hello",error)
        text=" ".join(logs.output)
        self.assertIn('"http_status": 400',text);self.assertIn('"error_code": "bad_file"',text);self.assertIn('"endpoint": "/responses"',text)
        self.assertNotIn(secret,text);self.assertNotIn(payload,text);self.assertNotIn("Authorization: Bearer",text)
        self.assertEqual((caught.exception.error_type,caught.exception.error_code),("invalid_request_error","bad_file"))

    def test_status_and_timeout_mapping_remain_specific(self):
        provider=OpenAIResponsesProvider(ProviderConfig("openai","gpt-test","secret",""))
        cases=((UpstreamError(401,"bad key"),ProviderAuthenticationError),(UpstreamError(404,"unknown endpoint"),ProviderEndpointError),
               (UpstreamError(404,"model not found"),ProviderModelError),(UpstreamError(429,"rate limit"),ProviderRateLimitError),
               (TimeoutError("request timed out"),ProviderTimeoutError))
        for error,expected in cases:
            with self.subTest(expected=expected),self.assertRaises(expected):self._provider_call(provider,"hello",error)

    def _review_patches(self):
        return (patch("review_engine._norm_evidence",return_value=[]),patch("review_engine.search_project_chunks",return_value=[]),
                patch("review_engine.list_project_requirements",return_value=[]),patch("review_engine.save_review",return_value=1),
                patch("review_engine.project_context_text",return_value="project"))

    def test_review_direct_file_supported_and_unsupported_preflight(self):
        import review_engine
        with tempfile.TemporaryDirectory(dir=ROOT/".test-tmp") as tmp:
            pdf=Path(tmp)/"drawing.pdf";pdf.write_bytes(b"%PDF-safe-test")
            official=types.SimpleNamespace(provider_id="openai",config=types.SimpleNamespace(model="gpt"),supports=lambda c:True,calls=[])
            official.generate=lambda **kw:official.calls.append(kw) or '{"summary":"ok","findings":[]}'
            patches=self._review_patches()
            with patch("review_engine.resolve_provider",return_value=official),patches[0],patches[1],patches[2],patches[3],patches[4]:
                result=review_engine.run_review({"id":1,"name":"p"},[str(pdf)],"施工图审查")
            self.assertEqual(result["meta"]["input_mode"],"direct_file");file_item=official.calls[0]["input"][0]["content"][1]
            self.assertEqual(file_item["type"],"input_file");self.assertNotIn("detail",file_item)
            custom=OpenAIResponsesProvider(ProviderConfig("openai","m","secret","https://api.ofox.ai/v1"))
            with patch("review_engine.resolve_provider",return_value=custom),patches[0],patches[1],patches[2],patches[3],patches[4],self.assertRaisesRegex(ProviderCapabilityError,"不支持直接 PDF/文件审查"):
                review_engine.run_review({"id":1,"name":"p"},[str(pdf)],"施工图审查")

    def test_review_text_fallback_is_explicit(self):
        import review_engine
        with tempfile.TemporaryDirectory(dir=ROOT/".test-tmp") as tmp:
            text_file=Path(tmp)/"plan.txt";text_file.write_text("施工方案正文",encoding="utf-8")
            fake=types.SimpleNamespace(provider_id="compatible",config=types.SimpleNamespace(model="m"),supports=lambda c:c=="text_input",calls=[])
            fake.generate=lambda **kw:fake.calls.append(kw) or '{"summary":"ok","findings":[]}'
            patches=self._review_patches()
            with patch("review_engine.resolve_provider",return_value=fake),patches[0],patches[1],patches[2],patches[3],patches[4]:
                result=review_engine.run_review({"id":1,"name":"p"},[str(text_file)],"施工方案审查")
            self.assertEqual(result["meta"]["input_mode"],"extracted_text");self.assertTrue(result["meta"]["input_warnings"])
            self.assertIn("兼容降级",fake.calls[0]["input"][0]["content"][1]["text"]);self.assertIn("输入模式提示",result["summary"])

    def test_pdf_crypto_dependency_password_and_plain_pdf(self):
        self.assertIn("cryptography>=3.1",(ROOT/"requirements-build.txt").read_text(encoding="utf-8"))
        self.assertIn('collect_submodules("cryptography")',(ROOT/"installer"/"EngineeringNormAgent.spec").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=ROOT/".test-tmp") as tmp:
            plain=Path(tmp)/"plain.pdf";writer=PdfWriter();writer.add_blank_page(width=100,height=100)
            with plain.open("wb") as f:writer.write(f)
            self.assertEqual(len(open_pdf_reader(plain).pages),1)
            protected=Path(tmp)/"protected.pdf";writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt("required-password",algorithm="AES-256")
            with protected.open("wb") as f:writer.write(f)
            with self.assertRaisesRegex(ValueError,PDF_PASSWORD_MESSAGE):open_pdf_reader(protected)
            empty_password=Path(tmp)/"aes-empty-password.pdf";writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt("",algorithm="AES-256")
            with empty_password.open("wb") as f:writer.write(f)
            self.assertEqual(len(open_pdf_reader(empty_password).pages),1)
        with patch("pdf_support.PdfReader",side_effect=DependencyError("cryptography missing")),self.assertRaisesRegex(RuntimeError,PDF_CRYPTO_MESSAGE):
            open_pdf_reader("missing-crypto.pdf")


if __name__=="__main__":unittest.main()
