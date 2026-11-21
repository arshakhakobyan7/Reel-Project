-- The Reel database schema version 1 (T-101 ... T-1012), taken from a real database on
-- 2026-11-21: the starting point of the migration test to version 2 (T-1013).

CREATE TABLE recordings (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
	source_path VARCHAR(1024) NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	file_modified_at DATETIME, 
	content_hash VARCHAR(64), 
	duplicate_of_id INTEGER, 
	group_name VARCHAR(200), 
	lesson_number INTEGER, 
	recording_date DATE, 
	identification JSON NOT NULL, 
	original_duration_seconds DOUBLE, 
	media_info JSON, 
	detected_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT ck_recordings_size_not_negative CHECK (size_bytes >= 0), 
	CONSTRAINT ck_recordings_lesson_not_negative CHECK (lesson_number IS NULL OR lesson_number >= 0), 
	CONSTRAINT ck_recordings_duration_not_negative CHECK (original_duration_seconds IS NULL OR original_duration_seconds >= 0), 
	CONSTRAINT uq_recordings_content_hash UNIQUE (content_hash), 
	CONSTRAINT fk_recordings_duplicate_of_id_recordings FOREIGN KEY(duplicate_of_id) REFERENCES recordings (id) ON DELETE SET NULL
);

CREATE TABLE staff_users (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	display_name VARCHAR(200), 
	password_hash VARCHAR(512) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	last_login_at DATETIME, 
	CONSTRAINT pk_staff_users PRIMARY KEY (id), 
	CONSTRAINT uq_staff_users_username UNIQUE (username)
);

CREATE TABLE schema_versions (
	version INTEGER NOT NULL, 
	applied_at DATETIME NOT NULL, 
	app_version VARCHAR(40) NOT NULL, 
	description TEXT NOT NULL, 
	CONSTRAINT pk_schema_versions PRIMARY KEY (version)
);

CREATE TABLE app_meta (
	"key" VARCHAR(64) NOT NULL, 
	value TEXT NOT NULL, 
	CONSTRAINT pk_app_meta PRIMARY KEY ("key")
);

CREATE TABLE seen_files (
	id INTEGER NOT NULL, 
	path VARCHAR(1024) NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	modified_at DATETIME NOT NULL, 
	first_seen_at DATETIME NOT NULL, 
	stable_since DATETIME NOT NULL, 
	last_checked_at DATETIME NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	recording_id INTEGER, 
	note TEXT, 
	CONSTRAINT pk_seen_files PRIMARY KEY (id), 
	CONSTRAINT ck_seen_files_size_not_negative CHECK (size_bytes >= 0), 
	CONSTRAINT uq_seen_files_path UNIQUE (path), 
	CONSTRAINT fk_seen_files_recording_id_recordings FOREIGN KEY(recording_id) REFERENCES recordings (id) ON DELETE SET NULL
);

CREATE TABLE jobs (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
	recording_id INTEGER NOT NULL, 
	stage VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at DATETIME, 
	lease_owner VARCHAR(200), 
	lease_expires_at DATETIME, 
	hold_reason VARCHAR(64), 
	hold_message TEXT, 
	last_error TEXT, 
	flags JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	version INTEGER NOT NULL, 
	CONSTRAINT ck_jobs_attempts_not_negative CHECK (attempts >= 0), 
	CONSTRAINT uq_jobs_recording_id UNIQUE (recording_id), 
	CONSTRAINT fk_jobs_recording_id_recordings FOREIGN KEY(recording_id) REFERENCES recordings (id) ON DELETE CASCADE
);

CREATE TABLE stage_runs (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	stage VARCHAR(32) NOT NULL, 
	attempt INTEGER NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	worker VARCHAR(200), 
	started_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	error TEXT, 
	metrics JSON NOT NULL, 
	CONSTRAINT pk_stage_runs PRIMARY KEY (id), 
	CONSTRAINT ck_stage_runs_attempt_positive CHECK (attempt >= 1), 
	CONSTRAINT fk_stage_runs_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE job_events (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	at DATETIME NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	actor_kind VARCHAR(32) NOT NULL, 
	actor_name VARCHAR(200), 
	stage VARCHAR(32), 
	message TEXT NOT NULL, 
	data JSON NOT NULL, 
	CONSTRAINT pk_job_events PRIMARY KEY (id), 
	CONSTRAINT fk_job_events_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE cut_lists (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	original_duration_seconds DOUBLE NOT NULL, 
	final_duration_seconds DOUBLE NOT NULL, 
	segments JSON NOT NULL, 
	parameters JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_cut_lists PRIMARY KEY (id), 
	CONSTRAINT uq_cut_lists_job_id_version UNIQUE (job_id, version), 
	CONSTRAINT ck_cut_lists_version_positive CHECK (version >= 1), 
	CONSTRAINT ck_cut_lists_durations_not_negative CHECK (original_duration_seconds >= 0 AND final_duration_seconds >= 0), 
	CONSTRAINT fk_cut_lists_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE video_metadata (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	summary TEXT NOT NULL, 
	description TEXT NOT NULL, 
	chapters JSON NOT NULL, 
	thumbnail_path VARCHAR(1024), 
	generator JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_video_metadata PRIMARY KEY (id), 
	CONSTRAINT uq_video_metadata_job_id UNIQUE (job_id), 
	CONSTRAINT fk_video_metadata_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE consent_checks (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	checked_at DATETIME NOT NULL, 
	provider VARCHAR(40) NOT NULL, 
	group_ref VARCHAR(200), 
	students JSON NOT NULL, 
	result VARCHAR(32) NOT NULL, 
	message TEXT, 
	released_by VARCHAR(200), 
	released_at DATETIME, 
	release_note TEXT, 
	CONSTRAINT pk_consent_checks PRIMARY KEY (id), 
	CONSTRAINT fk_consent_checks_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE publication_targets (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	target VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	dry_run BOOLEAN NOT NULL, 
	external_id VARCHAR(200), 
	url VARCHAR(1024), 
	details JSON NOT NULL, 
	attempts INTEGER NOT NULL, 
	last_error TEXT, 
	published_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_publication_targets PRIMARY KEY (id), 
	CONSTRAINT uq_publication_targets_job_id_target UNIQUE (job_id, target), 
	CONSTRAINT ck_publication_targets_attempts_not_negative CHECK (attempts >= 0), 
	CONSTRAINT fk_publication_targets_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE library_entries (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
	job_id INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	video_url VARCHAR(1024) NOT NULL, 
	group_name VARCHAR(200) NOT NULL, 
	lesson_number INTEGER, 
	recording_date DATE, 
	chapters JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT uq_library_entries_job_id UNIQUE (job_id), 
	CONSTRAINT fk_library_entries_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE transcripts (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	language VARCHAR(16) NOT NULL, 
	text TEXT NOT NULL, 
	segments JSON NOT NULL, 
	model VARCHAR(200), 
	cut_list_id INTEGER, 
	details JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_transcripts PRIMARY KEY (id), 
	CONSTRAINT uq_transcripts_job_id_kind UNIQUE (job_id, kind), 
	CONSTRAINT fk_transcripts_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE, 
	CONSTRAINT fk_transcripts_cut_list_id_cut_lists FOREIGN KEY(cut_list_id) REFERENCES cut_lists (id) ON DELETE SET NULL
);

CREATE INDEX ix_jobs_queue ON jobs (status, next_attempt_at);

CREATE INDEX ix_stage_runs_job_id ON stage_runs (job_id);

CREATE INDEX ix_job_events_job_id ON job_events (job_id);

CREATE INDEX ix_consent_checks_job_id ON consent_checks (job_id);
