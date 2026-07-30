CREATE TABLE IF NOT EXISTS ae_llm_agent_api_profile (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    profile_key VARCHAR(100) NOT NULL,
    profile_name VARCHAR(200) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    base_url VARCHAR(1000) NULL,
    base_url_env_name VARCHAR(128) NULL,
    endpoint_path VARCHAR(1000) NOT NULL,
    http_method VARCHAR(10) NOT NULL DEFAULT 'POST',
    auth_header_name VARCHAR(100) NULL,
    auth_env_name VARCHAR(128) NULL,
    headers_json JSON NULL,
    request_template_json JSON NOT NULL,
    response_config_json JSON NOT NULL,
    connect_timeout_seconds INT NULL,
    read_timeout_seconds INT NULL,
    verify_ssl TINYINT(1) NULL,
    ca_bundle_env_name VARCHAR(128) NULL,
    description TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ae_llm_agent_api_profile_key (profile_key),
    INDEX idx_ae_llm_agent_api_profile_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ae_llm_agent_mail_rule (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    rule_key VARCHAR(100) NOT NULL,
    rule_name VARCHAR(200) NOT NULL,
    route_type VARCHAR(30) NOT NULL,
    route_case VARCHAR(100) NOT NULL,
    priority INT NOT NULL DEFAULT 100,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    rule_version INT NOT NULL DEFAULT 1,
    match_config_json JSON NOT NULL,
    action_config_json JSON NULL,
    description TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ae_llm_agent_mail_rule_key (rule_key),
    INDEX idx_ae_llm_agent_mail_rule_enabled_priority (enabled, priority),
    INDEX idx_ae_llm_agent_mail_rule_route (route_type, route_case)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ae_llm_agent_mail (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    uidl VARCHAR(255) NOT NULL,
    source_type VARCHAR(30) NOT NULL DEFAULT 'POP3',
    mailbox_key VARCHAR(255) NOT NULL DEFAULT 'default',
    message_id VARCHAR(1000) NULL,
    original_subject TEXT NOT NULL,
    request_title TEXT NULL,
    normalized_subject TEXT NULL,
    subject_hash CHAR(64) NULL,
    raw_hash CHAR(64) NULL,
    mail_body LONGTEXT NULL,
    sender_email VARCHAR(500) NULL,
    requester_user_id VARCHAR(128) NULL,
    reply_to_email VARCHAR(500) NULL,
    original_recipient_email VARCHAR(500) NULL,
    actual_recipient_email VARCHAR(500) NULL,
    recipient_mode VARCHAR(20) NULL,
    mail_sent_at DATETIME NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duplicate_of BIGINT NULL,
    route_type VARCHAR(30) NOT NULL DEFAULT 'UNCLASSIFIED',
    route_case VARCHAR(100) NULL,
    route_rule_id BIGINT NULL,
    route_rule_key VARCHAR(100) NULL,
    route_rule_version INT NULL,
    route_reason TEXT NULL,
    route_matches_json JSON NULL,
    route_action_json JSON NULL,
    classified_at DATETIME NULL,
    sharedworkspace_path VARCHAR(2000) NULL,
    attachment_count INT NULL,
    saved_at DATETIME NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'RECEIVED',
    retry_count INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    answer_text LONGTEXT NULL,
    search_results_json JSON NULL,
    chat_session_id VARCHAR(64) NULL,
    chat_turn_artifact_id VARCHAR(64) NULL,
    chat_search_log_id VARCHAR(64) NULL,
    send_status VARCHAR(30) NOT NULL DEFAULT 'NOT_READY',
    sent_mail_id VARCHAR(255) NULL,
    sent_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ae_llm_agent_mail_uidl (uidl),
    INDEX idx_ae_llm_agent_mail_status (status),
    INDEX idx_ae_llm_agent_mail_route_status (route_type, status),
    INDEX idx_ae_llm_agent_mail_rule (route_rule_id),
    INDEX idx_ae_llm_agent_mail_send_status (send_status),
    INDEX idx_ae_llm_agent_mail_subject_hash (subject_hash),
    INDEX idx_ae_llm_agent_mail_raw_hash (raw_hash),
    INDEX idx_ae_llm_agent_mail_duplicate_of (duplicate_of)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO ae_llm_agent_api_profile(
    profile_key, profile_name, enabled, base_url, base_url_env_name,
    endpoint_path, http_method, auth_header_name, auth_env_name,
    headers_json, request_template_json, response_config_json,
    connect_timeout_seconds, read_timeout_seconds, verify_ssl,
    ca_bundle_env_name, description
) VALUES(
    'defect-analysis',
    'Defect 형태/성분 분석 API',
    1,
    NULL,
    'REPORT_SEARCH_BASE_URL',
    '/internal/email-analysis',
    'POST',
    'X-Internal-Service-Key',
    'REPORT_SEARCH_SERVICE_KEY',
    JSON_OBJECT('Accept', 'application/json', 'Content-Type', 'application/json'),
    JSON_OBJECT(
        'request_id', '{{request_id}}',
        'requester_user_id', '{{requester_user_id}}',
        'requester_email', '{{requester_email}}',
        'request_title', '{{request_title}}',
        'mail_body', '{{mail_body}}'
    ),
    JSON_OBJECT(
        'status_field', 'status',
        'success_values', JSON_ARRAY('COMPLETED'),
        'answer_field', 'answer_text',
        'search_results_field', 'search_results',
        'trace_field', 'trace',
        'trace_mapping', JSON_OBJECT(
            'session_id', 'session_id',
            'turn_artifact_id', 'turn_artifact_id',
            'search_log_id', 'search_log_id'
        )
    ),
    10,
    180,
    NULL,
    'REPORT_SEARCH_CA_BUNDLE',
    '기존 report-search 내부 이메일 분석 API 프로필'
);

INSERT IGNORE INTO ae_llm_agent_mail_rule(
    rule_key, rule_name, route_type, route_case, priority, enabled,
    rule_version, match_config_json, action_config_json, description
) VALUES(
    'file_inline_fa_report_v1',
    'Inline FA Report 파일 저장',
    'FILE_ARCHIVE',
    'INLINE_FA_REPORT',
    100,
    1,
    1,
    JSON_OBJECT(
        'subject', JSON_OBJECT(
            'operator', 'contains_any',
            'values', JSON_ARRAY('inline fa report'),
            'prefix_len', 50
        ),
        'banned_before_match', JSON_ARRAY(
            '수신처','수신인','수선처','rcp','회신','(파일 권한)','fw','re','측정','의뢰','참고','회의','확인',
            '제안','정리','의견','요청','감사','일정','회의록','문의','내부공유','내부 공유','내부 선공유',
            '내부선공유','내용 수정','내용수정','선공유','부탁','fb','께','님','fib','tem','img','image',
            'pie','raw','t-v','v-t','planar','vertical','ct','demo','cut'
        )
    ),
    JSON_OBJECT(
        'save_root_subdir', 'inline_fa_report',
        'version_tag', 'ver3',
        'overwrite_policy', 'skip',
        'save_raw_separately', TRUE
    ),
    'sharedworkspace 파일 저장용 초기 규칙'
);

INSERT IGNORE INTO ae_llm_agent_mail_rule(
    rule_key, rule_name, route_type, route_case, priority, enabled,
    rule_version, match_config_json, action_config_json, description
) VALUES(
    'api_defect_analysis_v1',
    'Defect 형태/성분 분석 API',
    'API_ANALYSIS',
    'DEFECT_ANALYSIS_REQUEST',
    100,
    1,
    1,
    JSON_OBJECT(
        'subject', JSON_OBJECT(
            'operator', 'prefix_any',
            'values', JSON_ARRAY('[분석 대기] [Defect 형태/성분 분석의뢰]')
        )
    ),
    JSON_OBJECT(
        'api_profile', 'defect-analysis',
        'strip_subject_prefix', '[분석 대기] [Defect 형태/성분 분석의뢰]'
    ),
    'request-pipeline API 호출용 초기 규칙'
);