from pathlib import Path


SCHEMA_SQL = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(
    encoding="utf-8"
)


def test_defect_analysis_prompt_uses_emoji_sections():
    assert "📌 가장 가까운 이전 분석 레포트 Top 3" in SCHEMA_SQL
    assert "🔍 공통점 및 차이점" in SCHEMA_SQL
    assert "💡 함의" in SCHEMA_SQL
    assert "각 순위에 정확한 레포트명과 해당 레포트에서 확인되는 핵심 내용을 함께 요약하세요" in SCHEMA_SQL


def test_comparison_is_not_restricted_to_one_line():
    assert "공통점과 차이점을 한 줄로 제한하지 말고 필요한 만큼 항목을 작성" in SCHEMA_SQL
    assert "과도한 중첩 목록은 사용하지 마세요" in SCHEMA_SQL


def test_schema_migrates_previous_prompt_without_rematching_new_prompt():
    assert (
        "instruction_template LIKE '%1) 이전 분석 레포트 요약, 2) 원리 (Mechanism)%'"
        in SCHEMA_SQL
    )
    assert (
        "instruction_template LIKE '%1) 가장 가까운 이전 분석 레포트, 2) 공통점 및 차이점, 3) 함의.%'"
        in SCHEMA_SQL
    )
    assert "instruction_template LIKE '%📌 가장 가까운 이전 분석 레포트 Top 3%'" not in SCHEMA_SQL


def test_mail_rule_index_name_is_unchanged():
    assert "INDEX idx_ae_llm_agent_mail_rule_enabled_priority (enabled, priority)" in SCHEMA_SQL
