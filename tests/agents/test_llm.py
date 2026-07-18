from novelizer.agents.llm import build_chat_model


def test_build_chat_model_targets_given_model_and_endpoint():
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key", temperature=0.5)
    # ChatOpenAI stores the model name and base URL; no network call is made here.
    assert m.model_name == "my-model"
    assert "1234" in str(m.openai_api_base)


def test_build_chat_model_caps_generation_length_by_default():
    # Without a cap, a temperature-0.8 local model can free-run for tens of
    # thousands of tokens on one call (observed live: 42k decoded in a single
    # stream). Every agent call must carry a finite max_tokens.
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key", temperature=0.5)
    assert m.max_tokens == 4096


def test_build_chat_model_accepts_explicit_max_tokens():
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key", temperature=0.5, max_tokens=512)
    assert m.max_tokens == 512
