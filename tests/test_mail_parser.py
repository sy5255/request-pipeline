from request_pipeline.mail_parser import normalize_subject


def test_normalize_target_subject():
    prefix = "[분석 대기] [Defect 형태/성분 분석의뢰]"
    title, normalized, subject_hash = normalize_subject(
        f"{prefix}  KS A1   #19  iFA 의뢰 ",
        prefix,
    )
    assert title == "KS A1   #19  iFA 의뢰"
    assert normalized == "ks a1 #19 ifa 의뢰"
    assert len(subject_hash) == 64


def test_ignore_non_target_subject():
    assert normalize_subject("일반 메일", "[대상]") == (None, None, None)
