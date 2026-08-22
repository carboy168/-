from __future__ import annotations
import logging,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import db
from log_security import SensitiveDataFilter,redact_sensitive
from provider import (ChatRequest,DeepSeekProvider,OpenAIResponsesProvider,ProviderAuthenticationError,
 ProviderConfig,ProviderConfigurationError,ProviderModelError,ProviderRateLimitError,ProviderRegistry,
 ProviderServerError,ProviderTimeoutError,QwenProvider,REGISTRY,ZhipuProvider)

class FakeResponses:
    def __init__(self,error=None):self.calls=[];self.error=error
    def create(self,**kw):
        self.calls.append(kw)
        if self.error:raise self.error
        return types.SimpleNamespace(output_text="ok",usage={"total_tokens":3},status="completed",id="safe-id")
class FakeCompletions(FakeResponses):
    def create(self,**kw):
        self.calls.append(kw)
        if self.error:raise self.error
        choice=types.SimpleNamespace(message=types.SimpleNamespace(content="ok"),finish_reason="stop")
        return types.SimpleNamespace(choices=[choice],usage={"total_tokens":3},id="safe-id")
class SDKHarness:
    def __init__(self,error=None):self.responses=FakeResponses(error);self.completions=FakeCompletions(error);self.clients=[]
    def module(self):
        owner=self
        class Client:
            def __init__(self,**kw):owner.clients.append(kw);self.responses=owner.responses;self.chat=types.SimpleNamespace(completions=owner.completions)
        return types.SimpleNamespace(OpenAI=Client)
class StatusError(Exception):
    def __init__(self,status_code,message="error"):super().__init__(message);self.status_code=status_code

class MemorySecrets:
    def __init__(self):self.values={}
    def save(self,p,s):self.values[p]=s;return "memory:"+p
    def load(self,p):return self.values.get(p,"")
    def delete(self,p):self.values.pop(p,None)

class ProviderTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/".test-tmp";base.mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=base);self.old=db.DB_PATH;db.DB_PATH=Path(self.tmp.name)/"db.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH)
        from migrations import migrate;migrate()
    def tearDown(self):db.DB_PATH=self.old;self.tmp.cleanup()
    def _run(self,cls,error=None):
        sdk=SDKHarness(error)
        with patch.dict(sys.modules,{"openai":sdk.module()}):response=cls(ProviderConfig(cls.provider_id,"model","secret","https://example.invalid/v1",12)).chat(ChatRequest("model",[{"role":"user","content":"hello"}],system_prompt="grounded"))
        return response,sdk
    def test_registry_has_four_providers(self):self.assertEqual(set(REGISTRY.ids()),{"openai","deepseek","qwen","zhipu"})
    def test_registry_rejects_unknown(self):
        with self.assertRaises(ProviderConfigurationError):ProviderRegistry().create(ProviderConfig("bad","m","k"))
    def test_openai_adapter(self):
        response,sdk=self._run(OpenAIResponsesProvider);self.assertEqual((response.text,response.provider),("ok","openai"));self.assertIn("input",sdk.responses.calls[0])
    def test_compatible_adapters_and_switching(self):
        for cls,pid in ((DeepSeekProvider,"deepseek"),(QwenProvider,"qwen"),(ZhipuProvider,"zhipu")):
            response,sdk=self._run(cls);self.assertEqual(response.provider,pid);self.assertIn("messages",sdk.completions.calls[0])
    def test_error_mapping(self):
        for error,expected in ((StatusError(401),ProviderAuthenticationError),(StatusError(429),ProviderRateLimitError),(StatusError(500),ProviderServerError),(TimeoutError("timed out"),ProviderTimeoutError),(Exception("model not found"),ProviderModelError)):
            with self.assertRaises(expected):self._run(DeepSeekProvider,error)
    def test_config_persistence_has_no_key(self):
        from provider_config import get_provider_config,save_provider_config
        save_provider_config("deepseek","deepseek-chat","https://example.invalid",30,True,True,"memory:deepseek");row=get_provider_config();self.assertEqual(row["provider_id"],"deepseek");self.assertNotIn("api_key",row);self.assertNotIn("secret",str(row).lower().replace("secret_ref",""))
    def test_secret_store(self):
        from desktop import secret_store
        memory=MemorySecrets()
        with patch("desktop.secret_store.get_secret_store",return_value=memory):
            ref=secret_store.save_provider_secret("qwen","private-value");self.assertEqual(ref,"memory:qwen");self.assertEqual(secret_store.load_provider_secret("qwen"),"private-value");secret_store.delete_provider_secret("qwen");self.assertEqual(secret_store.load_provider_secret("qwen"),"")
    def test_sensitive_logging_redaction(self):
        key="sk-test-super-secret-value";self.assertNotIn(key,redact_sensitive("authorization=Bearer "+key));self.assertNotIn("opaque-token",redact_sensitive("Authorization: Bearer opaque-token"))
        record=logging.LogRecord("x",logging.ERROR,"",0,"api_key="+key,(),None);SensitiveDataFilter().filter(record);self.assertNotIn(key,record.getMessage())
    def test_provider_unconfigured(self):
        with self.assertRaises(ProviderConfigurationError):DeepSeekProvider(ProviderConfig("deepseek","model",""))
    def test_migration_idempotent_and_preserves_legacy_config(self):
        from migrations import LATEST_SCHEMA_VERSION,migrate
        with db.connect() as con:con.execute("INSERT INTO app_settings(key,value) VALUES('legacy','keep')")
        self.assertEqual(migrate(),LATEST_SCHEMA_VERSION);self.assertEqual(migrate(),LATEST_SCHEMA_VERSION)
        with db.connect() as con:self.assertEqual(con.execute("SELECT value FROM app_settings WHERE key='legacy'").fetchone()[0],"keep")
    def test_legacy_provider_inference(self):
        from provider_config import get_provider_config,migrate_legacy_openai_settings
        class Settings:
            def __init__(self,model,base_url=""):self.data={"openai_model":model,"openai_base_url":base_url}
            def get(self,k,d=None):return self.data.get(k,d)
            def set(self,k,v):self.data[k]=v
        cases=(("deepseek-reasoner","","deepseek"),("qwen-plus","","qwen"),("glm-4-plus","","zhipu"),("gpt-4.1","","openai"))
        for model,url,expected in cases:
            with self.subTest(model=model):
                with db.connect() as con:con.execute("DELETE FROM provider_configs")
                with patch("desktop.secret_store.load_provider_secret",return_value=""):
                    migrate_legacy_openai_settings(Settings(model,url))
                row=get_provider_config();self.assertEqual(row["provider_id"],expected);self.assertEqual(row["model"],model)
    def test_legacy_conflict_requires_confirmation(self):
        from provider_config import get_provider_config,list_provider_configs,migrate_legacy_openai_settings,save_provider_config
        class Settings:
            data={"openai_model":"deepseek-reasoner","openai_base_url":"https://api.openai.com/v1"}
            def get(self,k,d=None):return self.data.get(k,d)
            def set(self,k,v):self.data[k]=v
        with patch("desktop.secret_store.load_provider_secret",return_value=""):migrate_legacy_openai_settings(Settings())
        self.assertIsNone(get_provider_config());pending=list_provider_configs()[0];self.assertEqual(pending["provider_id"],"legacy-unassigned");self.assertTrue(pending["needs_confirmation"])
        with self.assertRaises(ValueError):save_provider_config("openai","deepseek-reasoner")
    def test_repairs_bad_openai_row_idempotently_without_overwriting_secret(self):
        from provider_config import get_provider_config,list_provider_configs,migrate_legacy_openai_settings
        class Settings:
            def get(self,k,d=None):return d
            def set(self,k,v):pass
        with db.connect() as con:
            con.execute("INSERT INTO provider_configs(provider_id,model,secret_ref,key_configured,is_default) VALUES('openai','deepseek-reasoner','windows-credential:openai',1,1)")
        loaded=[];saved=[]
        def load(pid):loaded.append(pid);return "existing-target" if pid=="deepseek" else "legacy-key"
        with patch("desktop.secret_store.load_provider_secret",side_effect=load),patch("desktop.secret_store.save_provider_secret",side_effect=lambda p,s:saved.append((p,s)) or "new-ref"):
            migrate_legacy_openai_settings(Settings());migrate_legacy_openai_settings(Settings())
        row=get_provider_config();self.assertEqual(row["provider_id"],"deepseek");self.assertEqual(row["model"],"deepseek-reasoner");self.assertEqual(saved,[])
        raw={x["provider_id"]:x for x in list_provider_configs()};self.assertTrue(raw["openai"]["needs_confirmation"]);self.assertEqual(raw["openai"]["secret_ref"],"windows-credential:openai")
    def test_legacy_secret_is_copied_not_deleted(self):
        from provider_config import migrate_legacy_openai_settings
        class Settings:
            data={"openai_model":"qwen-plus"}
            def get(self,k,d=None):return self.data.get(k,d)
            def set(self,k,v):self.data[k]=v
        saved=[]
        with patch("desktop.secret_store.load_provider_secret",side_effect=lambda p:"legacy-key" if p=="openai" else ""),patch("desktop.secret_store.save_provider_secret",side_effect=lambda p,s:saved.append((p,s)) or "memory:qwen"),patch("desktop.secret_store.delete_provider_secret") as delete:
            migrate_legacy_openai_settings(Settings());migrate_legacy_openai_settings(Settings())
        self.assertEqual(saved,[("qwen","legacy-key")]);delete.assert_not_called()
    def test_rag_insufficient_evidence_never_calls_provider(self):
        import rag
        with patch("rag.resolve_provider",side_effect=AssertionError("provider must not run")):
            text=rag.answer("未知问题",[],project=None,overlay={})
        self.assertIn("不能据此下确定结论",text)
    def test_review_engine_uses_unified_provider(self):
        import review_engine
        fake=types.SimpleNamespace(config=types.SimpleNamespace(model="mock"),generate=lambda **kw:'{"summary":"ok","findings":[]}')
        sample=Path(self.tmp.name)/"plan.txt";sample.write_text("plan",encoding="utf-8")
        project={"id":1,"name":"p"}
        with patch("review_engine.resolve_provider",return_value=fake),patch("review_engine._norm_evidence",return_value=[]),patch("review_engine.search_project_chunks",return_value=[]),patch("review_engine.list_project_requirements",return_value=[]),patch("review_engine.save_review",return_value=1),patch("review_engine.project_context_text",return_value="p"):
            result=review_engine.run_review(project,[str(sample)],"施工方案审查")
        self.assertEqual(result["summary"],"ok")

if __name__=="__main__":unittest.main()
