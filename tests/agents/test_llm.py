from novelizer.agents.llm import build_chat_model


def test_build_chat_model_targets_given_model_and_endpoint():
    m = build_chat_model("my-model", "http://localhost:1234/v1", "key", temperature=0.5)
    # ChatOpenAI stores the model name and base URL; no network call is made here.
    assert m.model_name == "my-model"
    assert "1234" in str(m.openai_api_base)
