from pathlib import Path


SCHEMA_SQL = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(
    encoding="utf-8"
)


def test_defect_analysis_prompt_uses_compact_email_structure():
    assert (
        "1) 가장 가까운 이전 분석 레포트, 2) 공통점 및 차이점, 3) 함의."
        in SCHEMA_SQL
    )
    assert "1위 - <정확한 레포트명>" in SCHEMA_SQL
    assert "서로 다른 레포트의 내용을 한 항목에 섞지 마세요" in SCHEMA_SQL
    assert "검색 문서에서 직접 확인되지 않은 원인을 사실처럼 단정하지 마세요" in SCHEMA_SQL


def test_schema_migrates_previous_prompt_without_rematching_new_prompt():
    assert (
        "instruction_template LIKE '%1) 이전 분석 레포트 요약, 2) 원리 (Mechanism)%'"
        in SCHEMA_SQL
    )
    assert "instruction_template LIKE '%공통점 및 차이점%'" not in SCHEMA_SQL
    assert "instruction_template LIKE '%분석 시 주의사항 및 함의%'" not in SCHEMA_SQL


def test_mail_rule_index_name_is_unchanged():
    assert "INDEX idx_ae_llm_agent_mail_rule_enabled_priority (enabled, priority)" in SCHEMA_SQL
