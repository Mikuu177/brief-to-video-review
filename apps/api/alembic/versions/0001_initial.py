"""Frozen initial domain schema; later ORM additions belong to later migrations."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Keep historical DDL independent of the evolving application metadata.
INITIAL_DDL = (
    """CREATE TABLE users (
	username VARCHAR(100) NOT NULL, 
	password_hash TEXT NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
)""",
    """CREATE TABLE login_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)""",
    """CREATE TABLE projects (
	name VARCHAR(200) NOT NULL, 
	brand VARCHAR(200) NOT NULL, 
	product VARCHAR(200) NOT NULL, 
	platform VARCHAR(30) NOT NULL, 
	phase VARCHAR(80) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	member_ids JSON NOT NULL, 
	mode VARCHAR(30) NOT NULL, 
	revision INTEGER NOT NULL, 
	current_ruleset_id VARCHAR(36), 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
)""",
    """CREATE TABLE assets (
	project_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	filename TEXT NOT NULL, 
	mime VARCHAR(100) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	bytes INTEGER NOT NULL, 
	storage_key TEXT NOT NULL, 
	metadata_json JSON NOT NULL, 
	parse_status VARCHAR(30) NOT NULL, 
	coverage JSON NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
)""",
    """CREATE TABLE rulesets (
	project_id VARCHAR(36) NOT NULL, 
	source_ids JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	revision INTEGER NOT NULL, 
	rules JSON NOT NULL, 
	conflicts JSON NOT NULL, 
	extraction_coverage JSON NOT NULL, 
	content_hash VARCHAR(64), 
	confirmed_by VARCHAR(36), 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	confirmation_note TEXT, 
	confirmation_kind VARCHAR(30), 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
)""",
    """CREATE TABLE source_blocks (
	asset_id VARCHAR(36) NOT NULL, 
	text TEXT NOT NULL, 
	position INTEGER NOT NULL, 
	locator JSON NOT NULL, 
	extraction_method VARCHAR(100) NOT NULL, 
	coverage_status VARCHAR(30) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id)
)""",
    """CREATE TABLE submissions (
	project_id VARCHAR(36) NOT NULL, 
	creator VARCHAR(200) NOT NULL, 
	lineage_id VARCHAR(36) NOT NULL, 
	parent_id VARCHAR(36), 
	version INTEGER NOT NULL, 
	script_id VARCHAR(36), 
	video_id VARCHAR(36), 
	cover_id VARCHAR(36), 
	caption_id VARCHAR(36), 
	pairing_confirmed BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(parent_id) REFERENCES submissions (id), 
	FOREIGN KEY(script_id) REFERENCES assets (id), 
	FOREIGN KEY(video_id) REFERENCES assets (id), 
	FOREIGN KEY(cover_id) REFERENCES assets (id), 
	FOREIGN KEY(caption_id) REFERENCES assets (id)
)""",
    """CREATE TABLE review_runs (
	project_id VARCHAR(36) NOT NULL, 
	submission_id VARCHAR(36), 
	ruleset_id VARCHAR(36), 
	kind VARCHAR(30) NOT NULL, 
	input_snapshot JSON NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(100) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	assessment VARCHAR(30) NOT NULL, 
	freshness VARCHAR(20) NOT NULL, 
	revision INTEGER NOT NULL, 
	result JSON NOT NULL, 
	error JSON, 
	cancel_requested BOOLEAN NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (project_id, idempotency_key), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(submission_id) REFERENCES submissions (id), 
	FOREIGN KEY(ruleset_id) REFERENCES rulesets (id)
)""",
    """CREATE TABLE review_stages (
	run_id VARCHAR(36) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	attempt INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	request_id TEXT, 
	output JSON, 
	error JSON, 
	usage JSON NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (run_id, name, attempt), 
	FOREIGN KEY(run_id) REFERENCES review_runs (id)
)""",
    """CREATE TABLE observations (
	run_id VARCHAR(36) NOT NULL, 
	asset_id VARCHAR(36) NOT NULL, 
	channel VARCHAR(30) NOT NULL, 
	content TEXT NOT NULL, 
	start_ms INTEGER, 
	end_ms INTEGER, 
	uncertainty_ms INTEGER, 
	source_block_id VARCHAR(36), 
	origin JSON NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES review_runs (id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id)
)""",
    """CREATE TABLE rule_checks (
	run_id VARCHAR(36) NOT NULL, 
	rule_id VARCHAR(100) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	evidence_ids JSON NOT NULL, 
	explanation TEXT NOT NULL, 
	suggestion TEXT NOT NULL, 
	applicability_reason TEXT NOT NULL, 
	limitations JSON NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (run_id, rule_id), 
	FOREIGN KEY(run_id) REFERENCES review_runs (id)
)""",
    """CREATE TABLE script_approvals (
	run_id VARCHAR(36) NOT NULL, 
	script_id VARCHAR(36) NOT NULL, 
	ruleset_id VARCHAR(36) NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	decision VARCHAR(20) NOT NULL, 
	reason TEXT NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES review_runs (id), 
	FOREIGN KEY(script_id) REFERENCES assets (id), 
	FOREIGN KEY(ruleset_id) REFERENCES rulesets (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id)
)""",
    """CREATE TABLE human_decisions (
	run_id VARCHAR(36) NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	decision VARCHAR(30) NOT NULL, 
	mode VARCHAR(30) NOT NULL, 
	reason TEXT NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES review_runs (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id)
)""",
)

INITIAL_TABLES = (
    "users",
    "login_sessions",
    "projects",
    "assets",
    "rulesets",
    "source_blocks",
    "submissions",
    "review_runs",
    "review_stages",
    "observations",
    "rule_checks",
    "script_approvals",
    "human_decisions",
)


def upgrade():
    for ddl in INITIAL_DDL:
        op.execute(ddl)


def downgrade():
    for name in reversed(INITIAL_TABLES):
        op.drop_table(name)
